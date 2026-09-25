# -*- coding: utf-8 -*-
"""Вікно «Написи на картинках»: переклад з живим прев'ю.

Пишеш переклад — поруч одразу видно, як кнопка виглядатиме в грі (усі її
варіанти: звичайна, вибрана тощо). Зберігається в ту саму книгу Excel
«24 Написи на картинках», тож Excel лишається джерелом правди, а кнопка
«2. Залити переклад у гру» підхопить усе як завжди.
"""
import os, threading
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageTk

from maryskelter import atlas as atl, dds
from maryskelter.bra import Bra
from maryskelter.cl3 import Cl3

BOOK = '24 Написи на картинках.xlsx'
SHEET = 'Текст'
BG = (32, 32, 40, 255)
MAX_W = 380             # ширина прев'ю в пікселях екрана
MAX_VARIANTS = 4        # скільки різних варіантів кнопки показувати
DELAY = 180             # мс тиші після набору, перш ніж перемальовувати


class Atlases:
    """Розкодовані атласи з чистих оригіналів (backup, інакше тека гри)."""

    def __init__(self, backup_dir, game_dir):
        self.dirs = [d for d in (backup_dir, game_dir) if d]
        self.cache = {}
        self.lock = threading.Lock()

    def get(self, src, mark):
        with self.lock:
            if src not in self.cache:
                arc, _, name = src.partition('/')
                path = next((os.path.join(d, arc) for d in self.dirs
                             if os.path.exists(os.path.join(d, arc))), None)
                if path is None:
                    raise FileNotFoundError(arc)
                cl3 = Cl3(Bra.read_some(path, [name])[name])
                stem = mark.get('текстура')
                img = dds.decode(bytes(atl.pair(cl3, stem)[1][1]))
                self.cache[src] = (img, atl.frames(cl3, stem))
            return self.cache[src]


def variants(marks, key):
    """[(джерело, кадр, spec)] для ключа — без повторів за стилем і розміром."""
    out, seen = [], set()
    for src, mark in marks.items():
        for i, spec in sorted(mark['кадри'].items(), key=atl.order):
            if atl.key_of(spec) != key:
                continue
            sig = (spec['стиль'], tuple(spec.get('рамка') or ()), src if spec.get('рамка') else '')
            if sig in seen:
                continue
            seen.add(sig)
            out.append((src, i, spec))
    return out[:MAX_VARIANTS]


def render(atlases, marks, styles, key, text):
    """[(оригінал, результат, масштаб, [попередження])] для кожного варіанта."""
    res = []
    for src, i, spec in variants(marks, key):
        img, boxes = atlases.get(src, marks[src])
        box = atl.box_of(i, spec, boxes)
        orig = img.crop(box)
        if text:
            # малюємо на копії всього атласу: тло "шаблон" береться з іншого кадру
            work = img.copy()
            info = {}
            warn = atl.draw(work, box, spec, styles, text, info)
            res.append((orig, work.crop(box), info.get('масштаб', 1.0), warn))
        else:
            res.append((orig, orig, 1.0, []))
    return res


def to_photo(im):
    bg = Image.new('RGBA', im.size, BG)
    bg.alpha_composite(im)
    if bg.width > MAX_W:
        bg = bg.resize((MAX_W, max(1, round(bg.height * MAX_W / bg.width))), Image.LANCZOS)
    return ImageTk.PhotoImage(bg.convert('RGB'))


class Editor(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        work, xl, _out, bk = app.dirs()
        self.book = os.path.join(xl, BOOK)
        if not os.path.exists(self.book):
            self.destroy()
            raise RuntimeError('Книги «24 Написи на картинках» ще немає — '
                               'спершу натисни «1. Дістати текст з гри».')
        self.marks = {k: v for k, v in atl.load_json('написи.json').items() if not k.startswith('_')}
        self.styles = atl.load_json('стилі.json')
        self.atlases = Atlases(bk, app.root_dir())
        self.rows = self._read_book()           # [{id, src, tr, where}]
        self.edits = {}                          # id -> новий переклад (ще не збережений)
        self.cur = None
        self.gen = 0                             # номер рендеру: застарілі результати відкидаємо
        self.pending = None
        self.photos = []

        self.title('Написи на картинках — переклад з прев\'ю')
        self.geometry('1180x720')
        self.minsize(900, 520)
        c = app_colors(app)
        self.configure(bg=c['bg'])
        self._build(c)
        self._fill()
        self.protocol('WM_DELETE_WINDOW', self._close)
        if self.rows:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)

    # ---------------------------------------------------------------- книга
    def _read_book(self):
        from openpyxl import load_workbook
        wb = load_workbook(self.book, read_only=True, data_only=True)
        ws = wb[SHEET]
        it = ws.iter_rows(values_only=True)
        head = list(next(it))
        ix = {h: head.index(h) for h in ('id', 'Оригінал (EN)', 'Переклад', 'Хто / ключ')}
        rows = []
        for r in it:
            if not r or r[ix['id']] is None:
                continue
            rows.append({'id': str(r[ix['id']]), 'src': r[ix['Оригінал (EN)']] or '',
                         'tr': (r[ix['Переклад']] or '').strip() if isinstance(r[ix['Переклад']], str) else '',
                         'where': r[ix['Хто / ключ']] or ''})
        wb.close()
        return rows

    def _save(self):
        if not self.edits:
            return True
        lock = os.path.join(os.path.dirname(self.book), '~$' + BOOK)
        if os.path.exists(lock):
            messagebox.showwarning('Книга відкрита в Excel',
                                   'Закрий «24 Написи на картинках» в Excel і збережи ще раз — '
                                   'інакше Excel потім перезапише ці зміни.', parent=self)
            return False
        from openpyxl import load_workbook
        wb = load_workbook(self.book)
        ws = wb[SHEET]
        head = [c.value for c in ws[1]]
        i_id, i_tr = head.index('id'), head.index('Переклад')
        n = 0
        for row in ws.iter_rows(min_row=2):
            k = str(row[i_id].value)
            if k in self.edits:
                row[i_tr].value = self.edits[k] or None
                n += 1
        try:
            wb.save(self.book)
        except PermissionError:
            messagebox.showwarning('Не вдалося зберегти',
                                   'Книгу зайнято (відкрита в Excel?). Закрий її й спробуй ще раз.',
                                   parent=self)
            return False
        for r in self.rows:
            if r['id'] in self.edits:
                r['tr'] = self.edits[r['id']]
        self.edits.clear()
        self.saved.set(f'Збережено в книгу: {n}. Далі — «2. Залити переклад у гру».')
        self._fill(keep=True)
        return True

    def _close(self):
        if self.edits:
            ans = messagebox.askyesnocancel('Незбережені зміни',
                                            f'Змінено написів: {len(self.edits)}. Зберегти в книгу?',
                                            parent=self)
            if ans is None:
                return
            if ans and not self._save():
                return
        self.destroy()

    # ---------------------------------------------------------------- вигляд
    def _build(self, c):
        st = ttk.Style()
        st.configure('Pics.Treeview', background=c.get('panel', '#ffffff'),
                     fieldbackground=c.get('panel', '#ffffff'), foreground=c.get('fg', '#1a1a1a'),
                     rowheight=22)
        st.map('Pics.Treeview', background=[('selected', c.get('accent', '#1f4f82'))],
               foreground=[('selected', '#ffffff')])
        top = ttk.Frame(self)
        top.pack(fill='x', padx=10, pady=(10, 4))
        ttk.Label(top, text='Пошук:').pack(side='left')
        self.q = tk.StringVar()
        self.q.trace_add('write', lambda *_: self._fill())
        ttk.Entry(top, textvariable=self.q, width=30).pack(side='left', padx=6)
        self.only_todo = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text='лише неперекладені', variable=self.only_todo,
                        command=self._fill).pack(side='left', padx=6)
        self.count = tk.StringVar()
        ttk.Label(top, textvariable=self.count).pack(side='right')

        body = ttk.Panedwindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=10, pady=4)

        left = ttk.Frame(body)
        self.tree = ttk.Treeview(left, columns=('src', 'tr'), show='headings', selectmode='browse',
                                 style='Pics.Treeview')
        self.tree.heading('src', text='Оригінал')
        self.tree.heading('tr', text='Переклад')
        self.tree.column('src', width=160)
        self.tree.column('tr', width=160)
        sb = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='left', fill='y')
        self.tree.bind('<<TreeviewSelect>>', lambda e: self._select())
        self.tree.tag_configure('todo', foreground=c['dim'])
        self.tree.tag_configure('edit', foreground=c['warn'])
        body.add(left, weight=0)

        right = ttk.Frame(body, padding=(12, 0, 0, 0))
        body.add(right, weight=1)
        self.head = tk.StringVar()
        ttk.Label(right, textvariable=self.head, font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        self.where = tk.StringVar()
        ttk.Label(right, textvariable=self.where, style='Hint.TLabel').pack(anchor='w', pady=(0, 6))

        er = ttk.Frame(right)
        er.pack(fill='x')
        ttk.Label(er, text='Переклад:').pack(side='left')
        self.text = tk.StringVar()
        self.entry = ttk.Entry(er, textvariable=self.text, font=('Segoe UI', 12))
        self.entry.pack(side='left', fill='x', expand=True, padx=6)
        self.text.trace_add('write', lambda *_: self._typed())
        self.entry.bind('<Return>', lambda e: self._next())
        self.entry.bind('<Down>', lambda e: self._next())
        self.entry.bind('<Up>', lambda e: self._next(-1))
        ttk.Button(er, text='Лишити оригінал', command=lambda: self.text.set('')).pack(side='left')

        self.note = tk.StringVar()
        self.note_lbl = ttk.Label(right, textvariable=self.note)
        self.note_lbl.pack(anchor='w', pady=(4, 4))
        self.warn_color, self.ok_color = c['warn'], c['dim']

        grid = ttk.Frame(right)
        grid.pack(fill='both', expand=True)
        ttk.Label(grid, text='Оригінал', style='Hint.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(grid, text='У грі буде', style='Hint.TLabel').grid(row=0, column=1, sticky='w',
                                                                   padx=(12, 0))
        self.cells = []
        for k in range(MAX_VARIANTS):
            a = tk.Label(grid, bg=c['bg'], bd=0)
            b = tk.Label(grid, bg=c['bg'], bd=0)
            a.grid(row=k + 1, column=0, sticky='nw', pady=4)
            b.grid(row=k + 1, column=1, sticky='nw', pady=4, padx=(12, 0))
            self.cells.append((a, b))

        bot = ttk.Frame(self)
        bot.pack(fill='x', padx=10, pady=(4, 10))
        self.saved = tk.StringVar(value='Enter / ↓ — наступний напис, ↑ — попередній.')
        ttk.Label(bot, textvariable=self.saved, style='Hint.TLabel').pack(side='left')
        ttk.Button(bot, text='Закрити', command=self._close).pack(side='right')
        ttk.Button(bot, text='Зберегти в книгу', command=self._save).pack(side='right', padx=8)

    # ---------------------------------------------------------------- список
    def _value(self, r):
        return self.edits.get(r['id'], r['tr'])

    def _fill(self, keep=False):
        q = self.q.get().strip().lower()
        sel = self.cur
        self.tree.delete(*self.tree.get_children())
        shown = 0
        for r in self.rows:
            v = self._value(r)
            if self.only_todo.get() and v:
                continue
            if q and q not in r['src'].lower() and q not in v.lower():
                continue
            tag = 'edit' if r['id'] in self.edits else ('' if v else 'todo')
            self.tree.insert('', 'end', iid=r['id'], values=(r['src'], v or '—'), tags=(tag,))
            shown += 1
        done = sum(1 for r in self.rows if self._value(r))
        self.count.set(f'перекладено {done} з {len(self.rows)}')
        if sel and self.tree.exists(sel):
            self.tree.selection_set(sel)
            self.tree.see(sel)

    def _select(self):
        s = self.tree.selection()
        if not s or s[0] == self.cur:
            return
        self.cur = s[0]
        r = next(x for x in self.rows if x['id'] == self.cur)
        self.head.set(r['src'])
        self.where.set(f'Де: {r["where"]}')
        self._loading = True
        self.text.set(self._value(r))
        self._loading = False
        self.entry.focus_set()
        self.entry.icursor('end')
        self._schedule(0)

    def _next(self, step=1):
        items = self.tree.get_children()
        if not items:
            return 'break'
        k = items.index(self.cur) if self.cur in items else -1
        nxt = items[max(0, min(len(items) - 1, k + step))]
        self.tree.selection_set(nxt)
        self.tree.see(nxt)
        return 'break'

    # ---------------------------------------------------------------- прев'ю
    def _typed(self):
        if getattr(self, '_loading', False) or not self.cur:
            return
        r = next(x for x in self.rows if x['id'] == self.cur)
        v = self.text.get().strip()
        if v == r['tr']:
            self.edits.pop(r['id'], None)
        else:
            self.edits[r['id']] = v
        if self.tree.exists(r['id']):
            tag = 'edit' if r['id'] in self.edits else ('' if v else 'todo')
            self.tree.item(r['id'], values=(r['src'], v or '—'), tags=(tag,))
        self._schedule(DELAY)

    def _schedule(self, ms):
        if self.pending:
            self.after_cancel(self.pending)
        self.pending = self.after(ms, self._render)

    def _render(self):
        self.pending = None
        self.gen += 1
        gen, key, text = self.gen, self.cur, self.text.get().strip()
        self.note.set('малюю…')

        def work():
            try:
                res = render(self.atlases, self.marks, self.styles, key, text)
                err = None
            except Exception as ex:                                   # noqa: BLE001
                res, err = [], str(ex)
            self.after(0, lambda: self._show(gen, text, res, err))

        threading.Thread(target=work, daemon=True).start()

    def _show(self, gen, text, res, err):
        if gen != self.gen or not self.winfo_exists():
            return
        self.photos = []
        for k, (a, b) in enumerate(self.cells):
            if k < len(res):
                o, n = to_photo(res[k][0]), to_photo(res[k][1])
                self.photos += [o, n]
                a.configure(image=o)
                b.configure(image=n)
            else:
                a.configure(image='')
                b.configure(image='')
        if err:
            self.note.set(f'Не вдалося намалювати: {err}')
            self.note_lbl.configure(foreground=self.warn_color)
            return
        if not text:
            self.note.set('Порожньо — у грі лишиться оригінальний напис.')
            self.note_lbl.configure(foreground=self.ok_color)
            return
        scale = min((x[2] for x in res), default=1.0)
        warns = [w for x in res for w in x[3]]
        if warns:
            self.note.set(f'Задовге: напис зменшено до {scale:.0%} — спробуй коротше.')
            self.note_lbl.configure(foreground=self.warn_color)
        elif scale < 0.97:
            how = 'трохи стиснуто' if scale >= 0.85 else 'помітно зменшено'
            self.note.set(f'Вміщається, {how} ({scale:.0%} від оригінального розміру).')
            self.note_lbl.configure(foreground=self.ok_color)
        else:
            self.note.set('Вміщається без зменшення.')
            self.note_lbl.configure(foreground=self.ok_color)


def app_colors(app):
    import importlib
    mod = importlib.import_module('__main__')
    themes = getattr(mod, 'THEMES', None) or {'light': {'bg': '#f5f5f5', 'dim': '#6a6a6a',
                                                        'warn': '#8a6100'}}
    return themes.get(getattr(app, 'theme', 'light'), next(iter(themes.values())))
