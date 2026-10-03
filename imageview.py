# -*- coding: utf-8 -*-
"""Перегляд знімка з наближенням (з 2.11, прохання перекладача: «розгледіти написи»).

Спільне вікно для «Нагадувань» і «Гри на екрані»: коліщатко — наближення відносно
курсора, перетягування лівою кнопкою — зсув, «Вписати» / «100%», «Відкрити у Windows»
(програма перегляду фото) і «Показати в теці». Малюється лише видима частина
(обрізати -> масштабувати під полотно), тож і ×8 знімка 1920×1080 не займає сотні МБ.
"""
import os, subprocess
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk


class ImageView(tk.Toplevel):
    def __init__(self, parent, img, title='Знімок', path=None, boxes=(), colors=None):
        """img — PIL Image (або шлях); boxes — [(x0, y0, x1, y1, колір)] у пікселях знімка."""
        super().__init__(parent)
        if isinstance(img, str):
            path = path or img
            img = Image.open(img)
        self.img = img.convert('RGB')
        if boxes:
            self.img = self.img.copy()
            d = ImageDraw.Draw(self.img)
            for x0, y0, x1, y1, col in boxes:
                d.rectangle([x0 - 3, y0 - 3, x1 + 3, y1 + 3], outline=col, width=3)
        self.path = path
        c = colors or {}
        self.title(title)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f'{min(1400, sw - 120)}x{min(900, sh - 160)}')
        self.configure(bg=c.get('bg', '#1c1c1c'))
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill='x')
        ttk.Button(bar, text='Вписати', command=self.fit).pack(side='left')
        ttk.Button(bar, text='100%', command=lambda: self.zoom_to(1.0)).pack(side='left', padx=6)
        self.info = tk.StringVar()
        ttk.Label(bar, textvariable=self.info).pack(side='left', padx=8)
        if path:
            ttk.Button(bar, text='Показати в теці', command=self.reveal).pack(side='right')
            ttk.Button(bar, text='Відкрити у Windows', command=self.open_ext).pack(side='right', padx=6)
        self.cv = tk.Canvas(self, bg=c.get('logbg', '#202020'), highlightthickness=0, bd=0, cursor='fleur')
        self.cv.pack(fill='both', expand=True)
        self.z, self.ox, self.oy = 1.0, 0.0, 0.0
        self._fitted = False
        self.cv.bind('<Configure>', lambda e: self.fit() if not self._fitted else self.draw())
        self.cv.bind('<MouseWheel>', self._wheel)
        self.cv.bind('<ButtonPress-1>', self._press)
        self.cv.bind('<B1-Motion>', self._drag)
        self.cv.bind('<Double-1>', lambda e: self.fit())
        for key, f in (('<plus>', 1.25), ('<equal>', 1.25), ('<minus>', 0.8), ('<KP_Add>', 1.25),
                       ('<KP_Subtract>', 0.8)):
            self.bind(key, lambda e, f=f: self._zoom_at(f, self.cv.winfo_width() / 2, self.cv.winfo_height() / 2))
        self.bind('<Escape>', lambda e: self.destroy())
        self.focus_set()

    # ------------------------------------------------------------ масштаб і зсув
    def fit(self):
        cw, ch = max(1, self.cv.winfo_width()), max(1, self.cv.winfo_height())
        self.z = min(cw / self.img.width, ch / self.img.height)
        self.ox = (self.img.width - cw / self.z) / 2
        self.oy = (self.img.height - ch / self.z) / 2
        self._fitted = True
        self.draw()

    def zoom_to(self, z):
        self._zoom_at(z / self.z, self.cv.winfo_width() / 2, self.cv.winfo_height() / 2)

    def _zoom_at(self, f, x, y):
        z = max(0.05, min(8.0, self.z * f))
        ix, iy = self.ox + x / self.z, self.oy + y / self.z     # точка знімка під курсором лишається на місці
        self.z = z
        self.ox, self.oy = ix - x / z, iy - y / z
        self.draw()

    def _wheel(self, ev):
        self._zoom_at(1.25 if ev.delta > 0 else 0.8, ev.x, ev.y)

    def _press(self, ev):
        self._from = (ev.x, ev.y, self.ox, self.oy)

    def _drag(self, ev):
        x, y, ox, oy = self._from
        self.ox, self.oy = ox - (ev.x - x) / self.z, oy - (ev.y - y) / self.z
        self.draw()

    def draw(self):
        cw, ch = max(1, self.cv.winfo_width()), max(1, self.cv.winfo_height())
        W, H = self.img.size
        # видима частина знімка (у його пікселях), обрізана по краях
        x0, y0 = max(0.0, self.ox), max(0.0, self.oy)
        x1, y1 = min(W, self.ox + cw / self.z), min(H, self.oy + ch / self.z)
        self.cv.delete('all')
        self.info.set(f'{round(self.z * 100)}%  ·  коліщатко — ближче/далі, тягни мишею, подвійний клік — вписати')
        if x1 <= x0 or y1 <= y0:
            return
        crop = self.img.crop((int(x0), int(y0), int(x1 + 0.999), int(y1 + 0.999)))
        dw, dh = max(1, round(crop.width * self.z)), max(1, round(crop.height * self.z))
        crop = crop.resize((dw, dh), Image.NEAREST if self.z >= 2 else Image.BILINEAR)
        self.photo = ImageTk.PhotoImage(crop)
        self.cv.create_image(round((int(x0) - self.ox) * self.z), round((int(y0) - self.oy) * self.z),
                             image=self.photo, anchor='nw')

    # ------------------------------------------------------------ файл
    def open_ext(self):
        try:
            os.startfile(self.path)
        except OSError:
            pass

    def reveal(self):
        try:
            subprocess.Popen(['explorer', '/select,', os.path.normpath(self.path)])
        except OSError:
            pass
