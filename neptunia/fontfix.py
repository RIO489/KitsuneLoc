"""Українська в шрифтах Neptunia Re;Birth1 (sysfont / msgfont / advfont).

У кожному оригінальному шрифті гри вже є кирилиця (коди Shift-JIS 0x8440…),
намальована в тому ж стилі, що й латиниця цього шрифту, але в клітинках
повної ширини з фіксованим кроком — через це текст «розсипався».
Тут ми:
  1. беремо ці оригінальні гліфи (чисті, 4 біти на піксель — без шуму від
     старих правок), обрізаємо по «чорнилу» з точністю до пікселя;
  2. ставимо відступи зліва/справа такі, як у латиниці ЦЬОГО ж шрифту
     (медіана по a–z і A–Z окремо);
  3. домальовуємо і І ї Ї є Є ґ Ґ з гліфів самого шрифту (i, I, ё/Ё, э/Э, г/Г),
     з урахуванням нахилу курсивного шрифту;
  4. кладемо все в однобайтові слоти (neptunia/chars.py): 0xA1–0xDF і новий
     діапазон 0xFD–0xFF. Латиниця й решта шрифту — байт у байт оригінальні.

Висота гліфа й рядки не змінюються — базова лінія та сама, що в оригіналі.
"""
from .ffu import Ffu
from . import chars

INK = 3          # піксель з яскравістю від 3/15 вважаємо «чорнилом» при обрізанні


def _code(ch):
    return int.from_bytes(ch.encode('cp932'), 'big')


def _encodable(ch):
    try:
        ch.encode('cp932')
        return True
    except UnicodeEncodeError:
        return False


# стара схема перекладу (Crowdin/stcm-editor): і ї є ґ писались грецькими
# літерами Shift-JIS; так вони лежать у старих сейвах (назва локації тощо)
OLD_SUBST = {'І': 0x839F, 'Ї': 0x83A0, 'Ґ': 0x83A1, 'Є': 0x83A2,
             'і': 0x83BF, 'ї': 0x83C0, 'ґ': 0x83C1, 'є': 0x83C2}


def _bbox(rows, thr=INK):
    xs = [x for r in rows for x, v in enumerate(r) if v >= thr]
    ys = [y for y, r in enumerate(rows) if any(v >= thr for v in r)]
    if not xs:
        return None
    return min(xs), max(xs), min(ys), max(ys)


def _metrics(f, letters):
    """Медіанні відступи латиниці: (зліва, справа)."""
    lbs, rbs = [], []
    for ch in letters:
        g = f.glyph(ord(ch))
        if not g:
            continue
        xadv, rows = g
        bb = _bbox(rows, 1)
        if not bb:
            continue
        lbs.append(bb[0])
        rbs.append(xadv - bb[1] - 1)
    lbs.sort(); rbs.sort()
    return lbs[len(lbs) // 2], rbs[len(rbs) // 2]


def _slant(f):
    """Нахил курсиву: на скільки пікселів зсувається штрих на рядок угору."""
    g = f.glyph(ord('l')) or f.glyph(ord('I'))
    rows = g[1]
    bb = _bbox(rows)
    top = [x for x, v in enumerate(rows[bb[2]]) if v >= INK]
    bot = [x for x, v in enumerate(rows[bb[3]]) if v >= INK]
    h = bb[3] - bb[2]
    return ((sum(top) / len(top)) - (sum(bot) / len(bot))) / h if h else 0.0


def _crop(rows, lb):
    """Обрізати по чорнилу (з м'якими краями), лишити lb порожніх колонок зліва."""
    bb = _bbox(rows, 1)
    if not bb:
        return None, 0
    L, R = bb[0], bb[1]
    return [[0] * lb + r[L:R + 1] for r in rows], R - L + 1


def _pad(rows, left, right):
    return [[0] * left + r + [0] * right for r in rows]


def _shear(rows, s, base):
    """Зсунути рядок y праворуч на round(s·(base − y)) пікселів (цілими, без розмиття)."""
    shifts = [round(s * (base - y)) for y in range(len(rows))]
    lo = min(shifts)
    hi = max(shifts)
    w = len(rows[0]) + hi - lo
    out = []
    for y, r in enumerate(rows):
        k = shifts[y] - lo
        out.append(([0] * k + r + [0] * w)[:w])
    return out


def _mirror(rows, s):
    """Дзеркало по горизонталі; для курсиву — з поверненням нахилу."""
    bb = _bbox(rows)
    m = [list(reversed(r)) for r in rows]
    return _shear(m, 2 * s, bb[3]) if abs(s) > 0.01 else m


def _without_dot(rows):
    """Гліф i без крапки: лишаємо лише нижній суцільний блок рядків."""
    ys = [y for y, r in enumerate(rows) if any(v >= INK for v in r)]
    bottom = ys[-1]
    y = bottom
    while y > 0 and any(v >= INK for v in rows[y - 1]):
        y -= 1
    return [r if k >= y else [0] * len(r) for k, r in enumerate(rows)], y


def _dots_from(rows, base_top):
    """Діакритика над буквою (рядки над base_top) — з ё/Ё."""
    return [r if y < base_top else [0] * len(r) for y, r in enumerate(rows)]


def _top_of_body(rows):
    """Перший рядок тіла букви під діакритикою (ё: під крапками)."""
    ys = [y for y, r in enumerate(rows) if any(v >= INK for v in r)]
    # шукаємо розрив між крапками й тілом
    for a, b in zip(ys, ys[1:]):
        if b - a > 1:
            return b
    return ys[0]


def _overlay(base, extra, dx):
    """Накласти extra на base зі зсувом dx (розширює base за потреби)."""
    w = max(len(base[0]), len(extra[0]) + max(dx, 0))
    out = []
    for rb, re_ in zip(base, extra):
        row = (rb + [0] * w)[:w]
        for x, v in enumerate(re_):
            if v and 0 <= x + dx < w:
                row[x + dx] = max(row[x + dx], v)
        out.append(row)
    return out


def _center(rows):
    bb = _bbox(rows)
    return (bb[0] + bb[1]) / 2.0


def _yi(i_rows, yo_rows, slant):
    """ї/Ї: основа i/I без крапки + дві крапки з ё/Ё по центру основи."""
    body, top = _without_dot(i_rows)
    dots = _dots_from(yo_rows, _top_of_body(yo_rows))
    db = _bbox(dots)
    if not db:
        return body
    # центр крапок — над центром верху основи (для курсиву верх зсунутий)
    top_row = [x for x, v in enumerate(body[top]) if v >= INK]
    cx_body = (min(top_row) + max(top_row)) / 2.0 + slant * (top - db[3])
    cx_dots = (db[0] + db[1]) / 2.0
    dx = round(cx_body - cx_dots)
    left = max(0, -dx)
    body = _pad(body, left, 0)
    return _overlay(body, dots, dx + left)


def _ghe_tick(rows, slant):
    """ґ/Ґ: з г/Г — вертикальний «вусик» угору на правому кінці верхньої планки."""
    bb = _bbox(rows)
    L, R, T, B = bb
    # товщина верхньої планки і ширина вертикального штриха
    bar = 0
    while T + bar <= B and rows[T + bar][R] >= INK:
        bar += 1
    mid = (T + B) // 2
    run = [x for x, v in enumerate(rows[mid]) if v >= INK]
    stem = max(2, (run[-1] - run[0] + 1) if run else 2)
    h = max(3, round((B - T + 1) * 0.28))
    top = max(0, T - h)
    rows = [list(r) for r in rows]
    grow = max(0, round(slant * h) + 1)
    rows = _pad(rows, 0, grow)
    for y in range(top, T):
        k = round(slant * (T - y))
        for x in range(R - stem + 1 + k, R + 1 + k):
            if 0 <= x < len(rows[y]):
                rows[y][x] = 15
    return rows


def _sources(f, slant):
    """{укр. літера: рядки пікселів (некропнуті)} з гліфів цього ж шрифту."""
    src = {}
    for ch in chars.UPPER + chars.LOWER:
        try:
            g = f.glyph(_code(ch))
        except UnicodeEncodeError:
            g = None
        if g:
            src[ch] = g[1]
    get = lambda c: f.glyph(c)[1]
    src['і'] = get(ord('i'))
    src['І'] = get(ord('I'))
    src['ї'] = _yi(get(ord('i')), get(_code('ё')), slant)
    src['Ї'] = _yi(get(ord('I')), get(_code('Ё')), slant)
    src['є'] = _mirror(get(_code('э')), slant)
    src['Є'] = _mirror(get(_code('Э')), slant)
    src['ґ'] = _ghe_tick(get(_code('г')), slant)
    src['Ґ'] = _ghe_tick(get(_code('Г')), slant)
    return src


HEART = 0x81E6     # ∵ — перекладач пише його замість ♡ (у старій схемі тут було сердечко)
STAR = 0x8199      # ☆ — зразок розміру, товщини й положення значка в цьому шрифті


def _heart(f):
    """Контурне сердечко ♡ у слот ∵: в межах ☆ того ж шрифту (той самий розмір
    і базова лінія, товщина контуру — як у зірочки). ♡ у Shift-JIS немає."""
    import math
    from PIL import Image, ImageDraw
    star, i = f.glyph(STAR), f.index(HEART)
    if not star or i is None:
        return False
    xadv, rows = star
    h, w = len(rows), len(rows[0])
    bb = _bbox(rows)
    if not bb:
        return False
    x0, x1, y0, y1 = bb
    ink = sum(v for r in rows for v in r) / 15             # «площа» контуру зірочки
    stroke = max(1.2, min(3.0, ink / (2.6 * (x1 - x0 + y1 - y0 + 2))))
    S = 8
    im = Image.new('L', (w * S, h * S), 0)
    d = ImageDraw.Draw(im)
    # класична крива серця, вписана в рамку зірочки з відступом на пів контуру
    pts = [(16 * math.sin(t) ** 3, -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t)
                                     - math.cos(4 * t))) for t in [k * math.pi / 90 for k in range(181)]]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    pad = stroke / 2
    bx0, bx1, by0, by1 = x0 + pad, x1 + 1 - pad, y0 + pad, y1 + 1 - pad
    sx = (bx1 - bx0) / (max(xs) - min(xs))
    sy = (by1 - by0) / (max(ys) - min(ys))
    poly = [((bx0 + (px - min(xs)) * sx) * S, (by0 + (py - min(ys)) * sy) * S) for px, py in pts]
    d.line(poly + [poly[0]], fill=255, width=max(1, round(stroke * S)), joint='curve')
    im = im.resize((w, h), Image.LANCZOS)
    heart = [[min(15, (im.getpixel((x, y)) + 8) // 17) for x in range(w)] for y in range(h)]
    f.set_glyph_at(i, heart, xadv)
    return True


def italic_sysfont(sys_data, msg_data):
    """Похилий шрифт меню, як у старій схемі перекладу: гліфи й діапазони —
    з msgfont (похилий «квадратний» стиль), службовий заголовок (0x428 Б, з
    таблицею) і хвіст — від оригінального sysfont, розмір клітинки — msgfont.
    Далі — як завжди, fix() кладе кирилицю."""
    s, m = Ffu(sys_data), Ffu(msg_data)
    head = bytearray(s.header)
    head[9], head[10], head[14] = m.cell_w, m.cell_h, m.header[14]
    m.header = bytes(head)
    m.range_off = len(head)
    foot = bytearray(s.footer)
    if len(foot) > 1:
        foot[1] = m.cell_h
    m.footer = bytes(foot)
    return m.build()


def fix(data):
    """Оригінальний .ffu -> (новий .ffu, звіт)."""
    f = Ffu(data)
    slant = _slant(f)
    lb_lo, rb_lo = _metrics(f, 'abcdeghknopqsuvxyz')
    lb_up, rb_up = _metrics(f, 'ABCDEFGHKLMNOPRSTUVXZ')
    if f.index(0xFD) is None:
        f.add_range(0xFD, 0x100)
    src = _sources(f, slant)
    done = []
    # однобайтові слоти — основна схема; двобайтові коди — для тексту, який
    # гра зберегла в старих сейвах за старою схемою (кирилиця 0x84xx, а
    # і ї є ґ — грецькими α β δ γ): там теж мають бути охайні українські літери
    slots = [(ch, code) for ch, code in chars.CODE.items()]
    slots += [(ch, _code(ch)) for ch in chars.UPPER + chars.LOWER if _encodable(ch)]
    slots += list(OLD_SUBST.items())
    for ch, code in slots:
        rows = src.get(ch)
        i = f.index(code)
        if rows is None or i is None:
            continue
        up = ch in chars.UPPER
        lb, rb = (lb_up, rb_up) if up else (lb_lo, rb_lo)
        new, w = _crop(rows, lb)
        if new is None:
            continue
        f.set_glyph_at(i, new, lb + w + rb)
        if code in chars.LETTER:
            done.append(ch)
    rep = {'letters': len(done), 'slant': round(slant, 3), 'heart': _heart(f),
           'bearings': (lb_lo, rb_lo, lb_up, rb_up),
           'missing': [c for c in chars.CODE if c not in done]}
    return f.build(), rep
