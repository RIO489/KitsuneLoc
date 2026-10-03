# -*- coding: utf-8 -*-
"""Теми вигляду програми (з 3.0): палітра + як малювати віджети + прикраси.

Тема — дані (THEMES): кольори, основа ('light'/'dark' — заголовок вікна, меню), рушій:
  'sv'   — Sun Valley (sv-ttk, як було до 3.0): «Світла», «Темна»;
  'clam' — вбудована тема ttk, усі кольори наші (кнопки, поля, списки, смуги прокрутки).
           Вона ще й швидша за sv-ttk: sv-ttk малює рамки картинками;
і прикраси ('deco'): зоряне тло в проміжках між панелями, світні рамки навколо панелей,
персонаж гри в шапці редактора.

Прозорості в Tk немає: віджет завжди суцільний. Тому «небо» — картинка в окремій мітці під
іншими віджетами рамки (Deco.frame): її видно лише там, де віджетів немає (відступи між
панелями). Кожна прикрашена рамка показує свій шматок ОДНОГО малюнка вікна — виглядає
суцільно. Малюнок — після того, як зміна розміру вікна зупинилась (як усе в редакторі).
"""
import math, random
import tkinter as tk
from tkinter import ttk

# кольори, які читає решта програми: bg fg panel logbg logfg dim err warn ok accent link
# (+ для clam: field — поля вводу, button, button_hi, border, sel — виділення, head — заголовки
#  колонок; для прикрас: sky (верх, низ), glow — сяйво рамок, star — зірки)
THEMES = {
    'light': dict(title='Світла', base='light', engine='sv',
                  bg='#fafafa', fg='#1c1c1c', panel='#ffffff', logbg='#ffffff',
                  logfg='#1c1c1c', dim='#6a6a6a', err='#b00020', warn='#8a6100',
                  ok='#0a6b2e', accent='#005fb8', link='#005fb8'),
    'dark': dict(title='Темна', base='dark', engine='sv',
                 bg='#1c1c1c', fg='#e6e6e6', panel='#2b2b2b', logbg='#202020',
                 logfg='#dfe3e6', dim='#8b949e', err='#ff7b72', warn='#e3b341',
                 ok='#56d364', accent='#57c8ff', link='#57c8ff'),
    'stars': dict(title='Зоряна ніч', base='dark', engine='clam',
                  bg='#0b0c2a', fg='#ebe8ff', panel='#0f1238', logbg='#0d1034', logfg='#ddd8ff',
                  dim='#9590c8', err='#ff7194', warn='#ffd166', ok='#7ee0a1',
                  accent='#9b74ff', link='#b59bff', field='#141747', button='#1a1d55',
                  button_hi='#2a2c80', border='#5a46d8', sel='#4a2fb5', head='#161a4e',
                  deco=dict(sky=('#0a0b2c', '#1b0f4a'), glow='#8f63ff', star='#e9e4ff',
                            nebula='#6a3cff', portrait=True)),
    'calm': dict(title='Спокійна темна', base='dark', engine='clam',
                 bg='#16171f', fg='#e4e4ee', panel='#1d1e29', logbg='#1a1b25', logfg='#d8d8e6',
                 dim='#8d8fa6', err='#f2788f', warn='#e8c06a', ok='#7fd49b',
                 accent='#8f84f0', link='#a79ff5', field='#22233a', button='#262840',
                 button_hi='#33355a', border='#3a3c58', sel='#3d3a7a', head='#22243a'),
}
DEFAULT = 'light'


def get(name):
    return THEMES.get(name) or THEMES[DEFAULT]


def names():
    return list(THEMES)


def by_title(title):
    return next((k for k, t in THEMES.items() if t['title'] == title), DEFAULT)


def is_dark(name):
    return get(name)['base'] == 'dark'


# ============================================================ стилі ttk
def apply(root, name):
    """Тема -> стилі ttk усієї програми (діє на всі вікна) і кольори меню/списків."""
    t = get(name)
    st = ttk.Style(root)
    if t['engine'] == 'sv':
        try:
            import sv_ttk
            sv_ttk.set_theme(t['base'])
        except ImportError:
            _clam(root, st, t)
        _menus(root, None if t['base'] == 'light' else t)
    else:
        _clam(root, st, t)
        _menus(root, t)
    st.configure('Hint.TLabel', foreground=t['dim'], font=('Segoe UI', 8))
    return t


def _menus(root, t):
    """Меню й випадний список Combobox — віджети tk, не ttk: кольори через option."""
    if t is None:
        bg, fg, sel, self_fg = 'SystemMenu', 'SystemMenuText', 'SystemHighlight', 'SystemHighlightText'
    else:
        bg, fg, sel, self_fg = t.get('field', t['panel']), t['fg'], t.get('sel', t['accent']), '#ffffff'
    for pat in ('*Menu', '*TCombobox*Listbox'):
        root.option_add(pat + '.background', bg)
        root.option_add(pat + '.foreground', fg)
        root.option_add(pat + '.selectBackground', sel)
        root.option_add(pat + '.selectForeground', self_fg)
    root.option_add('*Menu.activeBackground', sel)
    root.option_add('*Menu.activeForeground', self_fg)


def _clam(root, st, t):
    st.theme_use('clam')
    bg, fg, panel = t['bg'], t['fg'], t['panel']
    field, btn, hi = t.get('field', panel), t.get('button', panel), t.get('button_hi', panel)
    border, sel, dim, acc = t.get('border', '#555555'), t.get('sel', t['accent']), t['dim'], t['accent']
    font = ('Segoe UI', 10)
    st.configure('.', background=bg, foreground=fg, fieldbackground=field, bordercolor=border,
                 darkcolor=bg, lightcolor=bg, troughcolor=panel, focuscolor=acc,
                 selectbackground=sel, selectforeground='#ffffff', insertcolor=fg,
                 arrowcolor=fg, font=font)
    # clam за замовчуванням світлішає під мишею й у вимкненому стані (#eeebe7, #dcdad5) — у
    # темній темі це білі плями
    st.map('.', foreground=[('disabled', dim)], background=[('disabled', bg), ('active', bg)],
           selectbackground=[('!focus', sel)], selectforeground=[('!focus', '#ffffff')])
    st.configure('TFrame', background=bg)
    st.configure('TLabel', background=bg, foreground=fg)
    st.configure('TLabelframe', background=bg, bordercolor=border, lightcolor=border, darkcolor=border)
    st.configure('TLabelframe.Label', background=bg, foreground=t.get('link', acc), font=('Segoe UI', 10, 'bold'))
    st.configure('TButton', background=btn, foreground=fg, bordercolor=border, lightcolor=btn,
                 darkcolor=btn, padding=(10, 4), focuscolor=btn)
    st.map('TButton', background=[('disabled', bg), ('pressed', sel), ('active', hi)],
           bordercolor=[('active', acc), ('focus', acc)], lightcolor=[('active', hi)],
           darkcolor=[('active', hi)], foreground=[('disabled', dim)])
    st.configure('Accent.TButton', background=sel, foreground='#ffffff', bordercolor=acc,
                 lightcolor=sel, darkcolor=sel)
    st.map('Accent.TButton', background=[('disabled', bg), ('pressed', btn), ('active', acc)],
           lightcolor=[('active', acc)], darkcolor=[('active', acc)], foreground=[('disabled', dim)])
    for w in ('TCheckbutton', 'TRadiobutton'):
        st.configure(w, background=bg, foreground=fg, indicatorbackground=field,
                     indicatorforeground=fg, bordercolor=border, focuscolor=bg)
        st.map(w, background=[('active', bg)], indicatorbackground=[('selected', sel), ('active', hi)],
               indicatorforeground=[('selected', '#ffffff')])
    st.configure('TEntry', fieldbackground=field, foreground=fg, bordercolor=border,
                 lightcolor=field, darkcolor=field, insertcolor=fg)
    st.map('TEntry', bordercolor=[('focus', acc)], lightcolor=[('focus', acc)])
    for w in ('TCombobox', 'TSpinbox'):
        st.configure(w, fieldbackground=field, background=btn, foreground=fg, bordercolor=border,
                     lightcolor=field, darkcolor=field, arrowcolor=fg, insertcolor=fg)
        st.map(w, fieldbackground=[('readonly', field)], foreground=[('readonly', fg)],
               bordercolor=[('focus', acc)], background=[('active', hi)],
               selectbackground=[('readonly', field)], selectforeground=[('readonly', fg)])
    st.configure('Treeview', background=panel, fieldbackground=panel, foreground=fg,
                 bordercolor=border, lightcolor=panel, darkcolor=panel)
    st.map('Treeview', background=[('selected', sel)], foreground=[('selected', '#ffffff')])
    st.configure('Treeview.Heading', background=t.get('head', btn), foreground=t.get('link', fg),
                 bordercolor=border, lightcolor=t.get('head', btn), darkcolor=t.get('head', btn),
                 relief='flat', font=('Segoe UI', 9, 'bold'))
    st.map('Treeview.Heading', background=[('active', hi)])
    for w in ('Vertical.TScrollbar', 'Horizontal.TScrollbar'):
        st.configure(w, background=btn, troughcolor=bg, bordercolor=bg, lightcolor=btn,
                     darkcolor=btn, arrowcolor=dim, gripcount=0)
        st.map(w, background=[('active', hi)])
    st.configure('TProgressbar', background=acc, troughcolor=panel, bordercolor=border,
                 lightcolor=acc, darkcolor=sel)
    st.configure('TPanedwindow', background=bg)
    st.configure('Sash', sashthickness=6, background=bg, bordercolor=bg, lightcolor=bg, darkcolor=bg)
    st.configure('TNotebook', background=bg, bordercolor=border)
    st.configure('TNotebook.Tab', background=btn, foreground=fg, bordercolor=border, padding=(10, 3))
    st.map('TNotebook.Tab', background=[('selected', sel)], foreground=[('selected', '#ffffff')])
    st.configure('TSeparator', background=border)


# ============================================================ прикраси
def _hex(c):
    c = c.lstrip('#')
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def sky(w, h, deco, seed=7):
    """Нічне небо w×h: градієнт, туманність, зірки й іскри-хрестики (PIL RGB)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    top, bottom = (np.array(_hex(c), dtype=np.float32) for c in deco['sky'])
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :, None]
    k = np.clip(0.55 * y + 0.45 * x, 0, 1)
    img = top * (1 - k) + bottom * k
    rnd = np.random.default_rng(seed)
    # туманність: великий згладжений шум, підфарбований кольором сяйва
    # (шум і розмиття — у восьмеро меншому розмірі: на повному розмиття — сотні мс)
    small = Image.fromarray((rnd.random((max(2, h // 90), max(2, w // 90))) * 255).astype('uint8'))
    mid = small.resize((max(2, w // 8), max(2, h // 8)), Image.BICUBIC).filter(ImageFilter.GaussianBlur(5))
    neb = np.asarray(mid.resize((w, h), Image.BICUBIC), dtype=np.float32)[:, :, None] / 255.0
    neb = np.clip((neb - 0.45) * 2.2, 0, 1) * 0.45
    img = img * (1 - neb) + np.array(_hex(deco['nebula']), dtype=np.float32) * neb
    im = Image.fromarray(np.clip(img, 0, 255).astype('uint8'), 'RGB')
    d = ImageDraw.Draw(im)
    star = _hex(deco['star'])
    r = random.Random(seed)
    for _ in range(w * h // 1800):                  # дрібні зорі
        px, py = r.randrange(w), r.randrange(h)
        a = r.random() ** 2
        c = tuple(int(bg * (1 - a) + s * a) for bg, s in zip(im.getpixel((px, py)), star))
        d.point((px, py), fill=c)
    glow = Image.new('RGB', (w, h))
    gd = ImageDraw.Draw(glow)
    for _ in range(max(6, w * h // 90000)):         # іскри-хрестики з сяйвом
        px, py, s = r.randrange(w), r.randrange(h), r.randint(4, 11)
        gd.line((px - s, py, px + s, py), fill=star)
        gd.line((px, py - s, px, py + s), fill=star)
        gd.ellipse((px - 2, py - 2, px + 2, py + 2), fill=star)
    soft = glow.filter(ImageFilter.GaussianBlur(3))
    from PIL import ImageChops
    im = ImageChops.add(im, soft)
    im = ImageChops.add(im, glow.point(lambda v: v * 0.8))
    return im


def glow_rect(im, box, color, radius=10, width=2, blur=6):
    """Світна заокруглена рамка на картинці (на місці, PIL)."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    layer = Image.new('RGB', im.size)
    ImageDraw.Draw(layer).rounded_rectangle(box, radius=radius, outline=_hex(color), width=width + 2)
    halo = layer.filter(ImageFilter.GaussianBlur(blur))
    out = ImageChops.add(im, halo)
    sharp = Image.new('RGB', im.size)
    ImageDraw.Draw(sharp).rounded_rectangle(box, radius=radius, outline=tuple(min(255, v + 70) for v in _hex(color)),
                                            width=1)
    out = ImageChops.add(out, sharp)
    im.paste(out)
    return im


class Deco:
    """Зоряне тло й світні рамки для одного вікна. frame(f) — показувати небо у вільних
    місцях рамки f (її відступах) і, якщо glow, світну рамку по її краю (усередині відступу,
    тож рамці треба padding ≥ inset+4)."""

    def __init__(self, win, theme_name):
        self.win = win
        self.frames = []                # (рамка, мітка, glow, inset)
        self.rings = []                 # (віджет, відступ) — світна рамка ЗОВНІ віджета
        self.photos = {}
        self._job = None
        self._size = None
        win.bind('<Configure>', self._later, add='+')
        self.retheme(theme_name)

    @property
    def on(self):
        return bool(self.deco)

    def retheme(self, theme_name):
        """Інша тема: прикраси з'являються / зникають (мітки лишаються, лише ховаються)."""
        self.t = get(theme_name)
        self.deco = self.t.get('deco')
        self._size = None
        for _f, lab, _g, _i in self.frames:
            lab.configure(bg=self.t['bg'], image='')
            if self.deco:
                lab.place(x=0, y=0, relwidth=1, relheight=1)
                lab.lower()
            else:
                lab.place_forget()
        self.photos.clear()
        self._later()

    def frame(self, f, glow=False, inset=4):
        lab = tk.Label(f, bd=0, highlightthickness=0, bg=self.t['bg'])
        if self.deco:
            lab.place(x=0, y=0, relwidth=1, relheight=1)
            lab.lower()
        self.frames.append((f, lab, glow, inset))
        self._later()

    def around(self, w, pad=5):
        """Світна рамка навколо віджета (видно на небі батьківської рамки)."""
        self.rings.append((w, pad))
        self._later()

    def _later(self, _e=None):
        if not self.deco:
            return
        if self._job is not None:
            self.win.after_cancel(self._job)
        self._job = self.win.after(160, self.render)

    def render(self):
        """Один малюнок неба на все вікно + світні рамки -> шматки в мітки рамок."""
        self._job = None
        if not self.deco:
            return
        try:
            if not self.win.winfo_exists():
                return
            self.win.update_idletasks()
            W, H = self.win.winfo_width(), self.win.winfo_height()
        except tk.TclError:
            return
        if W < 50 or H < 50:
            return
        from PIL import ImageTk
        if self._size != (W, H):
            self._sky = sky(W, H, self.deco)
            self._size = (W, H)
        im = self._sky.copy()
        wx, wy = self.win.winfo_rootx(), self.win.winfo_rooty()
        boxes = []
        for f, lab, glow, inset in self.frames:
            try:
                if not f.winfo_ismapped():
                    continue
                x, y = f.winfo_rootx() - wx, f.winfo_rooty() - wy
                boxes.append((f, lab, x, y, f.winfo_width(), f.winfo_height()))
                if glow:
                    glow_rect(im, (x + inset, y + inset, x + f.winfo_width() - inset - 1,
                                   y + f.winfo_height() - inset - 1), self.deco['glow'])
            except tk.TclError:
                continue
        for wdg, pad in self.rings:
            try:
                if not wdg.winfo_ismapped():
                    continue
                x, y = wdg.winfo_rootx() - wx, wdg.winfo_rooty() - wy
                glow_rect(im, (x - pad, y - pad, x + wdg.winfo_width() + pad - 1,
                               y + wdg.winfo_height() + pad - 1), self.deco['glow'])
            except tk.TclError:
                continue
        for f, lab, x, y, w, h in boxes:
            if w < 2 or h < 2:
                continue
            ph = ImageTk.PhotoImage(im.crop((x, y, x + w, y + h)))
            self.photos[str(lab)] = ph
            lab.configure(image=ph)


def portrait(game, height=130):
    """Персонаж гри для шапки (з кешу чібі, chibi.py) -> PIL RGBA або None."""
    try:
        import chibi
        from PIL import Image
        p = chibi.pick(game)
        if not p:
            return None
        im = Image.open(p).convert('RGBA')
        k = height / im.height
        return im.resize((max(1, int(im.width * k)), height), Image.LANCZOS)
    except Exception:                                   # noqa: BLE001
        return None


def on_color(im, color):
    """RGBA -> RGB на суцільному тлі (Tk не змішує прозорість з віджетами під міткою)."""
    from PIL import Image
    bg = Image.new('RGBA', im.size, _hex(color) + (255,))
    return Image.alpha_composite(bg, im).convert('RGB')
