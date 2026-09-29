# -*- coding: utf-8 -*-
"""Українська в шрифтах FFU рушія Compile Heart — один алгоритм для всіх ігор.

Що робимо з оригінальним шрифтом гри:
  1. кирилиця (вона є в шрифтах рушія, але в клітинках повної ширини — текст «розсипається»)
     обрізається по чорнилу й отримує відступи латиниці цього ж шрифту;
  2. відсутні і І ї Ї є Є ґ Ґ складаються з гліфів самого шрифту: і/І ← i/I,
     ї/Ї ← ï/Ï або основа i/I + крапки з ё/Ё, є/Є ← дзеркальні э/Э, ґ/Ґ ← г/Г з «вусиком»
     (з урахуванням нахилу курсиву);
  3. кладемо літери туди, звідки їх візьме гра (схема — scheme.py): у власні коди, у
     невживані слоти (рушій ігнорує нові діапазони — MSK) чи в однобайтові коди Shift-JIS
     (двобайтову кирилицю рушій малює шириною ієрогліфа — Neptunia);
  4. за потреби — « » кутиками в стилі шрифту, ♡ у слоті ∵.

Усе, чим ігри відрізняються, — у профілі (dict, див. DEFAULT); без профілю він
складається автоматично з шрифту й текстів гри (scheme.auto). Латиниця й решта
шрифту — байт у байт оригінальні. Висота гліфа й базова лінія не змінюються.
"""
import math

from .ffu import Ffu

INK = 3                       # яскравість «чорнила» для рамок, нахилу, крапок
UPPER = 'АБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ'
LOWER = 'абвгґдеєжзиіїйклмнопрстуфхцчшщьюя'
UKR = 'іІїЇєЄґҐ'
CYR = [chr(c) for c in range(0x400, 0x460)] + ['ґ', 'Ґ']      # кирилиця, яку звужуємо, де вона є

DEFAULT = {
    'bearings': 'latin',      # відступи: 'latin' — медіани латиниці шрифту; (зліва, справа) — фіксовані
    'pad_right': False,       # правий відступ — порожніми колонками в бітмапі (не лише в кроці)
    'crop_first': False,      # дзеркало/вусик — після обрізання (MSK) чи до (Neptunia)
    'yi': 'dots',             # ї/Ї: 'dots' — основа i + крапки ё; 'diaeresis' — готові ï/Ï шрифту
    'ghe': 'relative',        # вусик ґ/Ґ: 'relative' (0.28 висоти, товщина штриха) або (товщина, висота) px
    'slant': 'auto',          # нахил курсиву: 'auto' (з l/I) або число
    'tighten': 'cyrillic',    # що звужувати в клітинках повної ширини: 'cyrillic' або 'wide' (усе не CJK)
    'quotes': {},             # {'«': код, '»': код} — намалювати кутики (нові коди — новим діапазоном)
    'heart': None,            # (код ∵, код ☆) — ♡ у слот ∵ у рамці ☆
    'new_ranges': [],         # [(перший, кінець)] — додати діапазони (Neptunia: 0xFD–0x100)
}


# ---------------------------------------------------------------- растр
def _bbox(rows, thr=INK):
    return Ffu.bbox(rows, thr)


def _latin(f, letters):
    """Медіанні відступи латиниці: (зліва, справа)."""
    lbs, rbs = [], []
    for ch in letters:
        g = f.glyph(f.code(ch))
        if not g:
            continue
        xadv, rows = g
        bb = _bbox(rows, 1)
        if not bb:
            continue
        lbs.append(bb[0])
        rbs.append(xadv - bb[1] - 1)
    lbs.sort(); rbs.sort()
    return (lbs[len(lbs) // 2], rbs[len(rbs) // 2]) if lbs else (1, 1)


def _slant(f):
    """Нахил курсиву: на скільки пікселів зсувається штрих на рядок угору."""
    g = f.glyph(f.code('l')) or f.glyph(f.code('I'))
    if not g:
        return 0.0
    rows = g[1]
    bb = _bbox(rows)
    top = [x for x, v in enumerate(rows[bb[2]]) if v >= INK]
    bot = [x for x, v in enumerate(rows[bb[3]]) if v >= INK]
    h = bb[3] - bb[2]
    return ((sum(top) / len(top)) - (sum(bot) / len(bot))) / h if h else 0.0


def _crop(rows, lb, rb=0):
    """Обрізати по чорнилу (з м'якими краями): lb порожніх колонок зліва, rb справа.
    -> (рядки, ширина чорнила)."""
    bb = _bbox(rows, 1)
    if not bb:
        return None, 0
    L, R = bb[0], bb[1]
    return [[0] * lb + r[L:R + 1] + [0] * rb for r in rows], R - L + 1


def _pad(rows, left, right):
    return [[0] * left + r + [0] * right for r in rows]


def _shear(rows, s, base):
    """Зсунути рядок y праворуч на round(s·(base − y)) пікселів (цілими, без розмиття)."""
    shifts = [round(s * (base - y)) for y in range(len(rows))]
    lo, hi = min(shifts), max(shifts)
    w = len(rows[0]) + hi - lo
    return [([0] * (shifts[y] - lo) + r + [0] * w)[:w] for y, r in enumerate(rows)]


def _mirror(rows, s):
    """Дзеркало по горизонталі; для курсиву — з поверненням нахилу."""
    bb = _bbox(rows)
    m = [list(reversed(r)) for r in rows]
    return _shear(m, 2 * s, bb[3]) if abs(s) > 0.01 else m


def _without_dot(rows):
    """Гліф i без крапки: лишаємо лише нижній суцільний блок рядків."""
    ys = [y for y, r in enumerate(rows) if any(v >= INK for v in r)]
    y = ys[-1]
    while y > 0 and any(v >= INK for v in rows[y - 1]):
        y -= 1
    return [r if k >= y else [0] * len(r) for k, r in enumerate(rows)], y


def _dots_from(rows, base_top):
    return [r if y < base_top else [0] * len(r) for y, r in enumerate(rows)]


def _top_of_body(rows):
    """Перший рядок тіла букви під діакритикою (ё: під крапками)."""
    ys = [y for y, r in enumerate(rows) if any(v >= INK for v in r)]
    for a, b in zip(ys, ys[1:]):
        if b - a > 1:
            return b
    return ys[0]


def _overlay(base, extra, dx):
    w = max(len(base[0]), len(extra[0]) + max(dx, 0))
    out = []
    for rb, re_ in zip(base, extra):
        row = (rb + [0] * w)[:w]
        for x, v in enumerate(re_):
            if v and 0 <= x + dx < w:
                row[x + dx] = max(row[x + dx], v)
        out.append(row)
    return out


def _yi(i_rows, yo_rows, slant):
    """ї/Ї: основа i/I без крапки + дві крапки з ё/Ё по центру основи."""
    body, top = _without_dot(i_rows)
    dots = _dots_from(yo_rows, _top_of_body(yo_rows))
    db = _bbox(dots)
    if not db:
        return body
    top_row = [x for x, v in enumerate(body[top]) if v >= INK]
    cx_body = (min(top_row) + max(top_row)) / 2.0 + slant * (top - db[3])
    dx = round(cx_body - (db[0] + db[1]) / 2.0)
    left = max(0, -dx)
    return _overlay(_pad(body, left, 0), dots, dx + left)


def _ghe_relative(rows, slant):
    """ґ/Ґ: вертикальний «вусик» угору на правому кінці верхньої планки (0.28 висоти)."""
    L, R, T, B = _bbox(rows)
    mid = (T + B) // 2
    run = [x for x, v in enumerate(rows[mid]) if v >= INK]
    stem = max(2, (run[-1] - run[0] + 1) if run else 2)
    h = max(3, round((B - T + 1) * 0.28))
    top = max(0, T - h)
    rows = _pad([list(r) for r in rows], 0, max(0, round(slant * h) + 1))
    for y in range(top, T):
        k = round(slant * (T - y))
        for x in range(R - stem + 1 + k, R + 1 + k):
            if 0 <= x < len(rows[y]):
                rows[y][x] = 15
    return rows


def _ghe_fixed(rows, thick, high):
    """ґ/Ґ: вусик фіксованого розміру (Mary Skelter: 5×9 px, так перевірено в грі)."""
    L, R, T, B = _bbox(rows, 1)
    rows = [list(r) for r in rows]
    x0 = max(L, R - thick + 1)
    for y in range(max(0, T - high), T):
        for x in range(x0, R + 1):
            rows[y][x] = 15
    return rows


# ---------------------------------------------------------------- значки
def _heart(f, heart_code, star_code):
    """Контурне сердечко ♡ у слот heart_code: у рамці ☆ того ж шрифту (розмір, базова
    лінія, товщина контуру — як у зірочки)."""
    from PIL import Image, ImageDraw
    star, i = f.glyph(star_code), f.index(heart_code)
    if not star or i is None:
        return False
    xadv, rows = star
    h, w = len(rows), len(rows[0])
    bb = _bbox(rows)
    if not bb:
        return False
    x0, x1, y0, y1 = bb
    ink = sum(v for r in rows for v in r) / 15
    stroke = max(1.2, min(3.0, ink / (2.6 * (x1 - x0 + y1 - y0 + 2))))
    S = 8
    im = Image.new('L', (w * S, h * S), 0)
    d = ImageDraw.Draw(im)
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
    f.set_glyph_at(i, [[min(15, (im.getpixel((x, y)) + 8) // 17) for x in range(w)] for y in range(h)], xadv)
    return True


def _guillemet(f, right, slant):
    """« або » у стилі шрифту: два кутики ~0.75 висоти малої літери, по центру малих літер,
    товщина — як вертикаль «l», нахил — як у курсиву."""
    from PIL import Image, ImageDraw
    a, el = f.glyph(f.code('a')), f.glyph(f.code('l'))
    if not a or not el:
        return None
    rows_a = a[1]
    h, w_cell = len(rows_a), len(rows_a[0])
    ab = _bbox(rows_a)
    xh = ab[3] - ab[2] + 1
    eb = _bbox(el[1])
    mid = el[1][(eb[2] + eb[3]) // 2]
    stroke = max(1.3, min(4.5, sum(mid) / 15 * 0.7))
    ch, cw, gap = xh * 0.75, xh * 0.75 * 0.55, xh * 0.75 * 0.6
    S = 8
    W = w_cell + 8
    im = Image.new('L', (W * S, h * S), 0)
    d = ImageDraw.Draw(im)
    cy = (ab[2] + ab[3] + 1) / 2
    x0 = 2 + stroke / 2
    for k in range(2):
        xs = x0 + k * gap
        if right:
            pts = [(xs, cy - ch / 2), (xs + cw, cy), (xs, cy + ch / 2)]
        else:
            pts = [(xs + cw, cy - ch / 2), (xs, cy), (xs + cw, cy + ch / 2)]
        d.line([(x * S, y * S) for x, y in pts], fill=255, width=max(1, round(stroke * S)), joint='curve')
    im = im.resize((W, h), Image.LANCZOS)
    rows = [[min(15, (im.getpixel((x, y)) + 8) // 17) for x in range(W)] for y in range(h)]
    return _shear(rows, slant, ab[3]) if abs(slant) > 0.01 else rows


# ---------------------------------------------------------------- джерела літер
def _get(f, ch):
    g = f.glyph(f.code(ch))
    return g[1] if g else None


def _sources(f, p, slant):
    """{літера: рядки пікселів (некропнуті)} — наявна кирилиця + складені і ї є ґ.
    Для crop_first (MSK) тут лише наявні, решту складає _compose_cropped."""
    src = {}
    for ch in CYR:
        rows = _get(f, ch)
        if rows is not None:
            src[ch] = rows
    if p['crop_first']:
        return src
    get = lambda c: _get(f, c)
    for ch, base in (('і', 'i'), ('І', 'I')):
        if ch not in src and get(base):
            src[ch] = get(base)
    for ch, base, yo, dia in (('ї', 'i', 'ё', 'ï'), ('Ї', 'I', 'Ё', 'Ï')):
        if ch in src:
            continue
        if p['yi'] == 'diaeresis' and get(dia):
            src[ch] = get(dia)
        elif get(base) and get(yo):
            src[ch] = _yi(get(base), get(yo), slant)
    for ch, base in (('є', 'э'), ('Є', 'Э')):
        if ch not in src and get(base):
            src[ch] = _mirror(get(base), slant)
    for ch, base in (('ґ', 'г'), ('Ґ', 'Г')):
        if ch not in src and get(base):
            src[ch] = (_ghe_relative(get(base), slant) if p['ghe'] == 'relative'
                       else _ghe_fixed(get(base), *p['ghe']))
    return src


def _compose_cropped(f, p, lb, rb):
    """Порядок MSK: спершу обрізати джерело, потім дзеркало/вусик. -> {літера: (рядки, крок)}."""
    out = {}
    pairs = (('і', 'i'), ('І', 'I'), ('ї', 'ï' if p['yi'] == 'diaeresis' else 'i'),
             ('Ї', 'Ï' if p['yi'] == 'diaeresis' else 'I'), ('є', 'э'), ('Є', 'Э'), ('ґ', 'г'), ('Ґ', 'Г'))
    for ch, base in pairs:
        rows = _get(f, base)
        if rows is None:
            continue
        new, w = _crop(rows, lb, rb if p['pad_right'] else 0)
        if new is None:
            continue
        if ch in 'єЄ':
            new = [list(reversed(r)) for r in new]
        elif ch in 'ґҐ':
            new = (_ghe_fixed(new, *p['ghe']) if p['ghe'] != 'relative' else _ghe_relative(new, 0.0))
        out[ch] = (new, lb + w + rb)
    return out


# ---------------------------------------------------------------- головне
def fix(data, scheme, profile=None):
    """Оригінальний .ffu -> (новий .ffu, звіт). scheme — куди класти літери (scheme.py),
    profile — відмінності гри від DEFAULT (None — усе за замовчуванням)."""
    p = dict(DEFAULT, **(profile or {}))
    f = Ffu(data)
    slant = _slant(f) if p['slant'] == 'auto' else float(p['slant'])
    if p['bearings'] == 'latin':
        lb_lo, rb_lo = _latin(f, 'abcdeghknopqsuvxyz')
        lb_up, rb_up = _latin(f, 'ABCDEFGHKLMNOPRSTUVXZ')
    else:
        lb_lo, rb_lo = lb_up, rb_up = p['bearings']
    for first, end in p['new_ranges']:
        if f.index(first) is None:
            f.add_range(first, end)

    quotes = 0
    for ch, code in p['quotes'].items():
        if f.index(code) is None:
            f.add_range(code, code + 1)
        rows = _guillemet(f, ch == '»', slant)
        if rows:
            new, w = _crop(rows, lb_lo, rb_lo if p['pad_right'] else 0)
            if new is not None:
                f.set_glyph_at(f.index(code), new, lb_lo + w + rb_lo)
                quotes += 1

    tightened = 0
    if p['tighten'] == 'wide':
        tightened += _tighten_wide(f, (lb_lo, rb_lo), (lb_up, rb_up), p['pad_right'])

    src = _sources(f, p, slant)
    ready = _compose_cropped(f, p, lb_lo, rb_lo) if p['crop_first'] else {}
    done = set()
    for ch, code in scheme.placements(f):
        i = f.index(code)
        if i is None:
            continue
        if ch in ready:
            rows, adv = ready[ch]
            f.set_glyph_at(i, rows, adv)
            done.add(ch)
            continue
        rows = src.get(ch)
        if rows is None:
            continue
        lb, rb = (lb_up, rb_up) if ch.isupper() else (lb_lo, rb_lo)
        new, w = _crop(rows, lb, rb if p['pad_right'] else 0)
        if new is None:
            continue
        f.set_glyph_at(i, new, lb + w + rb)
        done.add(ch)
        tightened += ch not in UKR
    heart = bool(p['heart']) and _heart(f, *p['heart'])
    rep = {'letters': len(done), 'tightened': tightened, 'added': [c for c in UKR if c in done],
           'slant': round(slant, 3), 'heart': heart, 'quotes': quotes,
           'bearings': (lb_lo, rb_lo, lb_up, rb_up),
           'missing': [c for c in UPPER + LOWER if c not in done]}
    return f.build(), rep


def _is_cjk(cp):
    return (0x2E80 <= cp <= 0x9FFF or 0xAC00 <= cp <= 0xD7AF or 0xF900 <= cp <= 0xFAFF
            or 0xFF00 <= cp <= 0xFFEF or 0x3000 <= cp <= 0x303F)


def _tighten_wide(f, low, up, pad_right):
    """Некитайсько-японські знаки (крім кирилиці — її кладе схема) у клітинках повної
    ширини: крок ≥ 0.95 клітинки, а чорнило вужче за 0.8 кроку — відступи латиниці."""
    n = 0
    for code, i in list(f.map.items()):
        try:
            ch = code.to_bytes(max(1, (code.bit_length() + 7) // 8), 'big').decode(f.encoding)
        except (UnicodeDecodeError, OverflowError):
            continue
        if len(ch) != 1 or ord(ch) < 0x80 or _is_cjk(ord(ch)) or 0x400 <= ord(ch) < 0x530:
            continue
        g = f.glyph_at(i)
        bb = _bbox(g[1], 1)
        if not bb or g[0] < 0.95 * f.cell_w or bb[1] - bb[0] + 1 >= 0.8 * g[0]:
            continue
        lb, rb = up if ch.isupper() else low
        new, w = _crop(g[1], lb, rb if pad_right else 0)
        f.set_glyph_at(i, new, lb + w + rb)
        n += 1
    return n


def italic_sysfont(sys_data, msg_data):
    """Похилий шрифт меню з гліфів іншого шрифту (Neptunia: sysfont з msgfont, як у старій
    схемі перекладу): гліфи й діапазони — з msg, службовий заголовок і хвіст — з sys,
    розмір клітинки — msg."""
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
