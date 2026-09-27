# -*- coding: utf-8 -*-
"""Вікно «Знайти написи на картинках» — розмітка написів самим перекладачем.

Ліворуч — текстури гри, де можуть бути написи; посередині — вибрана текстура:
зелені рамки — уже розмічене, жовті — знайдене розпізнаванням (textscan.scan),
помаранчева — вибрана. Рамку можна перетягнути, змінити за краї й кути,
намалювати нову мишею по порожньому місці (для того, що розпізнавання пропустило)
або відкинути («Не текст», Delete). Праворуч — англійський текст, стиль і тло з
живим прев'ю; «Прийняти» записує кадр у розмітку перекладача (атлас/*.мої.json),
після «1» напис з'являється в книзі написів і в редакторі.
"""
import os, queue, subprocess, threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from PIL import Image, ImageTk

import pics
import textscan
from maryskelter import atlas as atl

ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.1, 8.0, 1.25
HANDLE = 6                      # «ручка» рамки, пікселі екрана
PAD_AREA = 3                    # область стирання = рамка напису + запас
PAD_FRAME = 12                  # кадр = рамка напису + місце під довший переклад
BG = (24, 16, 32, 255)
MODES = {'рядки': 'кнопка / плашка з однорідним тлом', 'прозорий': 'напис на прозорому'}
# налаштування вигляду прийнятого напису, які переживають відкликання й нову рамку
KEEP = ('правки', 'правки до шрифту', 'літери з оригіналу', 'кегль', 'вирівняти', 'ключ', 'приклад')


class StylePicker(ttk.Menubutton):
    """Вибір стилю: меню з підменю за групами стилів (atl.style_groups); свої — з ★."""

    def __init__(self, parent, var, **kw):
        super().__init__(parent, textvariable=var, **kw)
        self.var = var
        self.menu = tk.Menu(self, tearoff=False)
        self['menu'] = self.menu

    def refresh(self, styles, names):
        m = self.menu
        m.delete(0, 'end')
        groups = atl.style_groups(styles, names)
        for group, ns in groups:
            sub = m if len(groups) == 1 else tk.Menu(m, tearoff=False)
            for n in ns:
                sub.add_radiobutton(label=n + ('  ★' if styles[n].get('мій') else ''), value=n,
                                    variable=self.var)
            if sub is not m:
                m.add_cascade(label=f'{group}  ({len(ns)})', menu=sub)


class TextScan(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        work, self.xl, _out, bk = app.dirs()
        self.game = app.cur['game']
        if self.game not in textscan.MARKS:
            self.destroy()
            raise RuntimeError('Пошук написів на картинках є для Mary Skelter і Neptunia.')
        self.name = textscan.MARKS[self.game]
        self.tex = textscan.Textures(self.game, bk, app.root_dir())
        self.styles = atl.load_styles()
        nep = self.game == 'nep'
        self._style_list()
        self.found = textscan.load_found(self.game)
        self.q = queue.Queue()
        self.cur = None                 # (джерело, текстура)
        self.img = None                 # RGBA поточної текстури
        self.frames = {}
        self.cands = []                 # [{текст, рамка, стан}]: стан '' | 'вибрано'
        self.sel = None
        self.sel_done = None            # вибраний прийнятий напис (лише перегляд)
        self.drag = None
        self.photo = None
        self.prev_photos = []
        self.pending = None
        self.gen = 0
        self.busy = False

        self.title('Знайти написи на картинках — ' + ('Mary Skelter' if not nep else 'Neptunia'))
        self.geometry('1320x800')
        self.minsize(1000, 600)
        self._build()
        self._pic_state()
        self._fill()
        self.after(60, self._poll)
        # Delete / BackSpace — прибрати вибрану рамку; але не коли набираєш текст у полі
        for k in ('<Delete>', '<BackSpace>'):
            self.bind(k, self._key_reject)

    # ------------------------------------------------------------------ вигляд
    def _build(self):
        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill='x')
        self.b_scan = ttk.Button(top, text='Шукати написи (розпізнавання тексту)', style='Accent.TButton',
                                 command=self._scan)
        self.b_scan.pack(side='left')
        self.all_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text='Показати всі текстури', variable=self.all_var,
                        command=self._fill).pack(side='left', padx=12)
        # арти, новели, портрети тексту для розпізнавання не мають — але свою картинку
        # для них зробити можна
        self.art_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text='і арти, новели, портрети', variable=self.art_var,
                        command=self._fill).pack(side='left')
        self.status = tk.StringVar()
        ttk.Label(top, textvariable=self.status, style='Hint.TLabel').pack(side='left', padx=8)
        self.pbar = ttk.Progressbar(top, length=200, mode='determinate')

        body = ttk.Panedwindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=10, pady=4)

        left = ttk.Frame(body)
        body.add(left, weight=0)
        self.tree = ttk.Treeview(left, columns=('new', 'done'), show='tree headings', selectmode='browse',
                                 height=30)
        self.tree.heading('#0', text='Текстура')
        self.tree.heading('new', text='Нових')
        self.tree.heading('done', text='Розмічено')
        self.tree.column('#0', width=250)
        self.tree.column('new', width=50, anchor='e')
        self.tree.column('done', width=70, anchor='e')
        sb = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='left', fill='y')
        self.tree.bind('<<TreeviewSelect>>', lambda e: self._open())

        mid = ttk.Frame(body)
        body.add(mid, weight=3)
        bar = ttk.Frame(mid)
        bar.pack(fill='x')
        self.z = 1.0                     # масштаб картинки на полотні
        ttk.Button(bar, text='−', width=3, command=lambda: self._zoom_by(1 / ZOOM_STEP)).pack(side='left')
        self.zoom_lbl = ttk.Label(bar, text='100%', width=6, anchor='center')
        self.zoom_lbl.pack(side='left')
        ttk.Button(bar, text='+', width=3, command=lambda: self._zoom_by(ZOOM_STEP)).pack(side='left')
        ttk.Button(bar, text='Вмістити', command=self._fit).pack(side='left', padx=(6, 0))
        ttk.Button(bar, text='100%', command=lambda: self._zoom_to(1.0)).pack(side='left', padx=4)
        ttk.Label(bar, text='Ctrl + коліщатко — масштаб, права кнопка — тягнути картинку. Ліва по порожньому — '
                            'нова рамка; Delete / BackSpace — прибрати рамку. Зелене — розмічено, жовте — знайдено.',
                  style='Hint.TLabel', wraplength=560).pack(side='left', padx=8)
        # своя картинка: вивантажити оригінал у PNG, перемалювати деінде й завантажити назад
        own = ttk.Frame(mid)
        own.pack(fill='x', pady=(4, 2))
        ttk.Label(own, text='Своя картинка:').pack(side='left')
        self.b_pic_out = ttk.Button(own, text='Вивантажити оригінал', command=self._pic_export)
        self.b_pic_out.pack(side='left', padx=(6, 0))
        self.b_pic_in = ttk.Button(own, text='Завантажити свою…', command=self._pic_import)
        self.b_pic_in.pack(side='left', padx=4)
        self.b_pic_rm = ttk.Button(own, text='Прибрати свою', command=self._pic_remove)
        self.b_pic_rm.pack(side='left')
        self.show_own = tk.BooleanVar(value=False)
        self.c_pic_show = ttk.Checkbutton(own, text='показувати свою', variable=self.show_own,
                                          command=self._pic_show)
        self.c_pic_show.pack(side='left', padx=8)
        self.pic_state = tk.StringVar()
        ttk.Label(own, textvariable=self.pic_state, style='Hint.TLabel').pack(side='left', padx=4)
        cv = ttk.Frame(mid)
        cv.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(cv, bg='#181020', highlightthickness=0, cursor='crosshair')
        xs = ttk.Scrollbar(cv, orient='horizontal', command=self.canvas.xview)
        ys = ttk.Scrollbar(cv, orient='vertical', command=self.canvas.yview)
        # малюємо лише видиму частину картинки (великі текстури ×4 — сотні МБ), тож
        # після кожного прокручування перемальовуємо видиме
        self.canvas.configure(xscrollcommand=lambda *a: (xs.set(*a), self._view_later()),
                              yscrollcommand=lambda *a: (ys.set(*a), self._view_later()))
        self.canvas.bind('<Configure>', lambda e: self._view_later())
        self.canvas.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        xs.grid(row=1, column=0, sticky='ew')
        cv.rowconfigure(0, weight=1)
        cv.columnconfigure(0, weight=1)
        self.canvas.bind('<ButtonPress-1>', self._press)
        self.canvas.bind('<B1-Motion>', self._motion)
        self.canvas.bind('<ButtonRelease-1>', self._release)
        # права кнопка — тягнути саму картинку (навігація)
        self.canvas.bind('<ButtonPress-3>', lambda e: (self.canvas.scan_mark(e.x, e.y),
                                                       self.canvas.configure(cursor='fleur')))
        self.canvas.bind('<B3-Motion>', lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.canvas.bind('<ButtonRelease-3>', lambda e: self.canvas.configure(cursor='crosshair'))
        self.canvas.bind('<MouseWheel>', lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units'))
        self.canvas.bind('<Control-MouseWheel>',
                         lambda e: self._zoom_by(ZOOM_STEP if e.delta > 0 else 1 / ZOOM_STEP, e))
        self.canvas.bind('<Shift-MouseWheel>',
                         lambda e: self.canvas.xview_scroll(-1 if e.delta > 0 else 1, 'units'))

        right = ttk.Frame(body, padding=(10, 0, 0, 0))
        body.add(right, weight=1)
        ttk.Label(right, text='Вибраний напис', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        ttk.Label(right, text='Текст (англійською, як на картинці):').pack(anchor='w', pady=(8, 0))
        self.text = tk.StringVar()
        self.e_text = ttk.Entry(right, textvariable=self.text)
        self.e_text.pack(fill='x')
        ttk.Label(right, text='Стиль (яким малювати переклад):').pack(anchor='w', pady=(8, 0))
        self.style = tk.StringVar()
        srow = ttk.Frame(right)
        srow.pack(fill='x')
        cb = StylePicker(srow, self.style)
        cb.refresh(self.styles, self.style_names)
        cb.pack(side='left', fill='x', expand=True)
        self.b_styles = ttk.Button(srow, text='Усі стилі…', command=self._gallery)
        self.b_styles.pack(side='left', padx=(6, 0))
        self.style_cb = cb
        srow2 = ttk.Frame(right)
        srow2.pack(fill='x', pady=(4, 0))
        self.b_new_style = ttk.Button(srow2, text='Новий стиль…', command=lambda: self._edit_style(False))
        self.b_new_style.pack(side='left')
        self.b_edit_style = ttk.Button(srow2, text='Змінити свій стиль…', command=lambda: self._edit_style(True))
        self.b_edit_style.pack(side='left', padx=6)
        self.style.trace_add('write', lambda *_: self._own_state())
        ttk.Label(right, text='Тло під написом (як стерти старий):').pack(anchor='w', pady=(8, 0))
        self.mode = tk.StringVar(value='рядки')
        self.radios = []
        for m, d in MODES.items():
            rb = ttk.Radiobutton(right, text=f'{m} — {d}', value=m, variable=self.mode,
                                 command=self._changed)
            rb.pack(anchor='w')
            self.radios.append(rb)
        for v in (self.text, self.style):
            v.trace_add('write', lambda *_: self._changed())
        pv = ttk.Frame(right)
        pv.pack(fill='x', pady=(10, 0))
        self.prev = []
        for title in ('Оригінал', 'Стерто', 'Наш напис (англ.)'):
            ttk.Label(pv, text=title, style='Hint.TLabel').pack(anchor='w')
            lab = tk.Label(pv, bg='#181020', bd=0)
            lab.pack(anchor='w', pady=(0, 6))
            self.prev.append(lab)
        self.warn = tk.StringVar()
        ttk.Label(right, textvariable=self.warn, wraplength=330, foreground='#b36b00').pack(anchor='w')
        bt = ttk.Frame(right)
        bt.pack(fill='x', pady=(8, 0))
        self.b_ok = ttk.Button(bt, text='Прийняти напис', style='Accent.TButton', command=self._accept)
        self.b_ok.pack(side='left')
        self.b_no = ttk.Button(bt, text='Не текст', command=self._reject)
        self.b_no.pack(side='left', padx=6)
        self.b_revoke = ttk.Button(bt, text='Відкликати прийняття', command=self._revoke)
        self.b_revoke.pack(side='left')
        ttk.Label(right, text='Прийняте потрапляє в розмітку перекладача (атлас, файл «.мої.json»). '
                              'Після «1. Дістати текст з гри» напис з\'явиться в книзі написів і в '
                              'редакторі — там його й перекладай. Надішли цей файл власнику програми, '
                              'щоб розмітка не загубилась.',
                  style='Hint.TLabel', wraplength=330).pack(anchor='w', pady=(12, 0))
        self._enable(False)

    def _enable(self, on):
        for b in (self.b_ok, self.b_no, self.b_styles, self.b_new_style, self.e_text, *self.radios):
            b.state(['!disabled'] if on else ['disabled'])
        self.style_cb.state(['!disabled'] if on else ['disabled'])
        self.b_revoke.state(['!disabled'] if getattr(self, 'sel_done', None) is not None else ['disabled'])
        self._own_state()

    def _gallery(self):
        if self.sel is not None:
            StyleGallery(self)

    # ------------------------------------------------------------------ своя картинка
    def _pic_path(self, existing=False):
        """Шлях до своєї картинки поточної текстури (pics.py); existing — None, якщо файла ще немає."""
        if not self.cur:
            return None
        src, stem = self.cur
        p = os.path.join(pics.folder(self.xl), pics.name_of(src, stem if self.multi else ''))
        return p if not existing or os.path.exists(p) else None

    def _pic_state(self):
        on = self.cur is not None
        have = bool(self._pic_path(existing=True))
        self.b_pic_out.state(['!disabled'] if on else ['disabled'])
        self.b_pic_in.state(['!disabled'] if on else ['disabled'])
        self.b_pic_rm.state(['!disabled'] if have else ['disabled'])
        self.c_pic_show.state(['!disabled'] if have else ['disabled'])
        self.pic_state.set('є твоя — у грі буде вона (написи — поверх)' if have else
                           ('немає, у грі — оригінал' if on else ''))

    def _pic_show(self, fit=True):
        """Показувати на полотні свою картинку чи оригінал (розмітка й прев'ю — завжди з оригіналу)."""
        if self.img is None:
            return
        im = self.img
        p = self._pic_path(existing=True)
        if self.show_own.get() and p:
            try:
                im = pics.load(p, self.img)
            except (OSError, ValueError) as ex:
                self.pic_state.set(f'не вдалося відкрити свою: {ex}')
        bg = Image.new('RGBA', im.size, BG)
        bg.alpha_composite(im)
        self.flat = bg.convert('RGB')
        if fit:
            self._redraw(image=True)

    def _pic_export(self):
        """Чистий оригінал текстури — у PNG у «Свої картинки»; тека відкривається з вибраним файлом."""
        p = self._pic_path()
        if p is None:
            return
        if os.path.exists(p) and not messagebox.askyesno(
                'Своя картинка', 'Для цієї текстури вже є твоя картинка:\n' + os.path.basename(p) +
                '\n\nПереписати її оригіналом? Твої зміни в ній пропадуть.', parent=self):
            return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        self.img.save(p)
        self._pic_state()
        self._fill_keep()
        self.status.set(f'Оригінал вивантажено: {p}. Перемалюй його (розмір не міняй, прозорість '
                        'збережи) і збережи поверх — або «Завантажити свою…».')
        try:
            subprocess.Popen(['explorer', '/select,', os.path.normpath(p)])
        except OSError:
            pass

    def _pic_import(self):
        p = self._pic_path()
        if p is None:
            return
        fn = filedialog.askopenfilename(parent=self, title='Своя картинка для цієї текстури',
                                        filetypes=[('Картинки', '*.png *.tga *.bmp *.webp'), ('Усі', '*.*')])
        if not fn:
            return
        try:
            im = pics.load(fn, self.img)
        except (OSError, ValueError) as ex:
            messagebox.showerror('Своя картинка', f'Не підходить: {ex}', parent=self)
            return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.abspath(fn) != os.path.abspath(p):
            im.save(p)
        n = len(pics.changed_rects(self.img, im))
        self.show_own.set(True)
        self._pic_state()
        self._pic_show()
        self._fill_keep()
        self.status.set('Свою картинку завантажено' + ('' if n else ' — але вона така сама, як оригінал') +
                        '. У гру піде після «2. Залити переклад у гру».')

    def _pic_remove(self):
        p = self._pic_path(existing=True)
        if not p or not messagebox.askyesno('Своя картинка', 'Прибрати свою картинку? У грі знову буде '
                                            'оригінал.\n\n' + os.path.basename(p), parent=self):
            return
        os.remove(p)
        self.show_own.set(False)
        self._pic_state()
        self._pic_show()
        self._fill_keep()

    def _fill_keep(self):
        """Оновити список (позначка ✎), не втрачаючи вибраної текстури."""
        s = self.tree.selection()
        self._fill()
        if s and self.tree.exists(s[0]):
            self.tree.selection_set(s[0])       # _open ту саму текстуру не перевідкриває
            self.tree.see(s[0])

    # ------------------------------------------------------------------ свої стилі
    def _style_list(self):
        """Стилі цієї гри: основні (у Neptunia — з префіксом «неп-») і свої, створені для неї."""
        self.style_names = atl.game_styles(self.styles, self.game)
        if hasattr(self, 'style_cb'):
            self.style_cb.refresh(self.styles, self.style_names)

    def _own_state(self):
        own = bool(self.styles.get(self.style.get(), {}).get('мій'))
        if hasattr(self, 'b_edit_style'):
            self.b_edit_style.state(['!disabled'] if own and self.sel is not None else ['disabled'])

    def _edit_style(self, edit):
        if self.sel is not None:
            StyleEditor(self, self.style.get(), edit)

    def style_sample(self):
        """Для редактора стилю: (картинка, кадр, spec, [англ. текст, укр. текст|None]) вибраного
        напису або None."""
        if self.sel is None or self.img is None:
            return None
        key, spec = self._spec()
        return self.img, tuple(atl.box_of(key, spec, self.frames)), spec, [spec['текст'], None]

    def styles_changed(self, chosen=None):
        """Свої стилі змінились (редактор): перечитати й, якщо треба, вибрати `chosen`."""
        self.styles = atl.load_styles()
        self._style_list()
        if chosen is not None:
            self.style.set(chosen if chosen in self.style_names else '')
        self._changed()

    # ------------------------------------------------------------------ список
    def _marks(self):
        return atl.load_marks(self.name)

    def _rejected(self, key):
        return atl.load_user(self.name).get('_відхилено', {}).get(key, [])

    def _marked(self, src, stem, frames, marks=None):
        """Прийняті написи текстури: [{ключ, spec, рамка}] (рамка — кадр в атласі)."""
        mk = (marks if marks is not None else self._marks()).get(src)
        if not mk:
            return []
        if stem and mk.get('текстура') and mk['текстура'].lower() != stem.lower():
            return []
        out = []
        for i, spec in mk.get('кадри', {}).items():
            b = atl.box_of(i, spec, frames)
            if b:
                out.append({'ключ': i, 'spec': spec, 'рамка': list(b)})
        return out

    def _marked_boxes(self, src, stem, frames, marks=None):
        return [d['рамка'] for d in self._marked(src, stem, frames, marks)]

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        marks = self._marks()
        srcs = set(self.found)
        every = self.all_var.get() or self.art_var.get()
        if every:
            srcs |= set(self._all_srcs())
            srcs |= set(marks)
        own = pics.found(self.xl)
        rows = []
        if not hasattr(self, '_frames'):
            self._frames = self.tex.frames_of([s for s in self.found if self.found[s]])
        for src in srcs:
            for stem, lst in (self.found.get(src) or {'': []}).items():
                key = f'{src}|{stem}'
                rej = self._rejected(key)
                fr = self._frames.get(src, {})
                done_b = (self._marked_boxes(src, stem if len(fr) > 1 else '', fr.get(stem, {}), marks)
                          if lst else [])
                n = sum(1 for c in lst if not any(textscan.overlap(c['рамка'], r) > 0.5 for r in rej)
                        and not any(textscan.overlap(c['рамка'], d) > 0.5 for d in done_b))
                done = len((marks.get(src) or {}).get('кадри', {}))
                if n or every:
                    rows.append((src, stem, n, done))
        rows.sort(key=lambda r: (-r[2], r[0]))
        for src, stem, n, done in rows:
            label = src.split('/', 1)[-1] + (f' [{stem}]' if stem and stem.lower() not in src.lower() else '')
            if src in own:
                label += '  ✎'                   # є своя картинка
            self.tree.insert('', 'end', iid=f'{src}|{stem}', text=label, values=(n or '', done or ''))
        if not rows:
            self.status.set('Натисни «Шукати написи», щоб програма знайшла кандидатів, або постав '
                            '«Показати всі текстури» й розмічай вручну.')
        else:
            self.status.set(f'Текстур у списку: {len(rows)}.')

    def _all_srcs(self):
        art = self.art_var.get()
        if getattr(self, '_srcs_art', None) != art:
            self._srcs, self._srcs_art = self.tex.list(everything=art), art
        return self._srcs

    # ------------------------------------------------------------------ пошук
    def _scan(self):
        if self.busy:
            return
        self.busy = True
        self.b_scan.state(['disabled'])
        self.status.set('Перевіряю, чи вміє Windows розпізнавати англійський текст…')

        def work():
            st = textscan.ocr_status()
            self.q.put(('status', st))
        threading.Thread(target=work, daemon=True).start()

    def _after_status(self, st):
        if st == 'nolang':
            if messagebox.askyesno(
                    'Розпізнавання тексту',
                    'У Windows не встановлено розпізнавання англійського тексту.\n\n'
                    'Встановити зараз? Windows попросить дозволу адміністратора (вікно UAC), '
                    'далі компонент завантажиться з інтернету (кілька МБ, до хвилини).',
                    parent=self):
                self.status.set('Встановлюю компонент розпізнавання… (підтверди запит Windows)')

                def work():
                    self.q.put(('status2', textscan.install_ocr()))
                threading.Thread(target=work, daemon=True).start()
                return
            self._done_busy('Без розпізнавання можна розмічати вручну: «Показати всі текстури».')
            return
        if st != 'ok':
            self._done_busy('')
            messagebox.showwarning('Розпізнавання тексту',
                                   'Розпізнавання тексту Windows недоступне на цьому комп\'ютері '
                                   '(потрібна Windows 10 чи 11). Розмічати можна вручну: '
                                   '«Показати всі текстури» і мишею по картинці.', parent=self)
            return
        self.status.set('Готую перелік текстур…')
        self.pbar.pack(side='left', padx=8)

        def work():
            srcs = self._all_srcs()
            res = textscan.scan(self.tex, srcs, progress=lambda i, n, t: self.q.put(('prog', i, n, t)),
                                stop=lambda: not self.winfo_exists())
            self.q.put(('found', res))
        threading.Thread(target=work, daemon=True).start()

    def _done_busy(self, msg):
        self.busy = False
        self.b_scan.state(['!disabled'])
        self.pbar.pack_forget()
        if msg:
            self.status.set(msg)

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                m = self.q.get_nowait()
                if m[0] == 'status':
                    self._after_status(m[1])
                elif m[0] == 'status2':
                    if m[1] == 'ok':
                        self.status.set('Розпізнавання встановлено.')
                        self._after_status('ok')
                    else:
                        self._done_busy('Встановити не вдалося (або запит Windows відхилено).')
                elif m[0] == 'prog':
                    _t, i, n, txt = m
                    self.pbar.configure(maximum=n, value=i)
                    self.status.set(f'{txt} {round(i)}/{n}')
                elif m[0] == 'found':
                    self.found = m[1]
                    textscan.save_found(self.game, self.found)
                    self.__dict__.pop('_frames', None)
                    n = sum(len(v) for s in self.found.values() for v in s.values())
                    self._done_busy(f'Знайдено кандидатів: {n}.')
                    self._fill()
                elif m[0] == 'preview':
                    self._show_preview(*m[1:])
        except queue.Empty:
            pass
        self.after(60, self._poll)

    # ------------------------------------------------------------------ текстура
    def _open(self):
        s = self.tree.selection()
        if not s or (s[0] == getattr(self, 'cur_iid', None) and self.img is not None):
            return
        self.cur_iid = s[0]
        src, _, stem = s[0].rpartition('|')
        try:
            parts = self.tex.load(src)
        except Exception as ex:                                       # noqa: BLE001
            self.status.set(f'Не вдалося відкрити {src}: {ex}')
            return
        part = next((p for p in parts if p[0] == stem), parts[0] if parts else None)
        if part is None:
            return
        # текстура — як у пошуку (назва .tid у CL3); у розмітку "текстура" пишемо, лише
        # коли пар у атласі кілька
        self.cur = (src, part[0])
        self.multi = len(parts) > 1
        self.img, self.frames = part[1], part[2]
        rej = self._rejected(f'{src}|{part[0]}')
        self.done = self._marked(src, part[0] if self.multi else '', self.frames)
        self.cands = [dict(c) for c in (self.found.get(src) or {}).get(part[0], [])
                      if not any(textscan.overlap(c['рамка'], r) > 0.5 for r in rej)
                      and not any(textscan.overlap(c['рамка'], d['рамка']) > 0.5 for d in self.done)]
        self.sel = None
        self.sel_done = None
        self._select(None)
        bg = Image.new('RGBA', self.img.size, BG)
        bg.alpha_composite(self.img)
        self.flat = bg.convert('RGB')                 # текстура на темному тлі — для показу
        self._pic_state()
        if self.show_own.get() and self._pic_path(existing=True):
            self._pic_show(fit=False)
        self._fit()

    def _z(self):
        return self.z

    # ------------------------------------------------------------------ масштаб
    def _fit(self):
        """Уся текстура у вікні: велика — зменшити, мала — збільшити (до 400%)."""
        if self.img is None:
            return
        self.update_idletasks()
        w, h = max(50, self.canvas.winfo_width() - 4), max(50, self.canvas.winfo_height() - 4)
        self._zoom_to(min(4.0, w / self.img.width, h / self.img.height))

    def _zoom_by(self, k, e=None):
        self._zoom_to(self.z * k, e)

    def _zoom_to(self, z, e=None):
        """Новий масштаб; точка під курсором (або центр полотна) лишається на місці."""
        if self.img is None:
            return
        z = max(ZOOM_MIN, min(ZOOM_MAX, z))
        c = self.canvas
        ex, ey = (e.x, e.y) if e is not None else (c.winfo_width() / 2, c.winfo_height() / 2)
        ix, iy = c.canvasx(ex) / self.z, c.canvasy(ey) / self.z
        self.z = z
        W, H = self.img.width * z, self.img.height * z
        c.configure(scrollregion=(0, 0, W, H))
        c.xview_moveto(max(0.0, (ix * z - ex) / W))
        c.yview_moveto(max(0.0, (iy * z - ey) / H))
        self.zoom_lbl.configure(text=f'{round(z * 100)}%')
        self._redraw(True)

    def _view_later(self):
        if getattr(self, '_view_job', None) is None:
            self._view_job = self.after_idle(self._view)

    def _view(self):
        """Намалювати видиму частину текстури в поточному масштабі."""
        self._view_job = None
        if self.img is None or not hasattr(self, 'flat'):
            return
        c, z = self.canvas, self.z
        vx0, vy0 = c.canvasx(0), c.canvasy(0)
        vx1, vy1 = vx0 + c.winfo_width(), vy0 + c.winfo_height()
        ix0, iy0 = max(0, int(vx0 / z)), max(0, int(vy0 / z))
        ix1 = min(self.img.width, int(vx1 / z) + 2)
        iy1 = min(self.img.height, int(vy1 / z) + 2)
        if ix1 <= ix0 or iy1 <= iy0:
            return
        key = (ix0, iy0, ix1, iy1, z)
        if key == getattr(self, '_view_key', None):
            return
        self._view_key = key
        part = self.flat.crop((ix0, iy0, ix1, iy1))
        size = (max(1, round((ix1 - ix0) * z)), max(1, round((iy1 - iy0) * z)))
        part = part.resize(size, Image.NEAREST if z >= 1 else Image.BOX)
        self.photo = ImageTk.PhotoImage(part)
        c.delete('img')
        c.create_image(ix0 * z, iy0 * z, image=self.photo, anchor='nw', tags='img')
        c.tag_lower('img')

    def _redraw(self, image=False):
        if self.img is None:
            return
        z = self._z()
        c = self.canvas
        if image:
            self._view_key = None
            self._view()
        c.delete('box')
        for k, d in enumerate(self.done):
            sel = k == self.sel_done
            c.create_rectangle(*[v * z for v in d['рамка']], outline='#4aa8ff' if sel else '#3ddc84',
                               width=3 if sel else 2, tags='box')
        for k, cd in enumerate(self.cands):
            b = cd['рамка']
            col = '#ff8a00' if k == self.sel else '#ffd400'
            c.create_rectangle(*[v * z for v in b], outline=col, width=3 if k == self.sel else 2, tags='box')
            if k == self.sel:
                for hx, hy in self._handles(b):
                    c.create_rectangle(hx * z - HANDLE / 2, hy * z - HANDLE / 2, hx * z + HANDLE / 2,
                                       hy * z + HANDLE / 2, fill=col, outline='', tags='box')

    @staticmethod
    def _handles(b):
        x0, y0, x1, y1 = b
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return [(x0, y0), (mx, y0), (x1, y0), (x1, my), (x1, y1), (mx, y1), (x0, y1), (x0, my)]

    # ------------------------------------------------------------------ миша
    def _pt(self, e):
        z = self._z()
        return self.canvas.canvasx(e.x) / z, self.canvas.canvasy(e.y) / z

    def _press(self, e):
        if self.img is None:
            return
        x, y = self._pt(e)
        tol = HANDLE / self._z()
        if self.sel is not None:
            b = self.cands[self.sel]['рамка']
            for n, (hx, hy) in enumerate(self._handles(b)):
                if abs(hx - x) <= tol and abs(hy - y) <= tol:
                    self.drag = ('size', n, list(b), x, y)
                    return
        for k in range(len(self.cands) - 1, -1, -1):
            x0, y0, x1, y1 = self.cands[k]['рамка']
            if x0 <= x <= x1 and y0 <= y <= y1:
                self._select(k)
                self.drag = ('move', None, list(self.cands[k]['рамка']), x, y)
                return
        # клік по прийнятому напису — подивитись (змінити — «Відкликати прийняття»);
        # потягнути — нова рамка, як і деінде (кадр буває більшим за сам напис)
        hit = [k for k, d in enumerate(self.done)
               if d['рамка'][0] <= x <= d['рамка'][2] and d['рамка'][1] <= y <= d['рамка'][3]]

        def area(k):
            b = self.done[k]['рамка']
            return (b[2] - b[0]) * (b[3] - b[1])
        self.drag = ('new', min(hit, key=area) if hit else None, None, x, y)

    def _motion(self, e):
        if not self.drag:
            return
        kind, n, b0, sx, sy = self.drag
        x, y = self._pt(e)
        W, H = self.img.size
        if kind == 'new':
            if abs(x - sx) < 4 and abs(y - sy) < 4:
                return
            self.cands.append({'текст': '', 'рамка': [0, 0, 0, 0], 'ручна': True})
            self._select(len(self.cands) - 1)
            self.drag = kind, n, b0, sx, sy = ('size', 4, [sx, sy, sx, sy], sx, sy)
        b = list(b0)
        if kind == 'move':
            dx, dy = x - sx, y - sy
            dx = max(-b[0], min(W - b[2], dx))
            dy = max(-b[1], min(H - b[3], dy))
            b = [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy]
        else:
            if n in (0, 6, 7):
                b[0] = x
            if n in (2, 3, 4):
                b[2] = x
            if n in (0, 1, 2):
                b[1] = y
            if n in (4, 5, 6):
                b[3] = y
        x0, x1 = sorted((max(0, min(W, b[0])), max(0, min(W, b[2]))))
        y0, y1 = sorted((max(0, min(H, b[1])), max(0, min(H, b[3]))))
        self.cands[self.sel]['рамка'] = [round(x0), round(y0), round(x1), round(y1)]
        self._redraw()

    def _release(self, _e):
        if self.drag and self.drag[0] == 'new':            # клік без рамки кандидата
            k = self.drag[1]
            self.drag = None
            if k is None:
                self._select(None)                         # по порожньому — зняти вибір
            else:
                self._select_done(k)
            return
        if self.drag and self.sel is not None:
            b = self.cands[self.sel]['рамка']
            if b[2] - b[0] < 4 or b[3] - b[1] < 4:        # випадковий клік — не рамка
                del self.cands[self.sel]
                self._select(None)
            else:
                self._guess()
                self._changed()
        self.drag = None
        self._redraw()

    # ------------------------------------------------------------------ вибраний
    def _select(self, k):
        self.sel = k
        self.sel_done = None
        self._enable(k is not None)
        if k is None:
            self.text.set('')
            for lab in self.prev:
                lab.configure(image='')
            self.warn.set('')
            self._redraw()
            return
        cd = self.cands[k]
        self._loading = True
        self.text.set(cd.get('текст', ''))
        self._loading = False
        self._guess()
        self._redraw()
        self._changed()

    def _select_done(self, k):
        """Прийнятий напис: показати, як його розмічено, без права змінювати."""
        self._select(None)
        self.sel_done = k
        d = self.done[k]
        self._loading = True
        self.text.set(d['spec'].get('текст', ''))
        self.style.set(d['spec'].get('стиль', ''))
        self.mode.set(d['spec'].get('тло', ''))
        self._loading = False
        self._enable(False)
        self.status.set('Цей напис уже прийнято. Щоб змінити текст, стиль чи рамку — '
                        '«Відкликати прийняття».')
        self._redraw()
        self._render(d['ключ'], d['spec'])

    def _revoke(self):
        """Прийнятий напис → знову кандидат з тими самими текстом, стилем і тлом. У розмітці
        він лишається, доки не натиснеш «Прийняти напис» (замінить) чи «Не текст» (прибере)."""
        if self.sel_done is None:
            return
        d = self.done.pop(self.sel_done)
        spec = d['spec']
        fx0, fy0, fx1, fy1 = d['рамка']
        a = spec.get('область')
        box = ([fx0 + a[0], fy0 + a[1], fx0 + a[2], fy0 + a[3]]
               if a and not spec.get('обертання') else [fx0, fy0, fx1, fy1])
        self.cands.append({'текст': spec.get('текст', ''), 'рамка': box, 'стиль': spec.get('стиль'),
                           'тло': spec.get('тло'), 'ручна': True,
                           'старий': {'ключ': d['ключ'], 'spec': spec, 'рамка0': list(box)}})
        self._select(len(self.cands) - 1)
        self.status.set('Напис відкликано: зміни, що треба, і «Прийняти напис». Доки не натиснеш, '
                        'у розмітці лишається старий варіант.')

    def _guess(self):
        """Стиль і тло — за кольором літер і прозорістю навколо напису."""
        cd = self.cands[self.sel]
        x0, y0, x1, y1 = cd['рамка']
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        if cd.get('старий'):                       # відкликаний: тло й стиль — як були
            self.mode.set(cd.get('тло') or 'рядки')
            if cd.get('стиль'):
                self.style.set(cd['стиль'])
            return
        crop = self.img.crop((x0, y0, x1, y1))
        px = [p for p in crop.getdata() if p[3] > 200 and max(p[:3]) > 150]
        ring = self.img.crop((max(0, x0 - 4), max(0, y0 - 4), min(self.img.width, x1 + 4),
                              min(self.img.height, y1 + 4)))
        alphas = [p[3] for p in ring.getdata()]
        self.mode.set('прозорий' if sum(a < 40 for a in alphas) > len(alphas) * 0.3 else 'рядки')
        if cd.get('стиль'):
            self.style.set(cd['стиль'])
            return
        if not self.style_names:
            return
        import collections
        marks = self._marks()
        used = collections.Counter(s['стиль'] for m in marks.values() for s in m.get('кадри', {}).values()
                                   if s.get('стиль') in self.style_names)
        if not px:
            # світлих літер не видно (рамка ще порожня) — найуживаніший стиль атласу, інакше гри
            here = collections.Counter(s.get('стиль') for s in (marks.get(self.cur[0]) or {}).get('кадри', {}).values()
                                       if s.get('стиль') in self.style_names)
            best = (here or used).most_common(1)
            self.style.set(best[0][0] if best else self.style_names[0])
            return
        med = [sorted(p[c] for p in px)[len(px) // 2] for c in range(3)]

        def dist(name):
            fill = self.styles[name].get('заливка', '#ffffff')
            fill = fill[0] if isinstance(fill, list) else fill
            rgb = [int(fill[i:i + 2], 16) for i in (1, 3, 5)]
            d = sum((a - b) ** 2 for a, b in zip(rgb, med))
            return d + (0 if name in used else 4000)            # уживані стилі — вперед
        self.style.set(min(self.style_names, key=dist))

    def _spec(self):
        """Кадр розмітки для вибраної рамки: (ключ, spec) у форматі написи.json. Відкликаний
        напис: рамка й тло ті самі — старий кадр з новими текстом і стилем; інакше — новий,
        але з тим самим ключем і додатковими налаштуваннями вигляду."""
        cd = self.cands[self.sel]
        old = cd.get('старий')
        if old and cd['рамка'] == old['рамка0'] and self.mode.get() == old['spec'].get('тло'):
            return old['ключ'], dict(old['spec'], текст=self.text.get().strip() or '?',
                                     стиль=self.style.get())
        key, spec = self._new_spec()
        if old:
            key = old['ключ']
            spec.update({k: v for k, v in old['spec'].items() if k in KEEP})
        return key, spec

    def _new_spec(self):
        cd = self.cands[self.sel]
        x0, y0, x1, y1 = cd['рамка']
        W, H = self.img.size
        # кадр нарізки, у якому лежить напис: ручна рамка має бути всередині нього
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        inside = [f for f in self.frames.values() if f[0] <= cx <= f[2] and f[1] <= cy <= f[3]]
        frame = min(inside, key=lambda f: (f[2] - f[0]) * (f[3] - f[1])) if inside else (0, 0, W, H)
        fb = [max(frame[0], x0 - PAD_FRAME), max(frame[1], y0 - PAD_FRAME // 2),
              min(frame[2], x1 + PAD_FRAME), min(frame[3], y1 + PAD_FRAME // 2)]
        area = [max(0, x0 - PAD_AREA - fb[0]), max(0, y0 - PAD_AREA - fb[1]),
                min(fb[2] - fb[0], x1 + PAD_AREA - fb[0]), min(fb[3] - fb[1], y1 + PAD_AREA - fb[1])]
        mode = self.mode.get()
        spec = {'текст': self.text.get().strip() or '?', 'стиль': self.style.get(), 'тло': mode,
                'рамка': fb, 'область': area, 'літери': [y0 - fb[1], y1 - fb[1]],
                'поле': atl.suggest_field(area, fb[2] - fb[0], mode), 'вирівняти': 'центр'}
        if not inside and self.game == 'msk':
            spec['без кадру'] = True
        return ','.join(str(v) for v in fb), spec

    def _changed(self):
        if getattr(self, '_loading', False) or self.sel is None:
            return
        self.cands[self.sel]['текст'] = self.text.get()
        if self.cands[self.sel].get('старий'):
            self.cands[self.sel]['тло'] = self.mode.get()
        if self.style.get():
            self.cands[self.sel]['стиль'] = self.style.get()
        if self.pending:
            self.after_cancel(self.pending)
        self.pending = self.after(250, self._render)

    def _render(self, key=None, spec=None):
        self.pending = None
        if spec is None:
            if self.sel is None or not self.style.get():
                return
            key, spec = self._spec()
        self.gen += 1
        gen, img = self.gen, self.img
        styles = self.styles
        box = tuple(atl.box_of(key, spec, self.frames))       # кадр TI — без "рамка" в розмітці

        def work():
            try:
                a = img.copy()
                atl.erase(a, box, spec)
                b = img.copy()
                warn = atl.draw(b, box, spec, styles, spec['текст'])
                self.q.put(('preview', gen, img.crop(box), a.crop(box), b.crop(box), warn))
            except Exception as ex:                                   # noqa: BLE001
                self.q.put(('preview', gen, img.crop(box), None, None, [f'не вдалося: {ex}']))
        threading.Thread(target=work, daemon=True).start()

    def _show_preview(self, gen, orig, erased, drawn, warn):
        if gen != self.gen:
            return
        self.prev_photos = []
        for lab, im in zip(self.prev, (orig, erased, drawn)):
            if im is None:
                lab.configure(image='')
                continue
            bg = Image.new('RGBA', im.size, BG)
            bg.alpha_composite(im)
            s = min(3.0, 320 / max(1, bg.width))
            bg = bg.resize((max(1, int(bg.width * s)), max(1, int(bg.height * s))), Image.LANCZOS)
            ph = ImageTk.PhotoImage(bg.convert('RGB'))
            self.prev_photos.append(ph)
            lab.configure(image=ph)
        self.warn.set('\n'.join(warn))

    # ------------------------------------------------------------------ рішення
    def _accept(self):
        if self.sel is None:
            return
        if not self.text.get().strip():
            messagebox.showinfo('Напис', 'Впиши англійський текст напису (як на картинці).', parent=self)
            return
        src, stem = self.cur
        cd = self.cands[self.sel]
        old = cd.get('старий')
        if old and self.mode.get() not in MODES and cd['рамка'] != old['рамка0']:
            messagebox.showinfo('Напис', f'Тло «{self.mode.get()}» прив\'язане до старої рамки. '
                                'Поверни рамку як була або вибери, як стерти старий напис.', parent=self)
            return
        key, spec = self._spec()
        if old and atl.key_of(old['spec']) != atl.key_of(spec) and not messagebox.askyesno(
                'Напис', f'Текст змінився: «{old["spec"].get("текст", "")}» → «{spec["текст"]}».\n\n'
                'У книзі написів це буде новий рядок: переклад старого (якщо він був) сюди не '
                'перейде. Прийняти?', parent=self):
            return
        marks = atl.load_marks(self.name)
        mk = marks.get(src)
        if mk is None:
            mk = marks[src] = {'назва': src.split('/')[-1], 'кадри': {}}
            if self.multi and stem:
                mk['текстура'] = stem
        elif self.multi and stem and (mk.get('текстура') or '').lower() != stem.lower():
            messagebox.showwarning('Напис', 'У цьому атласі вже розмічено іншу текстуру — '
                                   'дві в одному атласі програма поки не підтримує.', parent=self)
            return
        mk.setdefault('кадри', {})[key] = spec
        atl.save_user_marks(self.name, marks)
        self.done.append({'ключ': key, 'spec': spec,
                          'рамка': list(atl.box_of(key, spec, self.frames) or spec['рамка'])})
        del self.cands[self.sel]
        self._select(None)
        self._update_row()
        self.status.set(f'Прийнято: «{spec["текст"]}». Після «1» він з\'явиться в книзі написів.')

    def _key_reject(self, e):
        if isinstance(e.widget, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text)):
            return None                                  # у полі вводу клавіша редагує текст
        self._reject()
        return 'break'

    def _reject(self):
        if self.sel is None:
            return
        cd = self.cands[self.sel]
        old = cd.get('старий')
        if old:                                          # відкликаний — прибрати з розмітки
            if not messagebox.askyesno('Напис', f'Прибрати напис «{old["spec"].get("текст", "")}» '
                                       'з розмітки? У книзі його більше не буде.', parent=self):
                return
            marks = atl.load_marks(self.name)
            (marks.get(self.cur[0]) or {}).get('кадри', {}).pop(old['ключ'], None)
            atl.save_user_marks(self.name, marks)
        elif not cd.get('ручна'):
            user = atl.load_user(self.name)
            rej = user.setdefault('_відхилено', {})
            rej.setdefault(f'{self.cur[0]}|{self.cur[1]}', []).append(cd['рамка'])
            atl.save_user(self.name, user)
        del self.cands[self.sel]
        self._select(None)
        self._update_row()

    def _update_row(self):
        s = self.tree.selection()
        if s:
            done = len((self._marks().get(self.cur[0]) or {}).get('кадри', {}))
            self.tree.item(s[0], values=(len(self.cands) or '', done or ''))


class StyleGallery(tk.Toplevel):
    """«Усі стилі»: вибраний напис, намальований кожним стилем гри (стерте тло + англійський
    текст), — щоб не перебирати стилі по одному. Клік — вибрати, подвійний клік чи «Взяти
    вибраний» — у вікно пошуку. Уживані в грі стилі — першими."""

    COLS = 3
    CARD_W = 300                            # ширина картинки в картці, пікселі екрана

    def __init__(self, scan):
        super().__init__(scan)
        import collections
        self.scan = scan
        self.key, self.spec = scan._spec()
        self.img = scan.img
        self.styles = scan.styles
        used = collections.Counter(s.get('стиль') for m in scan._marks().values()
                                   for s in m.get('кадри', {}).values())
        # за групами; у групі — уживані в грі першими
        self.groups = [(g, sorted(ns, key=lambda n: -used.get(n, 0)))
                       for g, ns in atl.style_groups(scan.styles, scan.style_names)]
        self.names = [n for _g, ns in self.groups for n in ns]
        self.sel = self.names.index(scan.style.get()) if scan.style.get() in self.names else 0
        self.q = queue.Queue()
        self.photos = {}
        self.alive = True
        self.title(f'Усі стилі — «{self.spec["текст"]}»')
        self.geometry('1060x760')
        box = tuple(self.spec['рамка'])
        self.zoom = min(2.0, self.CARD_W / max(1, box[2] - box[0]))

        top = ttk.Frame(self, padding=(10, 10, 10, 4))
        top.pack(fill='x')
        ttk.Label(top, text='Оригінал:').pack(side='left', anchor='n')
        self.orig_ph = self._photo(self.img.crop(box))
        tk.Label(top, image=self.orig_ph, bg='#181020', bd=0).pack(side='left', padx=8)
        ttk.Label(top, text='Клік — вибрати, подвійний клік — взяти. Стилі — за групами, у групі '
                            'перші — ті, що вже є в грі.',
                  style='Hint.TLabel').pack(side='left', padx=8)

        body = ttk.Frame(self)
        body.pack(fill='both', expand=True, padx=10)
        self.canvas = tk.Canvas(body, highlightthickness=0, bg='#221b2b')
        sb = ttk.Scrollbar(body, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.inner = tk.Frame(self.canvas, bg='#221b2b')
        self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.bind('<MouseWheel>', self._wheel)
        self.cards = []
        place, row = [], 0                  # (рядок, стовпчик) кожної картки; заголовок групи — свій рядок
        for group, ns in self.groups:
            if len(self.groups) > 1:
                tk.Label(self.inner, text=group, bg='#221b2b', fg='#c8a8ff', anchor='w',
                         font=('Segoe UI', 11, 'bold')).grid(row=row, column=0, columnspan=self.COLS,
                                                              sticky='w', padx=4, pady=(10, 0))
                row += 1
            for j in range(len(ns)):
                place.append((row + j // self.COLS, j % self.COLS))
            row += (len(ns) + self.COLS - 1) // self.COLS
        for k, name in enumerate(self.names):
            card = tk.Frame(self.inner, bg='#221b2b', highlightthickness=2, highlightbackground='#221b2b',
                            padx=6, pady=4)
            card.grid(row=place[k][0], column=place[k][1], sticky='nw', padx=4, pady=4)
            mine = self.styles.get(name, {}).get('мій')
            lab = tk.Label(card, text=name + ('  ★ мій' if mine else ''), bg='#221b2b',
                           fg='#ffd970' if mine else '#eeeeee', anchor='w', font=('Segoe UI', 9))
            lab.pack(anchor='w')
            pic = tk.Label(card, text='…', bg='#181020', fg='#999999', bd=0)
            pic.pack(anchor='w')
            for w in (card, lab, pic):
                w.bind('<Button-1>', lambda e, n=k: self._select(n))
                w.bind('<Double-Button-1>', lambda e, n=k: (self._select(n), self._take()))
                w.bind('<MouseWheel>', self._wheel)
            self.cards.append((card, pic))

        bot = ttk.Frame(self, padding=(10, 6, 10, 10))
        bot.pack(fill='x')
        ttk.Button(bot, text='Закрити', command=self._close).pack(side='right')
        ttk.Button(bot, text='Взяти вибраний', style='Accent.TButton', command=self._take).pack(side='right', padx=8)
        ttk.Button(bot, text='Новий стиль на основі вибраного…', command=self._new).pack(side='left')
        self.protocol('WM_DELETE_WINDOW', self._close)
        self._select(self.sel)
        threading.Thread(target=self._work, daemon=True).start()
        self.after(50, self._poll)

    def _photo(self, im):
        bg = Image.new('RGBA', im.size, BG)
        bg.alpha_composite(im)
        if self.zoom != 1.0:
            bg = bg.resize((max(1, int(bg.width * self.zoom)), max(1, int(bg.height * self.zoom))),
                           Image.LANCZOS)
        return ImageTk.PhotoImage(bg.convert('RGB'))

    def _work(self):
        box = tuple(self.spec['рамка'])
        for k, name in enumerate(self.names):
            if not self.alive:
                return
            spec = dict(self.spec, стиль=name)
            try:
                im = self.img.copy()
                atl.draw(im, box, spec, self.styles, spec['текст'])
                self.q.put((k, im.crop(box), None))
            except Exception as ex:                                   # noqa: BLE001
                self.q.put((k, None, str(ex)))

    def _poll(self):
        if not self.alive or not self.winfo_exists():
            return
        try:
            while True:
                k, im, err = self.q.get_nowait()
                pic = self.cards[k][1]
                if im is None:
                    pic.configure(text=f'не вдалося: {err}'[:60])
                    continue
                self.photos[k] = self._photo(im)
                pic.configure(image=self.photos[k], text='')
        except queue.Empty:
            pass
        self.after(50, self._poll)

    def _wheel(self, e):
        self.canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units')
        return 'break'

    def _select(self, n):
        self.cards[self.sel][0].configure(highlightbackground='#221b2b')
        self.sel = n
        self.cards[n][0].configure(highlightbackground='#ff8a00')

    def _take(self):
        if self.scan.winfo_exists() and self.scan.sel is not None:
            self.scan.style.set(self.names[self.sel])
        self._close()

    def _new(self):
        base = self.names[self.sel]
        self._close()
        StyleEditor(self.scan, base, False)

    def _close(self):
        self.alive = False
        self.destroy()


class StyleEditor(tk.Toplevel):
    """Свій стиль перекладача: за основу — вибраний стиль, змінюються шрифт, товщина,
    колір (чи градієнт), обведення, тінь, нахил, поворот, розрядка й квадратність; решта
    ключів основи (сяйво, друге обведення, розтяг…) переходить як є. Прев'ю — на вибраному
    місці картинки. Зберігається в атлас/стилі.мої.json (atl.save_user_style).

    `scan` — вікно, з якого відкрито («Знайти написи» чи «Написи на картинках»): має
    styles, game, name (файл розмітки), style_sample() і styles_changed(chosen)."""

    SAMPLE = 'Приклад Їжак'

    def __init__(self, scan, base, edit):
        super().__init__(scan)
        import copy
        from maryskelter import fontlib
        self.scan, self.edit, self.base = scan, edit, base
        self.st = copy.deepcopy(scan.styles.get(base, {}))
        self.st.pop('мій', None)
        self.fonts = fontlib.font_files()
        self.font_dir = {fn: d for d, fn in self.fonts}
        self.q = queue.Queue()
        self.gen, self.pending, self.photos = 0, None, []
        self.title(('Змінити свій стиль «%s»' if edit else 'Новий стиль на основі «%s»') % base)
        self.geometry('900x720')
        self._build()
        self._changed(0)
        self.after(60, self._poll)

    # ------------------------------------------------------------------ вигляд
    def _build(self):
        st = self.st
        f = ttk.Frame(self, padding=10)
        f.pack(side='left', fill='y')
        r = 0

        def row(label, widget):
            nonlocal r
            ttk.Label(f, text=label).grid(row=r, column=0, sticky='w', pady=3)
            widget.grid(row=r, column=1, sticky='w', pady=3)
            r += 1

        self.name = tk.StringVar(value=self.base if self.edit else '')
        e = ttk.Entry(f, textvariable=self.name, width=30)
        if self.edit:
            e.state(['disabled'])
        row('Назва:', e)
        # опис основи для нового стилю не годиться («Назва району на карті…» — вже не про нього)
        self.desc = tk.StringVar(value=st.get('опис', '') if self.edit else '')
        if not self.edit:
            self.st.pop('опис', None)
        row('Опис:', ttk.Entry(f, textvariable=self.desc, width=30))
        # група — щоб свої стилі не губились у списку; можна вписати нову
        groups = [g for g, _ns in atl.style_groups(self.scan.styles,
                                                    atl.game_styles(self.scan.styles, self.scan.game))]
        if atl.MY_GROUP not in groups:
            groups.append(atl.MY_GROUP)
        self.group = tk.StringVar(value=(st.get('група') or atl.MY_GROUP) if self.edit else atl.MY_GROUP)
        row('Група:', ttk.Combobox(f, textvariable=self.group, values=groups, width=28))

        self.font = tk.StringVar(value=st.get('шрифт', ''))
        cb = ttk.Combobox(f, textvariable=self.font, values=[fn for _d, fn in self.fonts],
                          state='readonly', width=34)
        cb.bind('<<ComboboxSelected>>', lambda e: self._font_changed())
        row('Шрифт:', cb)
        var = st.get('варіація')
        self.weight = tk.DoubleVar(value=var.get('wght', 700) if isinstance(var, dict) else 700)
        self.weight_touched = False
        self.w_scale = ttk.Scale(f, from_=100, to=900, variable=self.weight, length=200,
                                 command=lambda _v: self._touch_weight())
        row('Товщина:', self.w_scale)

        fill = st.get('заливка', '#ffffff')
        self.fill1 = tk.StringVar(value=fill[0] if isinstance(fill, list) else fill)
        self.fill2 = tk.StringVar(value=fill[-1] if isinstance(fill, list) else fill)
        self.grad = tk.BooleanVar(value=isinstance(fill, list))
        fr = ttk.Frame(f)
        self._color_btn(fr, self.fill1).pack(side='left')
        ttk.Checkbutton(fr, text='градієнт до', variable=self.grad,
                        command=self._changed).pack(side='left', padx=6)
        self._color_btn(fr, self.fill2).pack(side='left')
        row('Колір літер:', fr)

        ob = st.get('обведення') or {}
        self.ob_on = tk.BooleanVar(value=bool(ob))
        self.ob_col = tk.StringVar(value=ob.get('колір', '#2e0010'))
        self.ob_w = tk.DoubleVar(value=ob.get('товщина', 2))
        fr = ttk.Frame(f)
        ttk.Checkbutton(fr, variable=self.ob_on, command=self._changed).pack(side='left')
        self._color_btn(fr, self.ob_col).pack(side='left')
        ttk.Label(fr, text=' товщина').pack(side='left')
        ttk.Spinbox(fr, from_=0.5, to=10, increment=0.5, textvariable=self.ob_w, width=5,
                    command=self._changed).pack(side='left', padx=4)
        row('Обведення:', fr)

        sh = st.get('тінь') or {}
        self.sh_on = tk.BooleanVar(value=bool(sh))
        self.sh_col = tk.StringVar(value=(sh.get('колір') or '#000000c0'))
        dx, dy = (sh.get('зсув') or [2, 3])[:2]
        self.sh_dx, self.sh_dy = tk.DoubleVar(value=dx), tk.DoubleVar(value=dy)
        self.sh_blur = tk.DoubleVar(value=sh.get('розмиття', 0.5))
        fr = ttk.Frame(f)
        ttk.Checkbutton(fr, variable=self.sh_on, command=self._changed).pack(side='left')
        self._color_btn(fr, self.sh_col).pack(side='left')
        for lab, v, lo, hi in ((' зсув x', self.sh_dx, -10, 10), (' y', self.sh_dy, -10, 10),
                               (' розмиття', self.sh_blur, 0, 6)):
            ttk.Label(fr, text=lab).pack(side='left')
            ttk.Spinbox(fr, from_=lo, to=hi, increment=0.5, textvariable=v, width=4,
                        command=self._changed).pack(side='left')
        row('Тінь:', fr)

        self.sliders = {}
        for key, label, lo, hi, dflt in (('нахил', 'Нахил:', -0.2, 0.5, 0), ('поворот', 'Поворот:', -30, 30, 0),
                                         ('розрядка', 'Розрядка:', -0.1, 0.4, 0),
                                         ('квадратність', 'Квадратність:', 0, 1, 0)):
            v = tk.DoubleVar(value=st.get(key, dflt))
            fr = ttk.Frame(f)
            lab = ttk.Label(fr, width=6)
            ttk.Scale(fr, from_=lo, to=hi, variable=v, length=180,
                      command=lambda _v, v=v, lab=lab: (lab.configure(text=f'{v.get():.2f}'),
                                                         self._changed())).pack(side='left')
            lab.configure(text=f'{v.get():.2f}')
            lab.pack(side='left', padx=4)
            row(label, fr)
            self.sliders[key] = v
        for v in (self.ob_w, self.sh_dx, self.sh_dy, self.sh_blur, self.fill1, self.fill2,
                  self.ob_col, self.sh_col):
            v.trace_add('write', lambda *_: self._changed())

        bt = ttk.Frame(f)
        bt.grid(row=r, column=0, columnspan=2, sticky='w', pady=(14, 0))
        ttk.Button(bt, text='Зберегти стиль', style='Accent.TButton', command=self._save).pack(side='left')
        if self.edit:
            ttk.Button(bt, text='Видалити стиль', command=self._delete).pack(side='left', padx=6)
        ttk.Button(bt, text='Скасувати', command=self.destroy).pack(side='left', padx=6)
        self.msg = tk.StringVar()
        ttk.Label(f, textvariable=self.msg, wraplength=380, foreground='#b36b00').grid(
            row=r + 1, column=0, columnspan=2, sticky='w', pady=(8, 0))
        self._font_changed(init=True)

        pv = ttk.Frame(self, padding=10)
        pv.pack(side='left', fill='both', expand=True)
        self.prev = []
        for title in ('Оригінал', 'Цим стилем — англійською', 'Цим стилем — українською'):
            ttk.Label(pv, text=title, style='Hint.TLabel').pack(anchor='w')
            lab = tk.Label(pv, bg='#181020', bd=0)
            lab.pack(anchor='w', pady=(0, 10))
            self.prev.append(lab)

    def _color_btn(self, parent, var):
        from tkinter import colorchooser
        b = tk.Label(parent, width=3, relief='solid', bd=1, cursor='hand2')

        def paint(*_):
            try:
                b.configure(bg=var.get()[:7])
            except tk.TclError:
                pass

        def pick(_e):
            res = colorchooser.askcolor(color=var.get()[:7], parent=self)
            if res and res[1]:
                var.set(res[1] + var.get()[7:9])            # прозорість (#rrggbbaa) — як була
        b.bind('<Button-1>', pick)
        var.trace_add('write', paint)
        paint()
        return b

    def _font_changed(self, init=False):
        from maryskelter import fontlib
        fn = self.font.get()
        d = self.font_dir.get(fn)
        has_w = bool(d and 'Weight' in fontlib.axes(os.path.join(d, fn)))
        self.w_scale.state(['!disabled'] if has_w else ['disabled'])
        if not init:
            self.weight_touched = True
            self._changed()

    def _touch_weight(self):
        self.weight_touched = True
        self._changed()

    # ------------------------------------------------------------------ стиль
    def style(self):
        from maryskelter import fontlib
        st = dict(self.st)
        fn = self.font.get()
        d = self.font_dir.get(fn)
        if d:
            fontlib.register(d, fn)
        st['шрифт'] = fn
        if self.weight_touched:
            var = fontlib.variation(os.path.join(d, fn), self.weight.get()) if d else None
            if var:
                st['варіація'] = var
            else:
                st.pop('варіація', None)
        st['заливка'] = [self.fill1.get(), self.fill2.get()] if self.grad.get() else self.fill1.get()
        try:
            if self.ob_on.get():
                st['обведення'] = {'колір': self.ob_col.get(), 'товщина': float(self.ob_w.get())}
            else:
                st.pop('обведення', None)
            if self.sh_on.get():
                st['тінь'] = {'колір': self.sh_col.get(),
                              'зсув': [float(self.sh_dx.get()), float(self.sh_dy.get())],
                              'розмиття': float(self.sh_blur.get())}
            else:
                st.pop('тінь', None)
        except (tk.TclError, ValueError):
            pass
        for key, v in self.sliders.items():
            val = round(v.get(), 3)
            if abs(val) < 1e-3 and key not in self.st:
                st.pop(key, None)
            else:
                st[key] = val
        if self.desc.get().strip():
            st['опис'] = self.desc.get().strip()
        st['група'] = self.group.get().strip() or atl.MY_GROUP
        st['гра'] = self.scan.game
        return st

    def _changed(self, delay=250):
        if self.pending:
            self.after_cancel(self.pending)
        self.pending = self.after(delay, self._render)

    def _render(self):
        self.pending = None
        sample = self.scan.style_sample()
        if sample is None:
            return
        self.gen += 1
        img, box, spec, texts = sample
        gen, st = self.gen, self.style()
        styles = dict(self.scan.styles, __пробний=st)
        spec = dict(spec, стиль='__пробний')
        texts = [t or self.SAMPLE for t in texts]

        def work():
            out = [img.crop(box)]
            warn = []
            for text in texts:
                try:
                    im = img.copy()
                    warn += atl.draw(im, box, spec, styles, text)
                    out.append(im.crop(box))
                except Exception as ex:                               # noqa: BLE001
                    out.append(None)
                    warn.append(f'не вдалося: {ex}')
            self.q.put((gen, out, warn))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                gen, ims, warn = self.q.get_nowait()
                if gen != self.gen:
                    continue
                self.photos = []
                for lab, im in zip(self.prev, ims):
                    if im is None:
                        lab.configure(image='')
                        continue
                    bg = Image.new('RGBA', im.size, BG)
                    bg.alpha_composite(im)
                    s = min(3.0, 460 / max(1, bg.width))
                    bg = bg.resize((max(1, int(bg.width * s)), max(1, int(bg.height * s))), Image.LANCZOS)
                    ph = ImageTk.PhotoImage(bg.convert('RGB'))
                    self.photos.append(ph)
                    lab.configure(image=ph)
                self.msg.set('\n'.join(dict.fromkeys(w for w in warn if 'не вдалося' in w)))
        except queue.Empty:
            pass
        self.after(60, self._poll)

    # ------------------------------------------------------------------ збереження
    def _save(self):
        name = self.name.get().strip()
        if not name:
            self.msg.set('Дай стилю назву.')
            return
        if not self.edit and name in self.scan.styles:
            self.msg.set('Стиль з такою назвою вже є — вибери іншу назву.')
            return
        if name.startswith('_'):
            self.msg.set('Назва не може починатися з «_».')
            return
        from maryskelter import fontlib
        st = self.style()
        fontlib.adopt(st['шрифт'])                   # шрифт з бібліотеки — у атлас/шрифти/
        atl.save_user_style(name, st)
        self.scan.styles_changed(chosen=name)
        self.destroy()

    def _users(self, name):
        return sum(1 for m in atl.load_marks(self.scan.name).values()
                   for s in m.get('кадри', {}).values() if s.get('стиль') == name)

    def _delete(self):
        name = self.base
        n = self._users(name)
        if n:
            self.msg.set(f'Стиль ужито в написах: {n}. Спершу вибери для них інший стиль.')
            return
        if not messagebox.askyesno('Видалити стиль', f'Видалити свій стиль «{name}»?', parent=self):
            return
        atl.save_user_style(name, None)
        self.scan.styles_changed(chosen='')
        self.destroy()
