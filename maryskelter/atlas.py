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


# ------------------------------------------------------------------ шрифти
_fonts = {}


def _font(st, px):
    key = (st['шрифт'], json.dumps(st.get('варіація'), sort_keys=True), px)
    f = _fonts.get(key)
    if f is None:
        f = ImageFont.truetype(os.path.join(FONTS, st['шрифт']), px)
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

    def mask(sw):
        m = Image.new('L', (w, h))
        d = ImageDraw.Draw(m)
        for k, t in enumerate(lines):
            _line_draw(d, (bx + offs[k], by + k * adv), t, f, sw, track)
        return m

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


def _ink(im, thr=24):
    """Межі видимої частини (альфа > thr)."""
    return im.getchannel('A').point(lambda v: 255 if v > thr else 0).getbbox()


def fit_size(text, st, height):
    """Кегль, за якого літери `text` цим стилем мають висоту `height`."""
    probe = 100
    _, _, _, cb = render(text, st, probe)
    px = max(4, int(probe * height / max(1, cb[3] - cb[1])))
    for _ in range(4):                               # доточуємо на ±1-2
        _, _, _, cb = render(text, st, px)
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
        img.paste((0, 0, 0, 0), (x0, y0, x1, y1))
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
    _, _, rby, rcb = render(spec['текст'], st, size, how)
    base_y = ly0 + (rby - rcb[1])                   # базова лінія в кадрі
    n_orig = spec['текст'].count('\n') + 1
    text = text.replace('\r', '')

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

    erase(img, box, spec)
    # малюємо в окремий шар розміром кадру — сусідні спрайти не зачепимо
    layer = Image.new('RGBA', (box[2] - box[0], box[3] - box[1]), (0, 0, 0, 0))
    layer.paste(im, (ox, oy), im)
    frame = img.crop(box)
    frame.alpha_composite(layer)
    img.paste(frame, box[:2])
    return warn


# ------------------------------------------------------------------ атлас цілком
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


def rebuild(blob, mark, tr, styles):
    """Перемалювати написи одного атласу. `mark` — розмітка атласу з
    написи.json, `tr` — {ключ: переклад}. Повертає (нові байти CL3 | None,
    к-сть написів, [попередження])."""
    from .cl3 import Cl3
    from . import dds
    todo = [(i, s) for i, s in mark['кадри'].items() if tr.get(key_of(s))]
    if not todo:
        return None, 0, []
    cl3 = Cl3(blob)
    _ti, tid_f = pair(cl3, mark.get('текстура'))
    boxes = frames(cl3, mark.get('текстура'))
    data = bytes(tid_f[1])
    img = dds.decode(data)
    warns, rects = [], []
    for i, spec in sorted(todo, key=order):
        box = box_of(i, spec, boxes)
        if box is None:
            warns.append(f'кадру {i} немає в нарізці')
            continue
        warns += [f'кадр {i}: {w}' for w in draw(img, box, spec, styles, tr[key_of(spec)])]
        rects.append(box)
    cl3.replace(tid_f[0], dds.patch(data, img, rects))
    return cl3.build(), len(rects), warns


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
