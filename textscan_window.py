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
import os, queue, threading
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageTk

import textscan
from maryskelter import atlas as atl

ZOOMS = ('50%', '100%', '200%')
HANDLE = 6                      # «ручка» рамки, пікселі екрана
PAD_AREA = 3                    # область стирання = рамка напису + запас
PAD_FRAME = 12                  # кадр = рамка напису + місце під довший переклад
BG = (24, 16, 32, 255)
MODES = {'рядки': 'кнопка / плашка з однорідним тлом', 'прозорий': 'напис на прозорому'}


class TextScan(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        work, _xl, _out, bk = app.dirs()
        self.game = app.cur['game']
        if self.game not in textscan.MARKS:
            self.destroy()
            raise RuntimeError('Пошук написів на картинках є для Mary Skelter і Neptunia.')
        self.name = textscan.MARKS[self.game]
        self.tex = textscan.Textures(self.game, bk, app.root_dir())
        self.styles = atl.load_json('стилі.json')
        nep = self.game == 'nep'
        self.style_names = sorted(k for k in self.styles if not k.startswith('_') and k.startswith('неп-') == nep)
        self.found = textscan.load_found(self.game)
        self.q = queue.Queue()
        self.cur = None                 # (джерело, текстура)
        self.img = None                 # RGBA поточної текстури
        self.frames = {}
        self.cands = []                 # [{текст, рамка, стан}]: стан '' | 'вибрано'
        self.sel = None
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
        self._fill()
        self.after(60, self._poll)
        self.bind('<Delete>', lambda e: self._reject())

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
        ttk.Label(bar, text='Масштаб:').pack(side='left')
        self.zoom = tk.StringVar(value='100%')
        z = ttk.Combobox(bar, textvariable=self.zoom, values=ZOOMS, state='readonly', width=6)
        z.pack(side='left', padx=4)
        z.bind('<<ComboboxSelected>>', lambda e: self._redraw(True))
        ttk.Label(bar, text='Зелене — вже розмічено, жовте — знайдено. Порожнє місце: потягни мишею — '
                            'нова рамка.', style='Hint.TLabel').pack(side='left', padx=8)
        cv = ttk.Frame(mid)
        cv.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(cv, bg='#181020', highlightthickness=0, cursor='crosshair')
        xs = ttk.Scrollbar(cv, orient='horizontal', command=self.canvas.xview)
        ys = ttk.Scrollbar(cv, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xs.set, yscrollcommand=ys.set)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        xs.grid(row=1, column=0, sticky='ew')
        cv.rowconfigure(0, weight=1)
        cv.columnconfigure(0, weight=1)
        self.canvas.bind('<ButtonPress-1>', self._press)
        self.canvas.bind('<B1-Motion>', self._motion)
        self.canvas.bind('<ButtonRelease-1>', self._release)
        self.canvas.bind('<MouseWheel>', lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units'))
        self.canvas.bind('<Shift-MouseWheel>',
                         lambda e: self.canvas.xview_scroll(-1 if e.delta > 0 else 1, 'units'))

        right = ttk.Frame(body, padding=(10, 0, 0, 0))
        body.add(right, weight=1)
        ttk.Label(right, text='Вибраний напис', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        ttk.Label(right, text='Текст (англійською, як на картинці):').pack(anchor='w', pady=(8, 0))
        self.text = tk.StringVar()
        ttk.Entry(right, textvariable=self.text).pack(fill='x')
        ttk.Label(right, text='Стиль (яким малювати переклад):').pack(anchor='w', pady=(8, 0))
        self.style = tk.StringVar()
        cb = ttk.Combobox(right, textvariable=self.style, values=self.style_names, state='readonly')
        cb.pack(fill='x')
        ttk.Label(right, text='Тло під написом (як стерти старий):').pack(anchor='w', pady=(8, 0))
        self.mode = tk.StringVar(value='рядки')
        for m, d in MODES.items():
            ttk.Radiobutton(right, text=f'{m} — {d}', value=m, variable=self.mode,
                            command=self._changed).pack(anchor='w')
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
        ttk.Label(right, text='Прийняте потрапляє в розмітку перекладача (атлас, файл «.мої.json»). '
                              'Після «1. Дістати текст з гри» напис з\'явиться в книзі написів і в '
                              'редакторі — там його й перекладай. Надішли цей файл власнику програми, '
                              'щоб розмітка не загубилась.',
                  style='Hint.TLabel', wraplength=330).pack(anchor='w', pady=(12, 0))
        self._enable(False)

    def _enable(self, on):
        for b in (self.b_ok, self.b_no):
            b.state(['!disabled'] if on else ['disabled'])

    # ------------------------------------------------------------------ список
    def _marks(self):
        return atl.load_marks(self.name)

    def _rejected(self, key):
        return atl.load_user(self.name).get('_відхилено', {}).get(key, [])

    def _marked_boxes(self, src, stem, frames, marks=None):
        mk = (marks if marks is not None else self._marks()).get(src)
        if not mk:
            return []
        if stem and mk.get('текстура') and mk['текстура'].lower() != stem.lower():
            return []
        out = []
        for i, spec in mk.get('кадри', {}).items():
            b = atl.box_of(i, spec, frames)
            if b:
                out.append(list(b))
        return out

    def _fill(self):
        self.tree.delete(*self.tree.get_children())
        marks = self._marks()
        srcs = set(self.found)
        if self.all_var.get():
            srcs |= set(self._all_srcs())
        srcs |= set(marks) if self.all_var.get() else set()
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
                if n or self.all_var.get():
                    rows.append((src, stem, n, done))
        rows.sort(key=lambda r: (-r[2], r[0]))
        for src, stem, n, done in rows:
            label = src.split('/', 1)[-1] + (f' [{stem}]' if stem and stem.lower() not in src.lower() else '')
            self.tree.insert('', 'end', iid=f'{src}|{stem}', text=label, values=(n or '', done or ''))
        if not rows:
            self.status.set('Натисни «Шукати написи», щоб програма знайшла кандидатів, або постав '
                            '«Показати всі текстури» й розмічай вручну.')
        else:
            self.status.set(f'Текстур у списку: {len(rows)}.')

    def _all_srcs(self):
        if not hasattr(self, '_srcs'):
            self._srcs = self.tex.list()
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
        if not s:
            return
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
        self.done = self._marked_boxes(src, part[0] if self.multi else '', self.frames)
        self.cands = [dict(c) for c in (self.found.get(src) or {}).get(part[0], [])
                      if not any(textscan.overlap(c['рамка'], r) > 0.5 for r in rej)
                      and not any(textscan.overlap(c['рамка'], d) > 0.5 for d in self.done)]
        self.sel = None
        self._select(None)
        self._redraw(True)

    def _z(self):
        return {'50%': 0.5, '100%': 1.0, '200%': 2.0}[self.zoom.get()]

    def _redraw(self, image=False):
        if self.img is None:
            return
        z = self._z()
        c = self.canvas
        if image:
            bg = Image.new('RGBA', self.img.size, BG)
            bg.alpha_composite(self.img)
            im = bg.convert('RGB')
            if z != 1.0:
                im = im.resize((max(1, int(im.width * z)), max(1, int(im.height * z))),
                               Image.LANCZOS if z < 1 else Image.NEAREST)
            self.photo = ImageTk.PhotoImage(im)
            c.delete('all')
            c.create_image(0, 0, image=self.photo, anchor='nw', tags='img')
            c.configure(scrollregion=(0, 0, im.width, im.height))
        c.delete('box')
        for b in self.done:
            c.create_rectangle(*[v * z for v in b], outline='#3ddc84', width=2, tags='box')
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
        self.drag = ('new', None, None, x, y)

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
        if self.drag and self.drag[0] == 'new':            # клік по порожньому — зняти вибір
            self.drag = None
            self._select(None)
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

    def _guess(self):
        """Стиль і тло — за кольором літер і прозорістю навколо напису."""
        cd = self.cands[self.sel]
        x0, y0, x1, y1 = cd['рамка']
        if x1 - x0 < 2 or y1 - y0 < 2:
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
        """Кадр розмітки для вибраної рамки: (ключ, spec) у форматі написи.json."""
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
        if self.style.get():
            self.cands[self.sel]['стиль'] = self.style.get()
        if self.pending:
            self.after_cancel(self.pending)
        self.pending = self.after(250, self._render)

    def _render(self):
        self.pending = None
        if self.sel is None or not self.style.get():
            return
        self.gen += 1
        gen, img, (key, spec) = self.gen, self.img, self._spec()
        styles = self.styles

        def work():
            box = tuple(spec['рамка'])
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
        key, spec = self._spec()
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
        self.done.append(spec['рамка'])
        del self.cands[self.sel]
        self._select(None)
        self._update_row()
        self.status.set(f'Прийнято: «{spec["текст"]}». Після «1» він з\'явиться в книзі написів.')

    def _reject(self):
        if self.sel is None:
            return
        cd = self.cands[self.sel]
        if not cd.get('ручна'):
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
