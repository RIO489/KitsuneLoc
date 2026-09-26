# -*- coding: utf-8 -*-
"""Вікно «Редактор перекладу»: увесь текст гри в програмі, без книг Excel.

Ліворуч — дерево: «Усі рядки», «Мої групи» (як у Crowdin: виділив рядки —
«Додати в групу…»), «За файлами гри» (розкладка колишніх книг). Посередині —
рядки. Праворуч — вибраний рядок: оригінал, переклад, однакові рядки
(переклад спільний, можна відв'язати), примітка, попередження, терміни
(клік — вставити переклад терміна) і прев'ю шрифтом гри.

Дані — project.py (Переклад\\<гра>\\переклад.json); зберігається само.
"""
import os, queue, threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from PIL import Image, ImageTk

import glossary, project, sheets

SAVE_DELAY = 1500           # мс після останньої правки — запис на диск
COMMIT_DELAY = 250          # мс після набору — переклад іде в рядок (і в однакові)
PREVIEW_W = 560             # ширина прев'ю в панелі, px
FILTERS = ('Усі', 'Неперекладені', 'Перекладені', 'Відв\'язані', 'З приміткою')


def colors(app):
    import importlib
    mod = importlib.import_module('__main__')
    themes = getattr(mod, 'THEMES', None) or {'light': dict(bg='#fafafa', fg='#1c1c1c', panel='#ffffff',
                                                            dim='#6a6a6a', warn='#8a6100', ok='#0a6b2e',
                                                            accent='#005fb8', link='#005fb8')}
    return themes.get(getattr(app, 'theme', 'light'), next(iter(themes.values())))


def no_full_redraw(win):
    """Windows: прибрати CS_HREDRAW|CS_VREDRAW у класів вікон Tk (TkTopLevel, TkChild).
    Із ними КОЖЕН крок зміни розміру вікна перемальовує всі віджети, навіть незмінні, —
    з темою sv-ttk це ~150 мс на крок, і вміст «доганяв» край вікна. Віджети Tk самі
    перемальовуються, коли міняється їхній розмір, тож повне перемальовування зайве.
    Стиль класу — на всі вікна програми; помилка — мовчки лишаємо як є."""
    if os.name != 'nt':
        return
    try:
        import ctypes
        u32 = ctypes.windll.user32
        get, set_ = u32.GetClassLongPtrW, u32.SetClassLongPtrW
        get.restype = set_.restype = ctypes.c_ssize_t
        get.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        u32.GetParent.restype = ctypes.c_void_p
        u32.GetParent.argtypes = [ctypes.c_void_p]
        win.update_idletasks()
        child = win.winfo_id()
        for h in (child, u32.GetParent(child)):
            st = get(h, -26)                                    # GCL_STYLE
            if st & 0x3:
                set_(h, -26, st & ~0x3)
    except Exception:
        return
    # стиль класу діє на ВСІ вікна програми (головне, «Терміни», «Написи»…): кожне
    # один раз перемальовуємо повністю, коли перестали тягнути край (інакше смуги
    # прокрутки, що з'їхали чи змінили висоту, лишають старі пікселі)
    root = win._root()
    if getattr(root, '_kl_redraw_bound', False):
        return
    root._kl_redraw_bound = True
    timers = {}

    def resized(ev):
        w = ev.widget
        if not isinstance(w, (tk.Tk, tk.Toplevel)):
            return
        if timers.get(w):
            root.after_cancel(timers[w])
        timers[w] = root.after(150, lambda: (timers.pop(w, None), w.winfo_exists() and full_redraw(w)))
    for cls in ('Tk', 'Toplevel'):
        root.bind_class(cls, '<Configure>', resized, add='+')


def full_redraw(win):
    """Один раз перемалювати все вікно (після підгонки вмісту під новий розмір):
    без CS_HREDRAW/CS_VREDRAW Windows оновлює лише нові смуги, і на місці, звідки
    з'їхала смуга прокрутки, лишались старі пікселі."""
    if os.name != 'nt':
        return
    try:
        import ctypes
        u32 = ctypes.windll.user32
        u32.GetParent.restype = ctypes.c_void_p
        u32.GetParent.argtypes = [ctypes.c_void_p]
        u32.RedrawWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
        h = u32.GetParent(win.winfo_id())
        # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW
        u32.RedrawWindow(h, None, None, 0x1 | 0x4 | 0x80 | 0x100)
    except Exception:
        pass


def one_line(s, n=200):
    s = (s or '').replace('\n', ' ⏎ ')
    return s if len(s) <= n else s[:n - 1] + '…'


class Editor(tk.Toplevel):
    def __init__(self, app, pr, backup_dir, goto=None):
        super().__init__(app)
        self.app, self.pr, self.bk = app, pr, backup_dir
        self.game = pr.game
        self.terms = glossary.load(pr.xl)
        self.approved = sheets.load_approved(pr.xl)
        self.ctx = None                          # межі ширини (sheets.limits) — у фоні
        self.fonts = None                        # шрифти гри для прев'ю ({'msg'|'adv': GameFont}) — у фоні
        self.cur = None                          # ключ вибраного рядка
        self.view = []                           # рядки в списку
        self.nodes = {}                          # iid дерева -> ('all'|'group'|'book'|'scene', …)
        self.node = 'all'
        self._loading = False
        self.pending = {}                        # що відкладено: commit / save / preview
        self.q = queue.Queue()
        c = self.c = colors(app)
        self.title(f'Редактор перекладу — {os.path.basename(pr.xl)}')
        self.geometry('1480x880')
        self.minsize(1100, 600)
        self.configure(bg=c['bg'])
        # увесь вміст — в одній рамці з явним розміром (place): поки тягнуть край вікна,
        # вона не змінюється (тема sv-ttk дорого перемальовує кожен віджет, що міняє розмір:
        # ~150 мс на крок — вміст «доганяв» край); підганяємо один раз, коли зупинились
        self.content = ttk.Frame(self)
        self.content.place(x=0, y=0, width=1480, height=880)
        self._build()
        no_full_redraw(self)
        self._fill_nodes()
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.after(50, self._poll)
        self.after(120, self._initial_panes)
        self.bind('<Configure>', self._win_resized)
        self._win_w = None
        threading.Thread(target=self._load_bg, daemon=True).start()
        self._show_node('all')
        if goto:
            self.goto(goto)
        self.app.editor_win = self

    # ============================================================ вигляд
    def _build(self):
        c = self.c
        st = ttk.Style()
        st.configure('Ed.Treeview', background=c.get('panel', '#ffffff'),
                     fieldbackground=c.get('panel', '#ffffff'), foreground=c.get('fg', '#1a1a1a'),
                     rowheight=24)
        st.map('Ed.Treeview', background=[('selected', c.get('accent', '#1f4f82'))],
               foreground=[('selected', '#ffffff')])

        top = ttk.Frame(self.content, padding=(10, 8, 10, 4))
        top.pack(fill='x')
        # кнопки праворуч пакуються першими — у вузькому вікні стискається пошук, а не вони
        ttk.Button(top, text='Завантажити з Excel…', command=self._from_excel).pack(side='right')
        ttk.Button(top, text='Вивантажити в Excel', command=self._to_excel).pack(side='right', padx=6)
        ttk.Button(top, text='Терміни…', command=self._terms_window).pack(side='right', padx=6)
        ttk.Label(top, text='Пошук:').pack(side='left')
        self.q_var = tk.StringVar()
        # не розтягується з вікном: у темі sv-ttk кожне розтягування поля — дороге перемальовування
        self.search = ttk.Entry(top, textvariable=self.q_var, width=28)
        self.search.pack(side='left', padx=6)
        self.q_var.trace_add('write', lambda *_: self._later('filter', 300, self._refill))
        ttk.Label(top, text='Показати:').pack(side='left', padx=(10, 4))
        self.flt = tk.StringVar(value=FILTERS[0])
        cb = ttk.Combobox(top, textvariable=self.flt, values=FILTERS, state='readonly', width=16)
        cb.pack(side='left')
        cb.bind('<<ComboboxSelected>>', lambda e: self._refill())
        # службові ключі гри (IDS_…, шляхи до моделей) не перекладають — у списку їх немає;
        # показуються лише тоді, коли на такий рядок веде попередження (goto)
        self.keys = tk.BooleanVar(value=False)

        body = self.body = ttk.Panedwindow(self.content, orient='horizontal')
        body.pack(fill='both', expand=True, padx=10, pady=4)

        # --- дерево груп ------------------------------------------------
        left = ttk.Frame(body)
        tf = ttk.Frame(left)
        tf.pack(side='top', fill='both', expand=True)
        self.tree = ttk.Treeview(tf, show='tree', columns=('pc',), selectmode='browse',
                                 style='Ed.Treeview')
        self.tree.column('#0', width=210)
        self.tree.column('pc', width=56, anchor='e', stretch=False)
        sb = ttk.Scrollbar(tf, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self._node_selected())
        self.tree.bind('<Button-3>', self._tree_menu)
        ttk.Button(left, text='Нова група з виділених…',
                   command=lambda: self._to_group(None)).pack(fill='x', pady=(6, 0))
        body.add(left, weight=0)

        # --- рядки --------------------------------------------------------
        mid = ttk.Frame(body)
        self.list = ttk.Treeview(mid, columns=('n', 'st', 'who', 'src', 'tr'), show='headings',
                                 selectmode='extended', style='Ed.Treeview')
        for col, text, w, stretch in (('n', '№', 44, False), ('st', '', 44, False),
                                      ('who', 'Хто', 100, False), ('src', 'Оригінал', 220, True),
                                      ('tr', 'Переклад', 220, True)):
            self.list.heading(col, text=text)
            self.list.column(col, width=w, stretch=stretch, anchor='e' if col == 'n' else 'w')
        sb2 = ttk.Scrollbar(mid, orient='vertical', command=self.list.yview)
        self.list.configure(yscrollcommand=sb2.set)
        sb2.pack(side='right', fill='y')
        self.list.pack(side='left', fill='both', expand=True)
        self.list.tag_configure('todo', foreground=c['dim'])
        self.list.tag_configure('solo', foreground=c.get('warn', '#8a6100'))
        self.list.bind('<<TreeviewSelect>>', lambda e: self._row_selected())
        self.list.bind('<Button-3>', self._list_menu)
        self.list.bind('<Return>', lambda e: (self.tr_text.focus_set(), 'break')[1])
        self.list.bind('<Double-1>', lambda e: self.tr_text.focus_set())
        body.add(mid, weight=1)          # ширина вікна змінюється — тягнеться лише список

        # --- панель рядка -------------------------------------------------
        right = self.right = ttk.Frame(body, padding=(10, 0, 0, 0))
        body.add(right, weight=0)
        right.bind('<Configure>', self._right_resized)
        self._right_w = 0
        self.head = tk.StringVar()
        ttk.Label(right, textvariable=self.head, font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        self.where = tk.StringVar()
        self.where_lbl = ttk.Label(right, textvariable=self.where, style='Hint.TLabel',
                                   wraplength=PREVIEW_W, justify='left')
        self.where_lbl.pack(anchor='w')

        ttk.Label(right, text='Оригінал').pack(anchor='w', pady=(6, 0))
        self.src_text = self._text(right, 4, readonly=True)
        self.ja_lbl = ttk.Label(right, text='Японська')
        self.ja_text = self._text(right, 2, readonly=True, pack=False)

        tl = ttk.Frame(right)
        tl.pack(fill='x', pady=(6, 0))
        ttk.Label(tl, text='Переклад').pack(side='left')
        ttk.Label(tl, text='Enter — далі · Shift+Enter — новий рядок · Ctrl+↑/↓ — сусідній рядок',
                  style='Hint.TLabel').pack(side='right')
        self.tr_text = self._text(right, 5)
        self.tr_text.configure(undo=True)
        self.tr_text.bind('<<Modified>>', self._modified)
        self.tr_text.bind('<Return>', self._enter)
        self.tr_text.bind('<Shift-Return>', self._newline)
        self.tr_text.bind('<Alt-Return>', self._newline)
        self.tr_text.bind('<Control-Down>', lambda e: self._step(1))
        self.tr_text.bind('<Control-Up>', lambda e: self._step(-1))
        self.bind('<Control-f>', lambda e: (self.search.focus_set(), 'break')[1])

        lk = ttk.Frame(right)
        lk.pack(fill='x', pady=(4, 0))
        self.link_info = tk.StringVar()
        ttk.Label(lk, textvariable=self.link_info, style='Hint.TLabel').pack(side='left')
        self.b_link = ttk.Button(lk, text='', command=self._toggle_link)
        self.b_link.pack(side='right')

        nf = ttk.Frame(right)
        nf.pack(fill='x', pady=(6, 0))
        ttk.Label(nf, text='Примітка:').pack(side='left')
        self.note = tk.StringVar()
        ttk.Entry(nf, textvariable=self.note).pack(side='left', fill='x', expand=True, padx=6)
        self.note.trace_add('write', lambda *_: self._note_changed())

        self.warn = tk.StringVar()
        self.warn_lbl = ttk.Label(right, textvariable=self.warn, foreground=c.get('warn', '#8a6100'),
                                  wraplength=PREVIEW_W, justify='left')
        self.warn_lbl.pack(anchor='w', pady=(6, 0))

        ttk.Label(right, text='Терміни в рядку (клік — вставити переклад)').pack(anchor='w', pady=(8, 0))
        self.terms_box = ttk.Frame(right)
        self.terms_box.pack(fill='x')

        self.pic_title = tk.StringVar(value='Як у грі')
        ttk.Label(right, textvariable=self.pic_title).pack(anchor='w', pady=(8, 0))
        self.pic_box = ttk.Frame(right)
        self.pic_box.pack(anchor='w', fill='x')
        self.pic = tk.Label(self.pic_box, bg=c['bg'], bd=0, anchor='nw', justify='left',
                            fg=c['dim'], text='…')
        self.pic.pack(anchor='w')
        self.pic_photos = []
        self.pic_extra = []                      # (підпис, картинка) для інших екранів діалогу

        bot = ttk.Frame(self.content, padding=(10, 2, 10, 8))
        bot.pack(fill='x')
        self.status = tk.StringVar(value='')
        ttk.Label(bot, textvariable=self.status, style='Hint.TLabel').pack(side='left')
        self.count = tk.StringVar()
        ttk.Label(bot, textvariable=self.count).pack(side='right')

    def _initial_panes(self):
        """Початкові ширини: дерево ~280 px, панель рядка ~620 px, решта — список."""
        try:
            w = self.body.winfo_width()
            self.body.sashpos(0, 280)
            self.body.sashpos(1, max(700, w - 640))
        except tk.TclError:
            pass

    def _win_resized(self, ev):
        """Вікно змінило розмір: вміст підганяємо, коли перестали тягнути край."""
        if ev.widget is not self or (ev.width, ev.height) == self._win_w:
            return
        first = not self._win_w
        self._win_w = (ev.width, ev.height)
        self._later('fit', 0 if first else 120, self._fit_panes)

    def _fit_panes(self):
        w, h = self._win_w
        self.content.place_configure(width=w, height=h)
        self.content.update_idletasks()
        # списку лишилось замало — звузити панель рядка (вона сама ширини не міняє)
        try:
            left, mid = self.body.sashpos(0), self.body.sashpos(1)
            total = self.body.winfo_width()
            need = 420                                   # найвужчий список, з яким ще можна працювати
            if mid - left < need:
                self.body.sashpos(1, min(total - 380, left + need))
        except tk.TclError:
            pass
        self.update_idletasks()
        full_redraw(self)

    def _right_resized(self, ev):
        """Панель рядка змінила ширину (вікно чи роздільник): переноси підписів
        і прев'ю — під нову ширину (прев'ю — коли перестали тягнути)."""
        w = ev.width
        if abs(w - self._right_w) < 4:
            return
        self._right_w = w
        wrap = max(200, w - 24)
        self.warn_lbl.configure(wraplength=wrap)
        self.where_lbl.configure(wraplength=wrap)
        self._later('preview', 200, self._preview)

    def _text(self, parent, h, readonly=False, pack=True):
        c = self.c
        # width=20: поле не вимагає ширини (80 символів за замовчуванням), а заповнює панель
        t = tk.Text(parent, height=h, width=20, wrap='word', font=('Segoe UI', 11), relief='flat',
                    borderwidth=6, bg=c.get('panel', '#ffffff'), fg=c.get('fg', '#1a1a1a'),
                    insertbackground=c.get('fg', '#1a1a1a'))
        if readonly:
            t.configure(state='disabled', bg=c['bg'])
        if pack:
            t.pack(fill='x')
        return t

    @staticmethod
    def _set_text(t, s):
        ro = str(t.cget('state')) == 'disabled'
        if ro:
            t.configure(state='normal')
        t.delete('1.0', 'end')
        t.insert('1.0', s or '')
        if ro:
            t.configure(state='disabled')

    # ============================================================ фон
    def _load_bg(self):
        """Межі ширини й шрифт гри для прев'ю — кілька секунд, не блокуємо вікно."""
        try:
            docs = [d for _p, d in self.pr.docs.values()]
            ctx = sheets.limits(docs, self.bk, os.path.dirname(os.path.abspath(self.pr.work)))
            self.q.put(('ctx', ctx))
        except Exception as ex:                                   # noqa: BLE001
            self.q.put(('status', f'Межі ширини не пораховано: {ex}'))
        try:
            import preview
            fonts = {'msg': preview.GameFont(self.game, self.bk)}
            if self.game == 'nep':
                fonts['adv'] = preview.GameFont(self.game, self.bk, 'adv')
            self.q.put(('font', fonts))
        except Exception as ex:                                   # noqa: BLE001
            self.q.put(('status', f'Прев\'ю недоступне: {ex}'))

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                what, val = self.q.get_nowait()
                if what == 'ctx':
                    self.ctx = val
                    self._show_row(keep_text=True)
                elif what == 'font':
                    self.fonts = val
                    self._later('preview', 0, self._preview)
                elif what == 'status':
                    self.status.set(val)
                elif what == 'call':
                    val()
        except queue.Empty:
            pass
        self.after(50, self._poll)

    def _later(self, name, ms, fn):
        if self.pending.get(name):
            self.after_cancel(self.pending[name])
        self.pending[name] = self.after(ms, lambda: (self.pending.pop(name, None), fn()))

    # ============================================================ дерево
    def _pc(self, rows):
        rows = [r for r in rows if r['kind'] != 'key']
        if not rows:
            return ''
        d = sum(1 for r in rows if r['e'].get('tr'))
        return '✓' if d == len(rows) else f'{100 * d // len(rows)}%'

    def _fill_nodes(self):
        sel = self.node
        t = self.tree
        t.delete(*t.get_children())
        self.nodes = {}
        books = self.pr.books()
        all_rows = [r for sc in books.values() for rs in sc.values() for r in rs]
        t.insert('', 'end', iid='all', text='Усі рядки', values=(self._pc(all_rows),), open=True)
        self.nodes['all'] = ('all', None)
        t.insert('', 'end', iid='mine', text='Мої групи', open=True)
        self.nodes['mine'] = ('mine', None)
        for i, g in enumerate(self.pr.groups()):
            iid = f'g{i}'
            t.insert('mine', 'end', iid=iid, text=g['назва'],
                     values=(self._pc(self.pr.group_rows(g['назва'])),))
            self.nodes[iid] = ('group', g['назва'])
        t.insert('', 'end', iid='files', text='За файлами гри', open=True)
        self.nodes['files'] = ('files', None)
        for bi, (book, scenes) in enumerate(books.items()):
            biid = f'b{bi}'
            rows = [r for rs in scenes.values() for r in rs]
            t.insert('files', 'end', iid=biid, text=book, values=(self._pc(rows),))
            self.nodes[biid] = ('book', book)
            if len(scenes) > 1:
                for si, (scene, rs) in enumerate(scenes.items()):
                    siid = f'b{bi}s{si}'
                    t.insert(biid, 'end', iid=siid, text=scene or '—', values=(self._pc(rs),))
                    self.nodes[siid] = ('scene', (book, scene))
        if sel not in self.nodes:
            sel = 'all'
        self._quiet_select(sel)

    def _refresh_pcs(self):
        """Лише відсотки в дереві (після правок) — без перебудови."""
        books = self.pr.books()
        for iid, (kind, val) in self.nodes.items():
            if kind == 'all':
                rows = [r for sc in books.values() for rs in sc.values() for r in rs]
            elif kind == 'group':
                rows = self.pr.group_rows(val)
            elif kind == 'book':
                rows = [r for rs in books.get(val, {}).values() for r in rs]
            elif kind == 'scene':
                rows = books.get(val[0], {}).get(val[1], [])
            else:
                continue
            self.tree.set(iid, 'pc', self._pc(rows))

    def _quiet_select(self, iid):
        self._node_lock = True
        self.tree.selection_set(iid)
        self.tree.see(iid)
        self._node_lock = False
        self.node = iid

    def _node_selected(self):
        s = self.tree.selection()
        if not s or getattr(self, '_node_lock', False):
            return
        if self.nodes.get(s[0], ('',))[0] in ('mine', 'files') or s[0] == self.node:
            return                      # той самий вузол (подія вибору приходить із запізненням)
        self._show_node(s[0])

    def _node_rows(self, iid):
        kind, val = self.nodes.get(iid, ('all', None))
        if kind == 'group':
            return self.pr.group_rows(val)
        books = self.pr.books()
        if kind == 'book':
            return [r for rs in books.get(val, {}).values() for r in rs]
        if kind == 'scene':
            return books.get(val[0], {}).get(val[1], [])
        return [r for sc in books.values() for rs in sc.values() for r in rs]

    def _show_node(self, iid):
        self.node = iid
        self._refill()

    # ============================================================ список
    def _refill(self, keep=True):
        rows = self._node_rows(self.node)
        if not self.keys.get():
            rows = [r for r in rows if r['kind'] != 'key']
        f = self.flt.get()
        if f == 'Неперекладені':
            rows = [r for r in rows if not r['e'].get('tr')]
        elif f == 'Перекладені':
            rows = [r for r in rows if r['e'].get('tr')]
        elif f == 'Відв\'язані':
            rows = [r for r in rows if r.get('окремо')]
        elif f == 'З приміткою':
            rows = [r for r in rows if r['e'].get('note')]
        q = self.q_var.get().strip().lower()
        if q:
            rows = [r for r in rows if q in r['e']['src'].lower()
                    or q in r['e'].get('tr', '').lower() or q in (r['who'] or '').lower()]
        self.view = rows
        self.list.delete(*self.list.get_children())
        done = sum(1 for r in rows if r['e'].get('tr'))
        self.count.set(f'рядків: {len(rows)} · перекладено: {done}')
        if not rows:
            self.cur = None
            self._show_row()
            return
        target = None
        if keep and self.cur and any(r['k'] == self.cur for r in rows):
            target = str(self.pr.by_key[self.cur]['n'])
        # десятки тисяч рядків (MSK «Усі рядки» — 34 тис.) вставляємо порціями: вікно
        # готове одразу, решта списку доповнюється за частки секунди
        self._fill_gen = getattr(self, '_fill_gen', 0) + 1
        self._fill_chunk(self._fill_gen, rows, 0, target or str(rows[0]['n']))

    FIRST_CHUNK, CHUNK = 400, 2500

    def _fill_chunk(self, gen, rows, start, target):
        if gen != self._fill_gen or not self.winfo_exists():
            return                      # тим часом вибрали інший вузол / фільтр
        end = min(len(rows), start + (self.FIRST_CHUNK if start == 0 else self.CHUNK))
        for i in range(start, end):
            r = rows[i]
            self.list.insert('', 'end', iid=str(r['n']), values=self._values(r, i + 1), tags=self._tags(r))
        if target and self.list.exists(target):
            self.list.selection_set(target)
            self.list.see(target)
            target = None
        if end < len(rows):
            self.after(1, lambda: self._fill_chunk(gen, rows, end, target))

    def _values(self, r, i):
        twins = len(self.pr.twins(r['k']))
        st = '✂' if r.get('окремо') else (f'×{twins}' if twins > 1 else '')
        who = 'ім\'я' if r['kind'] == 'name' else ('ключ' if r['kind'] == 'key' else self.pr.speaker(r))
        return (i, st, who, one_line(r['e']['src']), one_line(r['e'].get('tr', '')))

    def _tags(self, r):
        if r.get('окремо'):
            return ('solo',)
        return () if r['e'].get('tr') else ('todo',)

    def _update_rows(self, keys):
        """Оновити в списку рядки, чий переклад змінився (однакові теж)."""
        pos = {str(r['n']): i for i, r in enumerate(self.view, 1)}
        for k in keys:
            r = self.pr.by_key[k]
            iid = str(r['n'])
            if iid in pos and self.list.exists(iid):       # список ще може доповнюватися
                self.list.item(iid, values=self._values(r, pos[iid]), tags=self._tags(r))

    def selected_keys(self):
        return [self.pr.rows[int(i)]['k'] for i in self.list.selection()]

    def _row_selected(self):
        s = self.list.selection()
        if not s:
            return
        k = self.pr.rows[int(s[-1] if len(s) == 1 else self.list.focus() or s[-1])]['k']
        if k == self.cur:
            return
        self._commit()
        self.cur = k
        self._show_row()

    def _step(self, d):
        self._commit()
        items = self.list.get_children()
        if not items:
            return 'break'
        iid = str(self.pr.by_key[self.cur]['n']) if self.cur else items[0]
        k = items.index(iid) if iid in items else -1
        nxt = items[max(0, min(len(items) - 1, k + d))]
        self.list.selection_set(nxt)
        self.list.focus(nxt)
        self.list.see(nxt)
        self.after(1, lambda: (self.tr_text.focus_set(), self.tr_text.mark_set('insert', 'end-1c')))
        return 'break'

    def goto(self, key):
        """Відкрити рядок (з попередження в головному вікні)."""
        if key not in self.pr.by_key:
            return False
        self._quiet_select('all')
        self.flt.set(FILTERS[0])
        self.q_var.set('')
        if self.pr.by_key[key]['kind'] == 'key':
            self.keys.set(True)
        self.cur = key
        self._refill()
        self.tr_text.focus_set()
        self.lift()
        return True

    # ============================================================ панель рядка
    def _show_row(self, keep_text=False):
        k = self.cur
        self._loading = True
        try:
            if not k:
                self.head.set('')
                self.where.set('')
                self._set_text(self.src_text, '')
                if not keep_text:
                    self._set_text(self.tr_text, '')
                self.note.set('')
                self.warn.set('')
                self.link_info.set('')
                self.b_link.pack_forget()
                return
            r = self.pr.by_key[k]
            e = r['e']
            who = self.pr.speaker(r)
            kind = {'name': 'ім\'я мовця', 'key': 'службовий ключ — не перекладати'}.get(r['kind'])
            self.head.set(kind or (who or r['scene']))
            self.where.set(f'{r["book"]} · {r["scene"]} · {r["source"]} [{e["id"]}]')
            self._set_text(self.src_text, e['src'])
            if e.get('ja'):
                self._set_text(self.ja_text, e['ja'])
                if not getattr(self, '_ja_shown', False):
                    self.ja_lbl.pack(anchor='w', pady=(4, 0), after=self.src_text)
                    self.ja_text.pack(fill='x', after=self.ja_lbl)
                    self._ja_shown = True
            elif getattr(self, '_ja_shown', False):
                self.ja_lbl.pack_forget()
                self.ja_text.pack_forget()
                self._ja_shown = False
            if not keep_text:
                self._set_text(self.tr_text, e.get('tr', ''))
                self.tr_text.edit_reset()
                self.tr_text.edit_modified(False)
            self.note.set(e.get('note', ''))
            self._link_state()
            self._checks()
            self._fill_terms()
        finally:
            self._loading = False
        self._later('preview', 80, self._preview)

    def _link_state(self):
        k = self.cur
        r = self.pr.by_key[k]
        twins = len(self.pr.twins(k))
        if twins <= 1:
            self.link_info.set('Цей рядок у грі один.')
            self.b_link.pack_forget()
            return
        self.b_link.pack(side='right')
        if r.get('окремо'):
            self.link_info.set(f'Відв\'язано: такий самий оригінал ще в {twins - 1} місцях, '
                               'а тут — свій переклад.')
            self.b_link.configure(text='Прив\'язати до однакових')
        else:
            n = len(self.pr.linked(k))
            self.link_info.set(f'Такий самий оригінал ще в {twins - 1} місцях — переклад спільний '
                               f'({n} пов\'язаних).')
            self.b_link.configure(text='Відв\'язати тут')

    def _toggle_link(self):
        k = self.cur
        if not k:
            return
        self._commit()
        if self.pr.by_key[k].get('окремо'):
            changed = self.pr.attach([k])
            self.status.set('Прив\'язано: переклад знову спільний з однаковими рядками.')
        else:
            self.pr.detach([k])
            changed = []
            self.status.set('Відв\'язано: тепер у цього рядка свій переклад.')
        self._update_rows(self.pr.twins(k) + changed)
        self._show_row()
        self._later('save', SAVE_DELAY, self._save)

    def _checks(self):
        r = self.pr.by_key[self.cur]
        if self.ctx is None or not r['e'].get('tr'):
            self.warn.set('')
            return
        doc = self.pr.docs[r['source']][1]
        msgs = [m for m in sheets.check_entry(doc, r['e'], self.ctx, self.terms)
                if self.approved.get(sheets.approval_key(r['source'], r['e']['id'], m)) != r['e'].get('tr')]
        self.warn.set('\n'.join('⚠ ' + m for m in msgs))

    def _fill_terms(self):
        for w in self.terms_box.winfo_children():
            w.destroy()
        r = self.pr.by_key[self.cur]
        found = [t for t in glossary.found(r['e']['src'], self.terms) if t.get('ua')]
        if not found:
            ttk.Label(self.terms_box, text='—', style='Hint.TLabel').pack(anchor='w')
            return
        for t in found[:12]:
            row = ttk.Frame(self.terms_box)
            row.pack(anchor='w', fill='x')
            ttk.Label(row, text=f'{t["en"]} →').pack(side='left')
            for v in [v.strip() for v in t['ua'].split('/') if v.strip()]:
                b = tk.Label(row, text=v, fg=self.c.get('link', '#005fb8'), bg=self.c['bg'],
                             cursor='hand2', font=('Segoe UI', 10, 'underline'))
                b.pack(side='left', padx=4)
                b.bind('<Button-1>', lambda e, v=v: self._insert(v))
            if t.get('примітка'):
                ttk.Label(row, text=f'({t["примітка"]})', style='Hint.TLabel').pack(side='left')

    def _insert(self, s):
        self.tr_text.insert('insert', s)
        self.tr_text.focus_set()

    # ============================================================ правка
    def _modified(self, _e=None):
        if not self.tr_text.edit_modified():
            return
        self.tr_text.edit_modified(False)
        if self._loading:
            return
        self._later('commit', COMMIT_DELAY, self._commit)
        self._later('preview', 300, self._preview)

    def _enter(self, _e):
        return self._step(1)

    def _newline(self, _e):
        self.tr_text.insert('insert', '\n')
        return 'break'

    def _commit(self):
        """Переклад із поля — у рядок (і в пов'язані однакові)."""
        if self.pending.get('commit'):
            self.after_cancel(self.pending.pop('commit'))
        k = self.cur
        if not k or self._loading:
            return
        text = self.tr_text.get('1.0', 'end-1c')
        if text.strip() == self.pr.tr(k):
            return
        changed = self.pr.set_tr(k, text)
        if changed:
            self._update_rows(changed)
            n = len(changed)
            self.status.set(f'Переклад записано{f" (разом з однаковими: {n})" if n > 1 else ""} — '
                            'зберігаю…')
            self._checks()
            self._later('save', SAVE_DELAY, self._save)

    def _note_changed(self):
        if self._loading or not self.cur:
            return
        if self.pr.set_note(self.cur, self.note.get()):
            self._later('save', SAVE_DELAY, self._save)

    def _save(self):
        try:
            self.pr.save()
        except OSError as ex:
            self.status.set(f'Не вдалося зберегти: {ex}')
            return
        self.status.set('Збережено.')
        self._later('pcs', 200, self._refresh_pcs)

    def flush(self):
        """Записати все негайно (перед «2» чи закриттям)."""
        self._commit()
        if self.pending.get('save'):
            self.after_cancel(self.pending.pop('save'))
        if self.pr.pending():
            self._save()

    # ============================================================ прев'ю
    def _pics(self, items):
        """Показати [(підпис | None, Image | None, текст)] — один чи кілька екранів."""
        self.pic_photos = []
        for w in self.pic_extra:
            w.destroy()
        self.pic_extra = []
        first = True
        for cap, im, text in items:
            photo = ImageTk.PhotoImage(im) if im is not None else None
            if photo:
                self.pic_photos.append(photo)
            if first:
                if cap:
                    lab = ttk.Label(self.pic_box, text=cap, style='Hint.TLabel')
                    lab.pack(anchor='w', before=self.pic)
                    self.pic_extra.append(lab)
                self.pic.configure(image=photo or '', text=text)
                first = False
                continue
            lab = ttk.Label(self.pic_box, text=cap or '', style='Hint.TLabel')
            lab.pack(anchor='w', pady=(6, 0))
            img = tk.Label(self.pic_box, bg=self.c['bg'], bd=0, image=photo or '', text=text)
            img.pack(anchor='w')
            self.pic_extra += [lab, img]

    def _preview(self):
        k = self.cur
        if not k:
            self._pics([(None, None, '')])
            return
        r = self.pr.by_key[k]
        e = r['e']
        if e.get('preview') and os.path.exists(e['preview']):
            self._pics([(None, Image.open(e['preview']), '')])
            return
        if self.fonts is None or self.ctx is None or not self.ctx.get('wtab'):
            self._pics([(None, None, 'прев\'ю готується…' if self.fonts is None or self.ctx is None
                         else 'для цієї гри прев\'ю немає')])
            return
        import preview
        text = self.tr_text.get('1.0', 'end-1c').strip()
        self.pic_title.set('Як у грі' if text else 'Як у грі (поки що оригінал — перекладу ще немає)')
        text = text or e['src']
        doc = self.pr.docs[r['source']][1]
        dialog = sheets._width_group(doc, e) == ('діалог',)
        name = self.pr.speaker(r) if dialog else None
        screens = self.ctx.get('screens') if dialog else None
        width = max(200, min(PREVIEW_W, self.pic_box.winfo_width() - 8))
        items = []
        for sc in screens or [None]:
            font = self.fonts.get(sc['font'] if sc else 'msg')
            if font is None:
                continue
            lim, lines, _w = sheets.width_limit(doc, e, self.ctx, sc)
            try:
                im, _notes = preview.render(font, text, lim, lines, name, dialog)
            except Exception as ex:                               # noqa: BLE001
                items.append((None, None, f'прев\'ю не вдалося: {ex}'))
                continue
            cap = sc['назва'].capitalize() if sc and len(screens) > 1 else None
            items.append((cap, preview.fit(im, width), ''))
        self._pics(items or [(None, None, '')])

    # ============================================================ групи
    def _list_menu(self, ev):
        iid = self.list.identify_row(ev.y)
        if iid and iid not in self.list.selection():
            self.list.selection_set(iid)
        keys = self.selected_keys()
        if not keys:
            return
        m = tk.Menu(self, tearoff=0)
        add = tk.Menu(m, tearoff=0)
        for g in self.pr.groups():
            add.add_command(label=g['назва'], command=lambda n=g['назва']: self._to_group(n))
        if self.pr.groups():
            add.add_separator()
        add.add_command(label='Нова група…', command=lambda: self._to_group(None))
        m.add_cascade(label=f'Додати в групу ({len(keys)})', menu=add)
        kind, val = self.nodes.get(self.node, ('', None))
        if kind == 'group':
            m.add_command(label=f'Прибрати з групи «{val}»',
                          command=lambda: self._from_group(val, keys))
        m.add_separator()
        m.add_command(label='Відв\'язати від однакових', command=lambda: self._detach(keys))
        m.add_command(label='Прив\'язати до однакових', command=lambda: self._attach(keys))
        m.add_separator()
        m.add_command(label='Вставити з буфера в переклад', command=lambda: self._paste(keys))
        m.add_command(label='Скопіювати оригінал у переклад', command=lambda: self._copy_src(keys))
        m.add_command(label='Очистити переклад', command=lambda: self._clear(keys))
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()

    def _to_group(self, name):
        self._commit()
        keys = self.selected_keys()
        if not keys:
            messagebox.showinfo('Групи', 'Спершу виділи рядки в списку (Shift чи Ctrl + клік).',
                                parent=self)
            return
        if name is None:
            name = simpledialog.askstring('Нова група', 'Назва групи (напр. «Розділ 1 — Сцена 1»):',
                                          parent=self)
            if not name or not name.strip():
                return
            name = name.strip()
        self.pr.add_to_group(name, keys)
        self._save()
        self._fill_nodes()
        self._quiet_select(self.node)
        self.status.set(f'Додано в групу «{name}»: {len(keys)} рядків.')

    def _from_group(self, name, keys):
        self.pr.remove_from_group(name, keys)
        self._save()
        self._fill_nodes()
        self._refill(keep=False)

    def _tree_menu(self, ev):
        iid = self.tree.identify_row(ev.y)
        kind, val = self.nodes.get(iid, ('', None))
        m = tk.Menu(self, tearoff=0)
        if kind == 'group':
            m.add_command(label='Перейменувати…', command=lambda: self._rename(val))
            m.add_command(label='Видалити групу', command=lambda: self._delete(val))
        elif kind == 'mine':
            m.add_command(label='Нова група з виділених…', command=lambda: self._to_group(None))
        else:
            return
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()

    def _rename(self, old):
        new = simpledialog.askstring('Перейменувати групу', 'Нова назва:', initialvalue=old, parent=self)
        if not new or not new.strip() or new.strip() == old:
            return
        if self.pr.group(new.strip()):
            messagebox.showwarning('Групи', 'Група з такою назвою вже є.', parent=self)
            return
        self.pr.rename_group(old, new.strip())
        self._save()
        self._fill_nodes()

    def _delete(self, name):
        if not messagebox.askyesno('Видалити групу',
                                   f'Видалити групу «{name}»?\nРядки й переклад лишаться — '
                                   'зникне лише сама група.', parent=self):
            return
        self.pr.delete_group(name)
        self._save()
        self.node = 'all'
        self._fill_nodes()
        self._refill()

    def _detach(self, keys):
        self._commit()
        self.pr.detach(keys)
        self._after_bulk(keys, 'Відв\'язано')

    def _attach(self, keys):
        self._commit()
        changed = self.pr.attach(keys)
        self._after_bulk(keys + changed, 'Прив\'язано')

    def _copy_src(self, keys):
        self._commit()
        changed = []
        for k in keys:
            changed += self.pr.set_tr(k, self.pr.by_key[k]['e']['src'])
        self._after_bulk(changed, 'Скопійовано оригінал')

    def _paste(self, keys):
        """Текст з буфера обміну — перекладом виділених рядків."""
        try:
            text = self.clipboard_get()
        except tk.TclError:
            text = ''
        text = text.replace('\r\n', '\n').strip('\n')
        if not text:
            messagebox.showinfo('Вставити', 'У буфері обміну немає тексту.', parent=self)
            return
        self._commit()
        n = sum(len(self.pr.linked(k)) for k in keys)
        if n > 1 and not messagebox.askyesno(
                'Вставити', f'Той самий текст стане перекладом {n} рядків. Далі?', parent=self):
            return
        changed = []
        for k in keys:
            changed += self.pr.set_tr(k, text)
        self._after_bulk(changed, 'Вставлено')

    def reload_terms(self):
        """Глосарій чи жанр змінили у вікні «Терміни…»."""
        self.terms = glossary.load(self.pr.xl)
        if self.cur:
            self._fill_terms()

    def _clear(self, keys):
        self._commit()
        n = sum(len(self.pr.linked(k)) for k in keys)
        if n > len(keys) and not messagebox.askyesno(
                'Очистити переклад', f'Разом з однаковими рядками очиститься {n} перекладів. Далі?',
                parent=self):
            return
        changed = []
        for k in keys:
            changed += self.pr.set_tr(k, '')
        self._after_bulk(changed, 'Очищено')

    def _after_bulk(self, keys, what):
        tw = set(keys)
        for k in list(tw):
            tw.update(self.pr.twins(k))
        self._update_rows(tw)
        self._show_row()
        self._save()
        self.status.set(f'{what}: {len(set(keys))} рядків.')

    # ============================================================ Excel
    def _to_excel(self):
        self.flush()
        self.status.set('Вивантажую книги Excel…')

        def job():
            try:
                self.pr.export_excel()
                msg = f'Книги Excel вивантажено в {self.pr.xl} — це копія: правки в них ' \
                      'потрапляють у програму лише через «Завантажити з Excel…».'
            except Exception as ex:                               # noqa: BLE001
                msg = f'Не вдалося вивантажити: {ex}'
            self.q.put(('status', msg))
        threading.Thread(target=job, daemon=True).start()

    def _from_excel(self):
        busy = [f for f in os.listdir(self.pr.xl) if f.startswith('~$')] if os.path.isdir(self.pr.xl) else []
        if busy:
            messagebox.showwarning('Excel', 'Спершу закрий книги в Excel: ' +
                                   ', '.join(f[2:] for f in busy), parent=self)
            return
        if not messagebox.askyesno(
                'Завантажити з Excel',
                'Узяти з книг Excel рядки, які там змінено після останнього вивантаження?\n\n'
                'Змінене в книзі замінить переклад у програмі (і в пов\'язаних однакових рядках).',
                parent=self):
            return
        self.flush()
        self.status.set('Читаю книги Excel…')

        def job():
            try:
                n = self.pr.load_excel()
                self.q.put(('call', lambda: self._after_excel(n)))
            except Exception as ex:                               # noqa: BLE001
                self.q.put(('status', f'Не вдалося прочитати книги: {ex}'))
        threading.Thread(target=job, daemon=True).start()

    def _after_excel(self, n):
        self._save()
        self._fill_nodes()
        self._refill()
        self._show_row()
        self.status.set(f'З Excel узято змін: {n}.' if n else 'У книгах Excel змін немає.')

    # ============================================================ інше
    def _terms_window(self):
        self.app.open_terms()

    def reload(self, pr):
        """Після «1»: нові рядки з гри."""
        self.flush()
        self.pr = pr
        if self.cur not in pr.by_key:
            self.cur = None
        self._fill_nodes()
        self._refill()
        self.ctx = None
        threading.Thread(target=self._load_bg, daemon=True).start()

    def _close(self):
        self.flush()
        if getattr(self.app, 'editor_win', None) is self:
            self.app.editor_win = None
        self.destroy()
