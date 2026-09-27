# -*- coding: utf-8 -*-
"""Написи, намальовані в текстурах інтерфейсу: стерти старий, намалювати новий.

Атлас — CL3 з парою *.TI (нарізка, див. ti.py) + *.tid (DDS BC7, див. dds.py).
Розмітка лежить у `атлас/написи.json`, стилі — у `атлас/стилі.json`,
шрифти — у `атлас/шрифти/`.

Розмітка кадру (координати відносно лівого верхнього кута кадру):
    текст     англійський оригінал (він же ключ рядка в книзі, якщо нема "ключ")
    стиль     назва пресету зі стилі.json
    тло       "рядки"    — під текстом тло кнопки: кожен рядок пікселів
                           заповнюємо переходом між краями області;
              "прозорий" — напис на прозорому: область просто очищаємо;
              "шаблон"   — тло копіюємо з порожнього кадру того ж вигляду,
                           "зразок": [x, y] — його лівий верхній кут в атласі;
              "смуги"    — тло в смужку: заповнюємо копіями смуг з обох боків,
                           "період": крок смуг по горизонталі в пікселях;
              "стрічка"  — трикутна стрічка-ярлик у кутку рамки: малюємо її
                           наново за "стрічка": {верх, низ, ліво, кінчик,
                           колір, над, під, лінія} (координати кадру);
              "смуга"    — напис на напівпрозорій смузі: рамку стираємо, смугу
                           малюємо наново під ширину нового тексту (_band);
              "картинка" — кадр цілком накриваємо готовим чистим тлом
                           "файл" з атлас/тло/ (порожня кнопка з іншого атласу)
    обертання 90 | -90 | 180 — напис іде вздовж кадру (знизу вгору тощо): уся
              розмітка тоді — в координатах кадру, повернутого на -N градусів
    правки    {ключ стилю: значення} — підправити стиль лише для цього кадру
              (наприклад, {"поворот": -3})
    область   [x0, y0, x1, y1] — що стерти: старий напис разом з обведенням
              і сяйвом
    літери    [y0, y1] — де по висоті лежать самі літери (без ефектів);
              по ній підбираємо кегль і ставимо базову лінію
    поле      [x0, x1] — скільки місця по ширині дозволено новому тексту
              (за замовчуванням — область)
    вирівняти "центр" | "ліво" | "право"
    кегль     (необов'язково) кегль у пікселях замість підібраного — щоб
              сусідні пункти меню мали однаковий розмір

Кегль не задається: беремо такий, за якого англійський оригінал нашим
шрифтом має ту саму висоту літер, що й старий напис. Переклад малюємо тим
самим кеглем; не влазить по ширині — стискаємо по горизонталі до 85%, далі
зменшуємо кегль, і лише коли й це не рятує (менше 70%) — попереджаємо.
"""
import json, math, os
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(HERE, 'атлас')
FONTS = os.path.join(DIR, 'шрифти')
SS = 4                  # надвибірка: малюємо в 4 рази більше і зменшуємо
MIN_SX = 0.85           # найсильніше стискання по горизонталі
MIN_SCALE = 0.70        # нижче цього відносно підібраного кегля — попередження
AXES = {'wght': 'Weight', 'wdth': 'Width', 'slnt': 'Slant', 'ital': 'Italic'}


def load_json(name):
    with open(os.path.join(DIR, name), encoding='utf-8') as f:
        return json.load(f)


# ------------------------------------------------------------ розмітка перекладача
# Основна розмітка (написи.json, нептун.json) — у git, її веде власник програми.
# Те, що розмітив сам перекладач (вікна «Написи на картинках» і «Знайти написи»),
# лежить поруч у «<назва>.мої.json» (не в git: git pull ніколи не конфліктує) і
# накладається зверху кадр за кадром. Власник забирає це в основну — fold_user_marks.
# Службові ключі файлу перекладача: "_відхилено" — {джерело: [[x0, y0, x1, y1], ...]}
# (що в пошуку позначено «не текст»), "_прибрано" — {джерело: [ключі кадрів основної
# розмітки, які перекладач відкликав]}.
def user_file(name):
    return name[:-5] + '.мої.json' if name.endswith('.json') else name + '.мої'


def _read(name):
    p = os.path.join(DIR, name)
    if not os.path.exists(p):
        return {}
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def load_user(name):
    """Уся розмітка перекладача, разом зі службовими ключами."""
    return _read(user_file(name))


def load_marks(name, user=True):
    """{джерело: розмітка} без службових ключів; user — з правками перекладача."""
    import copy
    marks = {k: v for k, v in _read(name).items() if not k.startswith('_')}
    if not user:
        return marks
    for src, mk in load_user(name).items():
        if src.startswith('_'):
            continue
        if src not in marks:
            marks[src] = copy.deepcopy(mk)
            continue
        base = marks[src] = copy.deepcopy(marks[src])
        for k, v in mk.items():
            if k != 'кадри':
                base[k] = v
        base.setdefault('кадри', {}).update(copy.deepcopy(mk.get('кадри', {})))
    # кадри основної розмітки, які перекладач відкликав («Знайти написи»)
    for src, keys in load_user(name).get('_прибрано', {}).items():
        if src in marks:
            fr = {i: sp for i, sp in marks[src].get('кадри', {}).items() if i not in keys}
            marks[src] = dict(marks[src], кадри=fr)
    return marks


def save_user_marks(name, marks):
    """Записати в «мої» лише те, чим `marks` відрізняється від основної розмітки
    (нові атласи, нові й змінені кадри; кадри основної, яких у `marks` немає, — у
    "_прибрано"). Інші службові ключі перекладача зберігаються."""
    base = load_marks(name, user=False)
    old = load_user(name)
    out = {k: v for k, v in old.items() if k.startswith('_') and k != '_прибрано'}
    gone = {}
    for src, b in base.items():
        have = (marks.get(src) or {}).get('кадри', {})
        keys = [i for i in b.get('кадри', {}) if i not in have]
        if keys:
            gone[src] = keys
    if gone:
        out['_прибрано'] = gone
    for src, mk in marks.items():
        if src.startswith('_'):
            continue
        b = base.get(src)
        if b is None:
            out[src] = mk
            continue
        fr = {i: s for i, s in mk.get('кадри', {}).items() if b.get('кадри', {}).get(i) != s}
        head = {k: v for k, v in mk.items() if k != 'кадри' and b.get(k) != v}
        if fr or head:
            out[src] = dict(head, кадри=fr)
    save_user(name, out)


def save_user(name, data):
    """Файл перекладача цілком (порожній — прибрати)."""
    p = os.path.join(DIR, user_file(name))
    if not any(data.values()):
        if os.path.exists(p):
            os.remove(p)
        return
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(p + '.tmp', p)


STYLES = 'стилі.json'


def load_styles(user=True):
    """Стилі: основні (стилі.json, у git) + свої стилі перекладача (стилі.мої.json).
    Свій стиль має позначку "мій": true; з тією самою назвою, що й основний, не буває
    (вікно не дає), але якщо трапиться — основний не перекривається."""
    st = {k: v for k, v in _read(STYLES).items()}
    if user:
        for k, v in _read(user_file(STYLES)).items():
            if not k.startswith('_') and k not in st:
                st[k] = dict(v, мій=True)
    return st


def save_user_style(name, style):
    """Записати (style=None — прибрати) свій стиль перекладача."""
    user = _read(user_file(STYLES))
    if style is None:
        user.pop(name, None)
    else:
        user[name] = {k: v for k, v in style.items() if k != 'мій'}
    save_user(STYLES, user)


MY_GROUP = 'Мої'            # група свого стилю, якщо перекладач не дав іншої


def game_styles(styles, game):
    """Назви стилів гри: основні (у Neptunia — з префіксом «неп-») і свої, створені для неї."""
    nep = game == 'nep'
    return sorted(k for k, v in styles.items() if not k.startswith('_') and
                  ((v.get('гра') == game) if v.get('мій') else (k.startswith('неп-') == nep)))


def group_of(style):
    return style.get('група') or (MY_GROUP if style.get('мій') else 'Інше')


def style_groups(styles, names):
    """[(група, [назви])] для `names`: групи й стилі в них — у порядку файлів стилів
    (основні з стилі.json, далі свої), тож групи перекладача — після основних."""
    names = set(names)
    out = {}
    for k, v in styles.items():
        if k in names:
            out.setdefault(group_of(v), []).append(k)
    return list(out.items())


def fold_user_marks(name):
    """Для власника: розмітку перекладача — в основну; повертає, скільки кадрів забрано.
    Службові ключі ("_відхилено") лишаються у «мої»."""
    user = load_user(name)
    merged = load_marks(name)
    n = sum(len(v.get('кадри', {})) for k, v in user.items() if not k.startswith('_'))
    n += sum(len(v) for v in user.get('_прибрано', {}).values())       # відкликані — теж зміна
    base_all = _read(name)
    base_all.update(merged)
    return base_all, n


# ------------------------------------------------------------------ шрифти
_fonts = {}
EXTRA_FONT_DIRS = {}    # шрифт -> тека (бібліотека кандидатів, див. fontlib.py)


def _font_path(st):
    path = os.path.join(FONTS, st['шрифт'])
    if not os.path.exists(path):
        path = os.path.join(EXTRA_FONT_DIRS.get(st['шрифт'], os.path.join(FONTS, 'кандидати')),
                            st['шрифт'])
    if not os.path.exists(path):                    # свій шрифт перекладача (fontlib.MY_DIR)
        mine = os.path.join(FONTS, 'мої', st['шрифт'])
        if os.path.exists(mine):
            path = mine
    return path


def _font(st, px):
    key = (st['шрифт'], json.dumps(st.get('варіація'), sort_keys=True), px)
    f = _fonts.get(key)
    if f is None:
        f = ImageFont.truetype(_font_path(st), px)
        var = st.get('варіація')
        if isinstance(var, str):
            f.set_variation_by_name(var)
        elif isinstance(var, dict):
            want = {AXES.get(k, k): v for k, v in var.items()}
            f.set_variation_by_axes([want.get(a['name'].decode(), a['default'])
                                     for a in f.get_variation_axes()])
        _fonts[key] = f
    return f


def _rgba(c):
    c = c.lstrip('#')
    if len(c) == 6:
        c += 'ff'
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4, 6))


def _blur(m, r):
    """Гаусове розмиття маски в масштабі SS; великі радіуси — у зменшеній копії."""
    if r <= 0:
        return m
    if r * SS <= 6:
        return m.filter(ImageFilter.GaussianBlur(r * SS))
    small = m.resize((max(1, m.width // SS), max(1, m.height // SS)), Image.BILINEAR)
    return small.filter(ImageFilter.GaussianBlur(r)).resize(m.size, Image.BILINEAR)


# --------------------------------------------------------------- малювання
def _line_bbox(f, t, stroke, track):
    """Межі рядка відносно початку базової лінії (з розрядкою, якщо є)."""
    if not track:
        return f.getbbox(t, anchor='ls', stroke_width=stroke)
    x = 0.0
    x0 = y0 = x1 = y1 = None
    for ch in t:
        b = f.getbbox(ch, anchor='ls', stroke_width=stroke)
        x0 = b[0] + x if x0 is None else min(x0, b[0] + x)
        x1 = b[2] + x if x1 is None else max(x1, b[2] + x)
        y0 = b[1] if y0 is None else min(y0, b[1])
        y1 = b[3] if y1 is None else max(y1, b[3])
        x += f.getlength(ch) + track
    return (round(x0), y0, round(x1), y1)


def _line_draw(d, xy, t, f, sw, track):
    if not track:
        d.text(xy, t, font=f, fill=255, anchor='ls', stroke_width=sw, stroke_fill=255)
        return
    x, y = xy
    for ch in t:
        d.text((x, y), ch, font=f, fill=255, anchor='ls', stroke_width=sw, stroke_fill=255)
        x += f.getlength(ch) + track


def render(text, st, px, align='центр'):
    """Напис з усіма ефектами в масштабі 1:1; '\\n' — новий рядок.
    Повертає (RGBA, x і y базової лінії першого рядка, межі самих літер
    [x0, y0, x1, y1])."""
    if st.get('регістр') == 'великі':
        text = text.upper()
    f = _font(st, px * SS)
    stroke = round(st.get('обведення', {}).get('товщина', 0) * SS)
    # друге, зовнішнє обведення (Neptunia: тонкий темний контур + товстий світлий)
    stroke2 = round(st.get('обведення2', {}).get('товщина', 0) * SS)
    glow = st.get('сяйво') or {}
    shadow = st.get('тінь') or {}
    pad = max(stroke, stroke2) + round((glow.get('радіус', 0) * 3 + max(map(abs, shadow.get('зсув', [0, 0])))
                          + shadow.get('розмиття', 0) * 3) * SS) + 4 * SS
    # кілька рядків: кожен зі своєю базовою лінією, крок — кегль × інтерліньяж
    lines = text.split('\n')
    adv = round(px * SS * st.get('інтерліньяж', 0.95))
    track = st.get('розрядка', 0) * px * SS                # додатковий крок між літерами
    bbs = [_line_bbox(f, t or ' ', stroke, track) for t in lines]
    W = max(b[2] - b[0] for b in bbs)
    offs = []
    for b in bbs:
        lw = b[2] - b[0]
        offs.append(-b[0] if align == 'ліво' else W - lw - b[0] if align == 'право'
                    else (W - lw) / 2 - b[0])
    y0 = min(b[1] + k * adv for k, b in enumerate(bbs))
    y1 = max(b[3] + k * adv for k, b in enumerate(bbs))
    shear = st.get('нахил', 0)
    rot = st.get('поворот', 0)                               # градуси, проти годинникової
    extra = round(abs(shear) * (y1 - y0))
    lift = round(abs(math.sin(math.radians(rot))) * (W + extra))   # місце під поворот
    w, h = W + 2 * pad + extra, y1 - y0 + 2 * pad + 2 * lift
    bx, by = pad + (extra if shear < 0 else 0), pad - y0 + lift    # базова лінія першого рядка

    sq = st.get('квадратність', 0)

    def mask(sw):
        m = Image.new('L', (w, h))
        d = ImageDraw.Draw(m)
        for k, t in enumerate(lines):
            _line_draw(d, (bx + offs[k], by + k * adv), t, f, sw, track)
        return m

    def square():
        """Літери з контурів, підтягнутих до кутів (squarefont.py)."""
        from . import squarefont
        m = Image.new('L', (w, h))
        d = ImageDraw.Draw(m)
        path, var = _font_path(st), st.get('варіація')
        for k, t in enumerate(lines):
            y = by + k * adv
            for i, ch in enumerate(t):
                x = bx + offs[k] + (f.getlength(t[:i]) if not track else
                                    sum(f.getlength(c) + track for c in t[:i]))
                if not ch.isspace() and not squarefont.draw(m, x, y, path, var, ch, px * SS, sq):
                    d.text((x, y), ch, font=f, fill=255, anchor='ls')
        return m

    if sq > 0:
        # обведення — розширена маска літер (кути лишаються гострішими, ніж у stroke_width)
        core = square()
        outer = Image.fromarray(_morph(np.asarray(core), stroke, True, 'коло')) if stroke else core
        outer2 = (Image.fromarray(_morph(np.asarray(core), stroke2, True, 'коло'))
                  if stroke2 > stroke else None)
    else:
        core = mask(0)
        outer = mask(stroke) if stroke else core
        outer2 = mask(stroke2) if stroke2 > stroke else None
    if outer2 is not None:
        stroke_all = outer2          # тінь і сяйво — від найширшого контуру
    else:
        stroke_all = outer
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))

    def put(m, color, strength=1.0):
        r, g, b, a = _rgba(color)
        if strength != 1.0:
            m = m.point(lambda v: min(255, int(v * strength)))
        layer = Image.new('RGBA', (w, h), (r, g, b, 0))
        layer.putalpha(ImageChops.multiply(m, Image.new('L', (w, h), a)))
        out.alpha_composite(layer)

    if shadow:
        dx, dy = (round(v * SS) for v in shadow.get('зсув', [2, 2]))
        put(_blur(ImageChops.offset(stroke_all, dx, dy), shadow.get('розмиття', 0)),
            shadow.get('колір', '#000000c0'))
    if glow:
        put(_blur(stroke_all, glow.get('радіус', 4)), glow.get('колір', '#ff00aa'),
            glow.get('сила', 1.5))
    if outer2 is not None:
        put(outer2, st['обведення2'].get('колір', '#ffffff'))
    if stroke:
        put(outer, st['обведення'].get('колір', '#000000'))
    fill = st.get('заливка', '#ffffff')
    cb = core.getbbox() or (0, 0, 1, 1)
    if isinstance(fill, list):                       # вертикальний градієнт
        cols = [_rgba(c) for c in fill]
        grad = Image.new('RGBA', (1, h))
        for yy in range(h):
            t = min(1.0, max(0.0, (yy - cb[1]) / max(1, cb[3] - cb[1]))) * (len(cols) - 1)
            i = min(int(t), len(cols) - 2)
            a, b = cols[i], cols[i + 1]
            grad.putpixel((0, yy), tuple(round(p + (q - p) * (t - i)) for p, q in zip(a, b)))
        grad = grad.resize((w, h))
        grad.putalpha(ImageChops.multiply(core, grad.getchannel('A')))
        out.alpha_composite(grad)
    else:
        put(core, fill)
    if shear:                                        # нахил навколо базової лінії
        tr = (1, shear, -shear * by, 0, 1, 0)
        out = out.transform((w, h), Image.AFFINE, tr, Image.BICUBIC)
        core = core.transform((w, h), Image.AFFINE, tr, Image.BILINEAR)
        cb = core.getbbox() or cb
    if rot:                                          # поворот навколо центру напису
        c = ((cb[0] + cb[2]) / 2, (cb[1] + cb[3]) / 2)
        out = out.rotate(rot, Image.BICUBIC, center=c)
        core = core.rotate(rot, Image.BILINEAR, center=c)
        cb = core.getbbox() or cb
    k = st.get('розтяг', 1.0)                        # широкий шрифт гри: тягнемо по горизонталі
    if k != 1.0:
        w = max(1, round(w * k))
        out = out.resize((w, h), Image.LANCZOS)
        cb = [cb[0] * k, cb[1], cb[2] * k, cb[3]]
        bx *= k
    out = out.resize((max(1, round(w / SS)), max(1, round(h / SS))), Image.LANCZOS)
    return out, bx / SS, by / SS, [v / SS for v in cb]


def _morph(a, r, grow, shape):
    """Мінімум (grow=False) чи максимум маски в околі радіуса r:
    shape 'квадрат' або 'коло' (восьмикутник: по черзі хрест і квадрат 3×3)."""
    fn = np.maximum if grow else np.minimum
    r = int(round(r))
    if r <= 0:
        return a

    def shift(x, dy, dx):
        p = np.pad(x, 1, mode='constant', constant_values=0 if grow else 255)
        return p[1 + dy:1 + dy + x.shape[0], 1 + dx:1 + dx + x.shape[1]]

    for k in range(r):
        out = fn(fn(a, shift(a, 1, 0)), shift(a, -1, 0))
        out = fn(fn(out, shift(a, 0, 1)), shift(a, 0, -1))
        if shape == 'квадрат' or k % 2:
            for dy, dx in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                out = fn(out, shift(a, dy, dx))
        a = out
    return a


def _ink(im, thr=24):
    """Межі видимої частини (альфа > thr)."""
    return im.getchannel('A').point(lambda v: 255 if v > thr else 0).getbbox()


_EFFECTS = ('обведення', 'обведення2', 'тінь', 'сяйво')


def _geom(st):
    """Стиль лише з формою літер — для вимірів. Межі самих літер (cb) і базова
    лінія від ефектів не залежать, а малювати сяйво й тінь у 4-кратному
    розмірі — найдорожче. З поворотом ефекти впливають на розміщення — там
    лишаємо стиль як є."""
    if st.get('поворот'):
        return st
    g = {k: v for k, v in st.items() if k not in _EFFECTS}
    g['заливка'] = '#ffffff'
    return g


_measured = {}


def measure(text, st, px, align='центр'):
    """(by, cb) з render() — з пам'яттю: той самий напис міряється багато разів."""
    key = (text, json.dumps(st, sort_keys=True, ensure_ascii=False), px, align)
    got = _measured.get(key)
    if got is None:
        if len(_measured) > 20000:
            _measured.clear()
        _im, _bx, by, cb = render(text, _geom(st), px, align)
        got = _measured[key] = (by, cb)
    return got


def fit_size(text, st, height):
    """Кегль, за якого літери `text` цим стилем мають висоту `height`."""
    probe = 100
    _, cb = measure(text, st, probe)
    px = max(4, int(probe * height / max(1, cb[3] - cb[1])))
    for _ in range(4):                               # доточуємо на ±1-2
        _, cb = measure(text, st, px)
        hh = cb[3] - cb[1]
        if hh > height + 0.5 and px > 4:
            px -= 1
        elif hh < height - 1.5:
            px += 1
        else:
            break
    return px


# ------------------------------------------------------------------ стирання
_pics = {}


def _bg_pic(name):
    if name not in _pics:
        _pics[name] = Image.open(os.path.join(DIR, 'тло', name)).convert('RGBA')
    return _pics[name]


def _turned(img, box, spec, fn):
    """Кадр з "обертання": N (градуси, кратні 90) — виконати `fn(кадр, spec)`
    у повернутому просторі, де напис горизонтальний, і вписати назад."""
    ang = spec['обертання']
    frame = img.crop(box).rotate(-ang, expand=True)
    sub = {k: v for k, v in spec.items() if k not in ('обертання', 'рамка')}
    res = fn(frame, (0, 0) + frame.size, sub)
    img.paste(frame.rotate(ang, expand=True), box[:2])
    return res


def erase(img, box, spec):
    """Стерти старий напис у кадрі `box` (координати атласу)."""
    if spec.get('обертання'):
        return _turned(img, box, spec, erase)
    fx, fy = box[0], box[1]
    ax0, ay0, ax1, ay1 = spec['область']
    x0, y0, x1, y1 = fx + ax0, fy + ay0, fx + ax1, fy + ay1
    if spec.get('тло', 'рядки') == 'прозорий':
        if spec.get('полігон'):
            # похилий підпис упритул до сусіднього малюнка (Pause під STOP у сценах):
            # стираємо лише всередині багатокутника (точки відносно кадру)
            m = Image.new('L', img.size, 0)
            ImageDraw.Draw(m).polygon([(fx + x, fy + y) for x, y in spec['полігон']], fill=255)
            img.paste((0, 0, 0, 0), (0, 0), m)
            return
        img.paste((0, 0, 0, 0), (x0, y0, x1, y1))
        return
    if spec.get('тло') == 'смуга':                  # напис на смузі: смугу малюємо наново (_band)
        img.paste((0, 0, 0, 0), box)
        return
    if spec.get('тло') == 'штрих':                  # похилі смужки на картці (наліпки Neptunia)
        _hatch(img, box, spec)
        return
    if spec.get('тло') == 'клин':                   # напівпрозорий смугастий клин (кнопки сцени MSK)
        _wedge(img, box, spec)
        return
    if spec.get('тло') == 'дуга':                   # ім'я героїні на смузі-дузі (портрет бою MSK)
        _arc_band(img, box, spec)
        return
    if spec.get('тло') == 'похила':                 # похила смуга: над нею — одне, у ній — інше
        s = spec['похила']
        (ta, tb), (ba, bb) = s['верх'], s['низ']       # y = a*x + b у координатах кадру
        над, кол = _rgba(s['над']), _rgba(s['колір'])
        px = img.load()
        for y in range(y0, y1):
            for x in range(x0, x1):
                xx, yy = x - fx, y - fy
                if yy < ta * xx + tb:
                    px[x, y] = над
                elif yy <= ba * xx + bb:
                    px[x, y] = кол
        return
    if spec.get('тло') == 'стрічка':                # трикутна кольорова стрічка в кутку рамки
        s = spec['стрічка']
        top, bot, lx, tip = s['верх'], s['низ'], s['ліво'], s['кінчик']
        col = {k: _rgba(s.get(k, s['лінія'])) for k in ('колір', 'над', 'під', 'лінія', 'лінія-на-стрічці')}
        px = img.load()
        for y in range(y0, y1):
            yy = y - fy
            for x in range(x0, x1):
                xx = x - fx
                if yy < top:
                    c = col['над']
                elif yy < top + s.get('товщина', 2):
                    c = col['лінія-на-стрічці'] if xx <= tip else col['лінія']
                elif yy <= bot and xx <= lx + (tip - lx) * (bot - yy) / max(1, bot - top):
                    c = col['колір'] if xx >= lx else col['під']
                else:
                    c = col['під']
                px[x, y] = c
        return
    if spec.get('тло') == 'картинка':               # готове чисте тло з атлас/тло/
        pic = _bg_pic(spec['файл'])
        img.paste(pic, box[:2])
        return
    if spec.get('тло') == 'шаблон':                 # порожній кадр того ж вигляду — цілком
        tx, ty = spec['зразок']
        w, h = box[2] - box[0], box[3] - box[1]
        img.paste(img.crop((tx, ty, tx + w, ty + h)), box[:2])
        return
    px = img.load()
    if spec.get('тло') == 'смуги':                  # періодичне тло: копіюємо смуги з країв
        P = spec['період']
        n = x1 - x0 + 1
        for y in range(y0, y1):
            for x in range(x0, x1):
                xl = x0 - P + (x - x0) % P
                xr = x1 + (x - x1) % P
                okl, okr = xl >= box[0], xr < box[2]
                if okl and okr:
                    t = (x - x0 + 1) / n
                    a, b = px[xl, y], px[xr, y]
                    px[x, y] = tuple(round(p + (q - p) * t) for p, q in zip(a, b))
                elif okl or okr:
                    px[x, y] = px[xl, y] if okl else px[xr, y]
        return
    k = 2                                           # краї усереднюємо по 2 пікселі
    if spec.get('полігон'):
        # напис упритул до похилого краю тла (Gold на червоному трикутнику в крамниці):
        # усередині багатокутника — колір правого краю рядка, поза ним — прозорість;
        # край згладжуємо (маска в 4× більшій роздільності)
        q = 4
        m = Image.new('L', ((x1 - x0) * q, (y1 - y0) * q), 0)
        ImageDraw.Draw(m).polygon([((fx + x - x0) * q, (fy + y - y0) * q) for x, y in spec['полігон']],
                                  fill=255)
        m = m.resize((x1 - x0, y1 - y0), Image.LANCZOS).load()
        for y in range(y0, y1):
            R = [px[x, y] for x in range(x1, min(box[2], x1 + k))] or [px[x1 - 1, y]]
            r = [sum(c[i] for c in R) / len(R) for i in range(4)]
            for x in range(x0, x1):
                w = m[x - x0, y - y0] / 255
                px[x, y] = (round(r[0]), round(r[1]), round(r[2]), round(r[3] * w))
        return
    for y in range(y0, y1):
        L = [px[x, y] for x in range(max(box[0], x0 - k), x0)] or [px[x0, y]]
        R = [px[x, y] for x in range(x1, min(box[2], x1 + k))] or [px[x1 - 1, y]]
        l = [sum(c[i] for c in L) / len(L) for i in range(4)]
        r = [sum(c[i] for c in R) / len(R) for i in range(4)]
        n = x1 - x0 + 1
        for x in range(x0, x1):
            t = (x - x0 + 1) / n
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(l, r))


# ------------------------------------------------------------------ напис
def _split(text, n):
    """Розбити текст по словах на `n` рядків якомога рівнішої довжини."""
    words = text.split()
    if len(words) < n:
        return None
    best, best_w = None, None
    import itertools
    for cut in itertools.combinations(range(1, len(words)), n - 1):
        parts = [' '.join(words[a:b]) for a, b in zip((0,) + cut, cut + (len(words),))]
        wmax = max(len(p) for p in parts)
        if best_w is None or wmax < best_w:
            best, best_w = parts, wmax
    return '\n'.join(best)


def draw(img, box, spec, styles, text, info=None):
    """Стерти старий напис і намалювати `text`. Повертає список попереджень.
    `info` (словник), якщо переданий, отримує 'масштаб' — наскільки довелось
    зменшити напис відносно оригінального кегля (1.0 — не зменшували)."""
    if spec.get('обертання'):
        return _turned(img, box, spec, lambda f, b, s: draw(f, b, s, styles, text, info))
    st = dict(styles[spec['стиль']])
    st.update(spec.get('правки', {}))               # поворот, колір тощо — лише для цього кадру
    how = spec.get('вирівняти', 'центр')
    ax0, ay0, ax1, ay1 = spec['область']
    ly0, ly1 = spec.get('літери', [ay0, ay1])
    fx0, fx1 = spec.get('поле', [ax0, ax1])
    fw = fx1 - fx0
    size = spec.get('кегль') or fit_size(spec['текст'], st, ly1 - ly0)
    rby, rcb = measure(spec['текст'], st, size, how)
    base_y = ly0 + (rby - rcb[1])                   # базова лінія в кадрі
    n_orig = spec['текст'].count('\n') + 1
    text = text.replace('\r', '')

    if spec.get('літери з оригіналу'):
        # А В Е К М Н О Р С Т Х І… — вирізати з оригіналу (див. origletters.py)
        from . import origletters
        orig = img.crop(box)
        erase(img, box, spec)
        res = origletters.compose(orig, img.crop(box), spec, st, text, size, base_y, (fx0, fx1))
        if res is not None:
            layer, real, sx = res
            if info is not None:
                info['масштаб'] = sx
                info['справжніх'] = real
            frame = img.crop(box)
            frame.alpha_composite(layer)
            img.paste(frame, box[:2])
            return []
        img.paste(orig, box[:2])                    # не вийшло — звичайний шлях

    def fit(t):
        px = size
        for step in range(3):
            im, bx, by, cb = render(t, st, px, how)
            b = _ink(im)
            if not b:
                return None
            sx = min(1.0, fw / max(1, b[2] - b[0]))
            if sx >= MIN_SX or px <= 4 or step == 2:
                break
            px = max(4, int(px * sx / MIN_SX))      # кегль так, щоб вистачило 85%
        return (min(1.0, px / size * sx), t, im, by, cb, b, px, sx)

    # оригінал був у кілька рядків — пробуємо й переклад розбити так само
    cands = [text] + ([] if '\n' in text else
                      [x for n in range(2, n_orig + 1) for x in [_split(text, n)] if x])
    best = None
    for t in cands:
        r = fit(t)
        if r and (best is None or r[0] > best[0] + 0.02):
            best = r
    if best is None:
        return [f'«{text}»: порожній напис']
    scale, text, im, by, cb, b, px, sx = best
    if info is not None:
        info['масштаб'] = scale
    warn = []
    if sx < MIN_SX - 0.01 or px < size * MIN_SCALE:
        warn.append(f'«{text}» не влазить у {fw}px (кегль {px} з {size}, стиск {sx:.2f})')
    if sx < 1.0:
        im = im.resize((max(1, round(im.width * sx)), im.height), Image.LANCZOS)
        b = (round(b[0] * sx), b[1], round(b[2] * sx), b[3])
    w = b[2] - b[0]
    left = fx0 if how == 'ліво' else fx1 - w if how == 'право' else fx0 + (fw - w) / 2
    ox = round(left - b[0])
    if text.count('\n') + 1 == n_orig:
        oy = round(base_y - by)                     # та сама базова лінія, що в оригіналі
    else:                                           # інша к-сть рядків — по центру літер
        oy = round((ly0 + ly1) / 2 - (cb[1] + cb[3]) / 2)

    band = _band(img.crop(box), left, left + w) if spec.get('тло') == 'смуга' else None
    erase(img, box, spec)
    # малюємо в окремий шар розміром кадру — сусідні спрайти не зачепимо
    layer = Image.new('RGBA', (box[2] - box[0], box[3] - box[1]), (0, 0, 0, 0))
    layer.paste(im, (ox, oy), im)
    frame = img.crop(box)
    if band is not None:
        frame.alpha_composite(band)
    frame.alpha_composite(layer)
    img.paste(frame, box[:2])
    return warn


def _hatch(img, box, spec):
    """Тло «штрих»: картка в похилу смужку, текст на ній (EXP UP!, TOUGH
    ENEMIES! у Neptunia). З чистого шматка картки "зразок" [x0, y0, x1, y1]
    знаходимо нахил і крок смужок (колір залежить лише від (x + k·y) mod P) і
    заливаємо такими самими смужками многокутник "полігон" [[x, y], ...] —
    місце старого тексту всередині картки, без рамки й іконок. Координати — кадру."""
    fx, fy = box[0], box[1]
    frame = np.asarray(img.crop(box).convert('RGBA')).astype(float)

    def patch(rect, src):
        x0, y0, x1, y1 = rect
        ys, xs = np.mgrid[y0:y1, x0:x1]
        return xs.ravel().astype(float), ys.ravel().astype(float), src[y0:y1, x0:x1].reshape(-1, 4)

    # нахил і крок — із "зразок-кут" (більший чистий шматок сусідньої такої ж картки,
    # координати атласу), якщо свій зразок замалий; колір і фаза — завжди зі свого
    if spec.get('зразок-кут'):
        X, Y, C = patch(spec['зразок-кут'], np.asarray(img.convert('RGBA')).astype(float))
    else:
        X, Y, C = patch(spec['зразок'], frame)
    def search(ks, ps):
        best = None
        for k in ks:
            u = X + k * Y
            for P in ps:
                b = np.floor((u % P) / P * 10).astype(int)
                n = np.bincount(b, minlength=10)
                s = np.array([np.bincount(b, C[:, c], 10) for c in range(3)])
                s2 = np.array([np.bincount(b, C[:, c] ** 2, 10) for c in range(3)])
                err = (s2 - s ** 2 / np.maximum(n, 1)).sum()
                if best is None or err < best[0]:
                    best = (err, k, P)
        return best
    # грубо, потім точніше навколо знайденого
    _e, k, P = search(np.arange(-2.5, 2.5001, 0.1), np.arange(3.0, 16.0, 0.5))
    _e, k, P = search(np.arange(k - 0.12, k + 0.1201, 0.02), np.arange(max(2.5, P - 0.6), P + 0.61, 0.05))
    X, Y, C = patch(spec['зразок'], frame)
    B = 32                                              # профіль однієї смужки, 32 відліки
    b = np.floor(((X + k * Y) % P) / P * B).astype(int)
    n = np.bincount(b, minlength=B)
    prof = np.array([np.bincount(b, C[:, c], B) for c in range(4)]) / np.maximum(n, 1)
    have = n > 0
    if not have.all():                                  # порожні відліки — з сусідніх
        idx = np.arange(B)
        for c in range(4):
            prof[c] = np.interp(idx, idx[have], prof[c][have], period=B)
    mask = Image.new('L', (box[2] - box[0], box[3] - box[1]), 0)
    ImageDraw.Draw(mask).polygon([tuple(p) for p in spec['полігон']], fill=255)
    m = np.asarray(mask) > 127
    yy, xx = np.nonzero(m)
    t = ((xx + k * yy) % P) / P * B
    i0 = np.floor(t).astype(int) % B
    i1 = (i0 + 1) % B
    f = (t - np.floor(t))[None, :]
    col = prof[:, i0] * (1 - f) + prof[:, i1] * f
    frame[yy, xx] = col.T
    img.paste(Image.fromarray(np.clip(frame + 0.5, 0, 255).astype(np.uint8), 'RGBA'), (fx, fy))


def _stripes(C, X, Y, ks, ps):
    """Нахил k і крок P смужок: колір залежить лише від (x + k·y) mod P."""
    best = None
    for k in ks:
        u = X + k * Y
        for P in ps:
            b = np.floor((u % P) / P * 16).astype(int)
            n = np.bincount(b, minlength=16)
            s = np.array([np.bincount(b, C[:, c], 16) for c in range(3)])
            s2 = np.array([np.bincount(b, C[:, c] ** 2, 16) for c in range(3)])
            err = (s2 - s ** 2 / np.maximum(n, 1)).sum()
            if best is None or err < best[0]:
                best = (err, k, P)
    return best[1], best[2]


def _wedge(img, box, spec):
    """Тло «клин»: великі STOP/SKIP/AUTO/LOG у сценах MSK (Game.bra/EVENT/parts1.dds) лежать
    на напівпрозорому клині в косу смужку, що звужується праворуч. Під літерами його
    відтворюємо: колір — смужки з чистого "зразок" [x0, y0, x1, y1] (координати АТЛАСУ: чистий
    шматок праворуч від слова, поза кадром; у RGB смужки є по всій ділянці, навіть де клин
    прозорий), прозорість — між прямими "верх"/"низ" (y = a·x + b) з лінійним спадом
    "альфа" [[x, a], [x, a]] і згладженими краями. Змінюємо лише всередині "полігон".
    Координати, крім "зразок", — кадру."""
    s = spec['клин']
    fx, fy = box[0], box[1]
    frame = np.asarray(img.crop(box).convert('RGBA')).astype(float)
    x0, y0, x1, y1 = s['зразок']
    # "зразок-зсув" [dx, dy]: зразок узято з іншого, такого самого ряду (смуги рядів
    # однакові, а довший чистий шматок є лише під найкоротшим словом)
    sdx, sdy = s.get('зразок-зсув', [0, 0])
    ys, xs = np.mgrid[y0:y1, x0:x1]
    # у координатах кадру: фаза смужок має збігатися з тією, що під літерами
    X, Y = (xs.ravel() - fx - sdx).astype(float), (ys.ravel() - fy - sdy).astype(float)
    C = np.asarray(img.crop((x0, y0, x1, y1)).convert('RGBA')).astype(float).reshape(-1, 4)
    # лише пікселі самої смуги: у прозорих RGB — сміття, у непрозорих — літери й тіні
    keep = (C[:, 3] > 12) & (C[:, 3] < 245) & (C[:, :3].min(axis=1) < 200)
    X, Y, C = X[keep], Y[keep], C[keep]
    k, P = _stripes(C, X, Y, np.arange(-2.5, 2.5001, 0.1), np.arange(10.0, 121.0, 2.0))
    k, P = _stripes(C, X, Y, np.arange(k - 0.12, k + 0.1201, 0.02), np.arange(max(8.0, P - 2.5), P + 2.51, 0.25))
    B = 64
    b = np.floor(((X + k * Y) % P) / P * B).astype(int)
    n = np.bincount(b, minlength=B)
    prof = np.array([np.bincount(b, C[:, c], B) for c in range(3)]) / np.maximum(n, 1)
    have = n > 0
    if not have.all():
        idx = np.arange(B)
        for c in range(3):
            prof[c] = np.interp(idx, idx[have], prof[c][have], period=B)
    mask = Image.new('L', (box[2] - box[0], box[3] - box[1]), 0)
    ImageDraw.Draw(mask).polygon([tuple(p) for p in spec['полігон']], fill=255)
    yy, xx = np.nonzero(np.asarray(mask) > 127)
    t = ((xx + k * yy) % P) / P * B
    i0 = np.floor(t).astype(int) % B
    f = t - np.floor(t)
    rgb = prof[:, i0] * (1 - f) + prof[:, (i0 + 1) % B] * f
    (ta, tb), (ba, bb) = s['верх'], s['низ']
    cov = np.clip(np.minimum(yy - (ta * xx + tb), (ba * xx + bb) - yy) + 0.5, 0, 1)
    (ax0, av0), (ax1, av1) = s['альфа']
    al = np.clip(av0 + (av1 - av0) * (xx - ax0) / (ax1 - ax0), 0, 255)
    frame[yy, xx, :3] = rgb.T
    frame[yy, xx, 3] = al * cov
    img.paste(Image.fromarray(np.clip(frame + 0.5, 0, 255).astype(np.uint8), 'RGBA'), (fx, fy))


def _edge_fit(xs, ys, deg):
    """Многочлен степеня deg через точки краю, з відкиданням викидів (літери, що
    заходять за край)."""
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    for _ in range(3):
        p = np.polyfit(xs, ys, deg)
        r = ys - np.polyval(p, xs)
        keep = np.abs(r) < max(1.5, 3 * r.std())
        xs, ys = xs[keep], ys[keep]
    return np.polyfit(xs, ys, deg)


def _arc_band(img, box, spec):
    """Тло «дуга»: велике ім'я на портреті бою MSK (TTM1.bra/PC/battle_char*.CL3, кадр 1)
    лежить на непрозорій смузі з вигнутим верхнім краєм, градієнтом зліва направо й косими
    смужками. Літери (білі пікселі в "область" оригіналу, розширені на "запас" px — з
    обведенням і об'ємною тінню) замінюємо відтвореною смугою: верхній край — кубічна
    крива, нижній — пряма (далі край кадру), обидва підігнані по видимих ділянках краю
    (похибка ~0,5 px); колір — середнє по стовпчиках (градієнт) + профіль смужок
    (x + k·y) mod P з чистої частини смуги цього ж кадру ("смужки": [k, P] — задати, щоб не
    шукати). Вище краю — прозорість."""
    fx, fy = box[0], box[1]
    F = np.asarray(img.crop(box).convert('RGBA')).astype(float)
    h, w = F.shape[:2]
    ax0, ay0, ax1, ay1 = spec['область']
    white = (F[..., :3].min(axis=-1) > 150) & (F[..., 3] > 100)
    lim = np.zeros((h, w), bool)
    lim[ay0:ay1, ax0:ax1] = True
    pad = spec.get('запас', 17)
    grow = Image.fromarray(((white & lim) * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * pad + 1))
    area = (np.asarray(grow) > 127) & lim
    near = np.asarray(Image.fromarray((area * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(7))) > 127
    A = F[..., 3]
    op = A > 127
    tx, ty, bx, by = [], [], [], []
    for x in range(w):
        col = np.nonzero(op[:, x])[0]
        if not len(col):
            continue
        y0, y1 = col[0], col[-1]
        if y0 > 0 and not near[y0, x]:
            tx.append(x)
            ty.append(y0 - A[y0 - 1, x] / 255.0)          # край з точністю до частки пікселя
        if y1 < h - 3 and not near[y1, x]:
            bx.append(x)
            by.append(y1 + 1 + A[y1 + 1, x] / 255.0)
    X = np.arange(w)
    top = np.polyval(_edge_fit(tx, ty, 3), X)
    bot = np.minimum(np.polyval(_edge_fit(bx, by, 1), X), h + 50)   # далі низ — край кадру
    yy, xx = np.mgrid[0:h, 0:w]
    clean = (yy > top[None] + 2) & (yy < bot[None] - 2) & op & ~near
    Xs, Ys = xx[clean].astype(float), yy[clean].astype(float)
    C = F[clean][:, :3]
    XB = 24                                                  # градієнт: середнє по смугах 24 px
    nxb = w // XB + 1
    xb = np.clip((Xs / XB).astype(int), 0, nxb - 1)
    cnt = np.bincount(xb, minlength=nxb)
    mean = np.stack([np.bincount(xb, C[:, c], nxb) for c in range(3)], 1) / np.maximum(cnt, 1)[:, None]
    have = np.nonzero(cnt)[0]
    for c in range(3):
        mean[:, c] = np.interp(np.arange(nxb), have, mean[have, c])
    R = C - mean[xb]
    if spec.get('смужки'):                  # [k, P] відомі (однакові в усіх портретах) — без пошуку
        k, P = spec['смужки']
    else:                                   # пошук — на вибірці пікселів, інакше секунди на кадр
        sub = np.random.default_rng(0).permutation(len(Xs))[:6000]
        k, P = _stripes(R[sub], Xs[sub], Ys[sub], np.arange(-2.5, 2.5001, 0.1), np.arange(10.0, 121.0, 2.0))
        k, P = _stripes(R[sub], Xs[sub], Ys[sub], np.arange(k - 0.12, k + 0.1201, 0.02),
                        np.arange(max(8.0, P - 2.5), P + 2.51, 0.25))
    PB = 32
    pb = (((Xs + k * Ys) % P) / P * PB).astype(int) % PB
    pc = np.bincount(pb, minlength=PB)
    prof = np.stack([np.bincount(pb, R[:, c], PB) for c in range(3)], 1) / np.maximum(pc, 1)[:, None]
    ay, ax = np.nonzero(area)
    t = (ax + 0.5) / XB - 0.5
    i0 = np.clip(np.floor(t).astype(int), 0, nxb - 1)
    i1 = np.clip(i0 + 1, 0, nxb - 1)
    f = np.clip(t - np.floor(t), 0, 1)[:, None]
    ph = ((ax + k * ay) % P) / P * PB
    j0 = np.floor(ph).astype(int) % PB
    g = (ph - np.floor(ph))[:, None]
    F[ay, ax, :3] = mean[i0] * (1 - f) + mean[i1] * f + prof[j0] * (1 - g) + prof[(j0 + 1) % PB] * g
    F[ay, ax, 3] = 255 * np.clip(np.minimum(ay - top[ax] + 0.5, bot[ax] - ay + 0.5), 0, 1)
    img.paste(Image.fromarray(np.clip(F + 0.5, 0, 255).astype(np.uint8), 'RGBA'), (fx, fy))


def _band(orig, nx0, nx1):
    """Напівпрозора смуга під написом (назви локацій Neptunia) під ширину НОВОГО
    тексту [nx0, nx1): колір, вертикальний профіль, згасання країв і відступи від
    тексту — як в оригінальному кадрі. None — смуги в оригіналі немає."""
    w, h = orig.size
    px = orig.load()
    cols = [x for x in range(w) if any(px[x, y][3] for y in range(h))]
    if not cols:
        return None
    bx0, bx1 = cols[0], cols[-1] + 1
    mid = h // 2
    fade = []                                       # згасання лівого краю: до першого «плато»
    for x in range(bx0, bx1):
        a = px[x, mid][3]
        if fade and a <= fade[-1]:
            break
        fade.append(a)
    top = fade.pop() if len(fade) > 1 else (fade[0] if fade else 0)
    cx = bx0 + len(fade)                            # стовпчик самої смуги (ще без тексту)
    color = px[cx, mid][:3]
    prof = [px[cx, y][3] / max(1, top) for y in range(h)]
    ink = [x for x in range(w) if any(px[x, y][3] and max(px[x, y][:3]) > 60 for y in range(h))]
    if not ink or not top:
        return None
    m_l, m_r = ink[0] - bx0, bx1 - (ink[-1] + 1)
    x0, x1 = max(0, round(nx0) - m_l), min(w, round(nx1) + m_r)
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    op = out.load()
    n = len(fade)
    for x in range(x0, x1):
        k = x - x0
        a = fade[k] if k < n else fade[x1 - 1 - x] if x1 - 1 - x < n else top
        for y in range(h):
            op[x, y] = color + (round(a * prof[y]),)
    return out


# ------------------------------------------------------------------ атлас цілком
class Plain:
    """Окрема текстура .dds без нарізки TI (Game.bra/EVENT/parts1.dds — STOP/SKIP/AUTO/LOG
    у сценах). Та сама поведінка, що в Cl3 для pair/frames/rebuild; кадрів у нарізці
    немає, тож усі кадри розмітки — з явною "рамка"."""

    def __init__(self, data):
        self.files = [['texture.tid', bytearray(data)]]

    def replace(self, name, new_data):
        self.files[0][1] = bytearray(new_data)

    def build(self, align=1):
        return bytes(self.files[0][1])


def container(blob):
    """CL3-атлас або окрема текстура .dds (Plain)."""
    from .cl3 import Cl3
    return Plain(blob) if bytes(blob[:4]) == b'DDS ' else Cl3(blob)


def pair(cl3, stem=None):
    """Файли нарізки й текстури в CL3: ([ім'я, дані, ...] TI, те саме tid).
    Якщо пар кілька — `stem` (ім'я без розширення) вибирає потрібну."""
    tis = {os.path.splitext(f[0])[0].lower(): f for f in cl3.files if f[0].lower().endswith('.ti')}
    tids = {os.path.splitext(f[0])[0].lower(): f for f in cl3.files if f[0].lower().endswith('.tid')}
    key = stem.lower() if stem else next(iter(tids))
    return tis.get(key), tids[key]


def frames(cl3, stem=None):
    """{індекс кадру: (x0, y0, x1, y1)} у пікселях атласу."""
    from .ti import Ti
    ti_f, tid_f = pair(cl3, stem)
    if ti_f is None:                        # Plain: нарізки немає, лише ручні рамки
        return {}
    h, w = __import__('struct').unpack_from('<2I', bytes(tid_f[1][:20]), 12)
    return {i: (x0, y0, x1, y1) for i, x0, y0, x1, y1 in Ti(bytes(ti_f[1])).frames(w, h)}


def box_of(i, spec, boxes):
    """Рамка кадру: з нарізки за номером або явна "рамка" в розмітці (буває, що
    спрайт в атласі є, а кадру в TI під нього нема — тоді ключ довільний)."""
    if spec.get('рамка'):
        return tuple(spec['рамка'])
    return boxes.get(int(i)) if str(i).isdigit() else None


def order(item):
    """Сортування кадрів розмітки: спершу номери по зростанню, далі решта."""
    i = item[0]
    return (0, int(i), '') if str(i).isdigit() else (1, 0, str(i))


def key_of(spec):
    """Ключ рядка в книзі: однаковий англійський напис перекладається один раз
    (перенос рядка не рахується: заголовок «Rescue\\nCenter» і кнопка
    «Rescue Center» — один рядок)."""
    return spec.get('ключ') or ' '.join(spec['текст'].split())


def rebuild(blob, mark, tr, styles, base=None):
    """Перемалювати написи одного атласу. `mark` — розмітка атласу з
    написи.json, `tr` — {ключ: переклад}, `base` — свої картинки перекладача
    {текстура ('' — єдина в атласі): шлях до PNG} (pics.py): вони стають оригіналом
    текстури, написи малюються поверх. Повертає (нові байти CL3 | None,
    к-сть написів, [попередження])."""
    from . import dds
    import pics
    base = base or {}
    todo = [(i, s) for i, s in mark['кадри'].items() if tr.get(key_of(s))]
    if not todo and not base:
        return None, 0, []
    cl3 = container(blob)
    mstem = mark.get('текстура') or ''
    warns, n, changed = [], 0, False
    for stem in sorted(set(base) | ({mstem} if todo else set())):
        _ti, tid_f = pair(cl3, stem or None)
        data = bytes(tid_f[1])
        img = orig = dds.decode(data)
        rects = []
        if stem in base:
            try:
                img = pics.load(base[stem], orig)
                rects += pics.changed_rects(orig, img)
            except (OSError, ValueError) as ex:
                warns.append(f'своя картинка: {ex}')
        if stem == mstem and todo:
            img = img.copy()
            boxes = frames(cl3, stem or None)
            for i, spec in sorted(todo, key=order):
                box = box_of(i, spec, boxes)
                if box is None:
                    warns.append(f'кадру {i} немає в нарізці')
                    continue
                warns += [f'кадр {i}: {w}' for w in draw(img, box, spec, styles, tr[key_of(spec)])]
                rects.append(box)
                n += 1
        if rects:
            cl3.replace(tid_f[0], dds.patch(data, img, rects))
            changed = True
    return (cl3.build() if changed else None), n, warns


# ------------------------------------------------------------ кеш атласів
CACHE_DIR = os.path.join(HERE, 'кеш', 'атласи')


_md5s = {}                  # шлях -> ((розмір, час зміни), md5 вмісту)


def _file_sig(p):
    """md5 вмісту файла (пам'ятаємо, доки не змінились розмір і час): розпакування
    оновлення з ZIP переписує час усім файлам, а вміст — той самий."""
    import hashlib
    try:
        st = os.stat(p)
    except OSError:
        return '-'
    stamp = (st.st_size, st.st_mtime_ns)
    got = _md5s.get(p)
    if got is None or got[0] != stamp:
        with open(p, 'rb') as f:
            got = _md5s[p] = (stamp, hashlib.md5(f.read()).hexdigest())
    return got[1]


def _env_sig():
    """Усе спільне, від чого залежить малювання: код і заготовки тла (шрифти — ні:
    вони в ключі кожного атласа, лише ті, якими він малюється)."""
    parts = []
    for d, ext in ((os.path.join(HERE, 'maryskelter'), '.py'), (os.path.join(HERE, 'neptunia'), '.py'),
                   (os.path.join(DIR, 'тло'), ''), (HERE, 'pics.py')):
        try:
            for fn in sorted(os.listdir(d)):
                p = os.path.join(d, fn)
                if fn.endswith(ext) and os.path.isfile(p):
                    parts.append(f'{fn}:{_file_sig(p)}')
        except OSError:
            pass
    return '|'.join(parts)


# ключі стилю, що не впливають на малювання
_NOT_DRAWN = ('опис', 'група', 'гра', 'мій')


def _used_styles(mark, styles):
    """Стилі, якими малюються кадри атласа (без опису й групи), і шрифти до них: інші
    стилі на атлас не впливають — свій новий стиль не має перемальовувати всі атласи."""
    used, fonts = {}, set()
    for spec in mark['кадри'].values():
        name = spec.get('стиль')
        st = styles.get(name)
        if st is None:
            continue
        used[name] = {k: v for k, v in st.items() if k not in _NOT_DRAWN}
        for s in (st, spec.get('правки') or {}):
            if s.get('шрифт'):
                fonts.add(s['шрифт'])
    return used, {f: _file_sig(_font_path({'шрифт': f})) for f in sorted(fonts)}


def cached(src, blob, mark, tr, styles, fn, base=None):
    """fn(blob, mark, tr, styles[, base]) з кешем у кеш/атласи/: атлас, у якому не
    змінилось нічого (оригінал, розмітка, переклад його написів, ужиті ним стилі
    й шрифти, свої картинки перекладача `base`, код), не перемальовуємо — малювання
    дає ті самі байти, а коштує секунди."""
    import hashlib, pickle
    keys = sorted({key_of(s) for s in mark['кадри'].values()})
    h = hashlib.md5(_env_sig().encode('utf-8'))
    h.update(hashlib.md5(blob).digest())
    h.update(json.dumps([mark, {k: tr[k] for k in keys if k in tr}, *_used_styles(mark, styles),
                         {k: _file_sig(p) for k, p in (base or {}).items()}],
                        sort_keys=True, ensure_ascii=False).encode('utf-8'))
    key = h.hexdigest()
    path = os.path.join(CACHE_DIR, hashlib.md5(src.encode('utf-8')).hexdigest() + '.pickle')
    try:
        with open(path, 'rb') as f:
            k, res = pickle.load(f)
        if k == key:
            return res
    except Exception:
        pass
    res = fn(blob, mark, tr, styles, base) if base else fn(blob, mark, tr, styles)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path + '.tmp', 'wb') as f:
            pickle.dump((key, res), f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(path + '.tmp', path)
    except OSError:
        pass                    # кеш — лише прискорення
    return res


# ------------------------------------------------------------- для розмітки
def _inner_text(bright, w, h, line=0.7):
    """Межі тексту всередині кнопки, не зачіпаючи рамки: шукаємо світле в
    середині кадру, далі нарощуємо межі, поки не впремося в суцільну лінію
    (рядок/стовпець, світлий більш ніж на `line`) або в край кадру."""
    inner = bright.crop((int(w * 0.08), int(h * 0.2), int(w * 0.92), int(h * 0.8))).getbbox()
    if not inner:
        return None
    x0, y0 = inner[0] + int(w * 0.08), inner[1] + int(h * 0.2)
    x1, y1 = inner[2] + int(w * 0.08), inner[3] + int(h * 0.2)
    px = bright.load()

    def row(y):
        n = sum(1 for x in range(x0, x1) if px[x, y])
        return n, n / max(1, x1 - x0)

    def col(x):
        n = sum(1 for y in range(y0, y1) if px[x, y])
        return n, n / max(1, y1 - y0)

    grew = True
    while grew:
        grew = False
        for side in ('u', 'd', 'l', 'r'):
            if side == 'u' and y0 > 0:
                n, f = row(y0 - 1)
                if n and f < line:
                    y0 -= 1; grew = True
            elif side == 'd' and y1 < h:
                n, f = row(y1)
                if n and f < line:
                    y1 += 1; grew = True
            elif side == 'l' and x0 > 0:
                n, f = col(x0 - 1)
                if n and f < line:
                    x0 -= 1; grew = True
            elif side == 'r' and x1 < w:
                n, f = col(x1)
                if n and f < line:
                    x1 += 1; grew = True
    return (x0, y0, x1, y1)


def suggest_field(area, width, mode='рядки', margin=0.1):
    """Поле для нового тексту: симетрично навколо центру старого напису, але
    не ближче за `margin` ширини кадру до його країв (там рамка кнопки)."""
    if mode == 'прозорий':
        return [2, width - 2]
    c = (area[0] + area[2]) / 2
    half = max((area[2] - area[0]) / 2, min(c - margin * width, (1 - margin) * width - c))
    return [round(c - half), round(c + half)]


def suggest(frame, mode='рядки', light=140, grow=6):
    """Підказка розмітки кадру: (область, літери) — для початкової розмітки,
    далі перевіряється очима. Літери в інтерфейсі світлі (білі/жовті: високий
    зелений канал), а тло, рамки й сяйво темніші або рожеві."""
    r, g, b_, a = frame.split()
    bright = ImageChops.multiply(g.point(lambda v: 255 if v > light else 0),
                                 a.point(lambda v: 255 if v > 200 else 0))
    w, h = frame.size
    inner = (int(w * 0.08), int(h * 0.2), int(w * 0.92), int(h * 0.8))
    if not bright.crop(inner).getbbox():            # рожевий текст (неактивна кнопка): по червоному
        bright = ImageChops.multiply(r.point(lambda v: 255 if v > light else 0),
                                     a.point(lambda v: 255 if v > 200 else 0))
    core = bright.getbbox()
    if mode != 'прозорий' and core:
        core = _inner_text(bright, w, h) or core
    if mode == 'прозорий':
        area = _ink(frame, 8)
        area = list(area) if area else None
    elif core:
        area = [max(0, core[0] - grow), max(0, core[1] - grow),
                min(w, core[2] + grow), min(h, core[3] + grow)]
    else:
        area = None
    return area, ([core[1], core[3]] if core else None)
