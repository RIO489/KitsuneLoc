# -*- coding: utf-8 -*-
"""«Квадратність» літер: округлі шрифти → рублені, як написи в текстурах гри.

Беремо контури гліфа зі шрифту (fontTools) і кожну дугу між двома крайніми
точками (там дотична горизонтальна чи вертикальна — шрифти ставлять туди
точки контуру) тягнемо до кута, де перетинаються дотичні на її кінцях:
    s = 0 — як у шрифті, s = 1 — гострий кут, між ними — прямі боки й
    заокруглений кут меншого радіуса (як «О» в меню Mary Skelter).
Крайні точки не рухаються, тож межі літер і ширина ті самі, що в шрифті, —
кегль і розміщення рахуються як завжди (atlas.measure).

Малює маску `L` (255 — літера) за правилом ненульового обходу: у змінних
шрифтах контури часто перекриваються.
"""
import math

from PIL import Image, ImageDraw

AXIS_TOL = math.radians(12)      # «горизонтальна/вертикальна» дотична — з таким допуском
MIN_TURN = math.radians(25)      # дуга, що повертає менше, лишається дугою
STEPS = 8                        # точок на один відрізок кривої

_fonts = {}
_glyphs = {}


def _font(path):
    f = _fonts.get(path)
    if f is None:
        from fontTools.ttLib import TTFont
        f = _fonts[path] = TTFont(path, lazy=True)
    return f


def _location(font, var):
    """Варіація стилю (ім'я екземпляра або {вісь: значення}) → location для fontTools."""
    if 'fvar' not in font or not var:
        return None
    if isinstance(var, dict):
        tags = {a.axisTag for a in font['fvar'].axes}
        return {k: v for k, v in var.items() if k in tags} or None
    for inst in font['fvar'].instances:
        name = font['name'].getDebugName(inst.subfamilyNameID)
        if name == var:
            return dict(inst.coordinates)
    return None


# ------------------------------------------------------------------ контури
def _segments(ops):
    """Команди пера → замкнені контури як списки відрізків
    ('line', [p0, p1]) / ('curve', [p0, c.., p1]) (квадратичні й кубічні)."""
    out, cur, start, pos = [], [], None, None
    for op, args in ops:
        if op == 'moveTo':
            start = pos = args[0]
            cur = []
        elif op == 'lineTo':
            cur.append(('line', [pos, args[0]]))
            pos = args[0]
        elif op == 'curveTo':
            cur.append(('curve', [pos, *args]))
            pos = args[-1]
        elif op == 'qCurveTo':
            pts = list(args)
            if pts[-1] is None:                     # контур з самих позакривих точок
                pts = pts[:-1]
                first = ((pts[-1][0] + pts[0][0]) / 2, (pts[-1][1] + pts[0][1]) / 2)
                start = pos = first
                pts = pts + [first]
            for k, c in enumerate(pts[:-1]):
                nxt = pts[k + 1]
                end = nxt if k == len(pts) - 2 else ((c[0] + nxt[0]) / 2, (c[1] + nxt[1]) / 2)
                cur.append(('curve', [pos, c, end]))
                pos = end
        elif op in ('closePath', 'endPath'):
            if pos is not None and start is not None and pos != start:
                cur.append(('line', [pos, start]))
            if cur:
                out.append(cur)
            cur, start, pos = [], None, None
    return out


def _dir(a, b):
    d = (b[0] - a[0], b[1] - a[1])
    n = math.hypot(*d)
    return (d[0] / n, d[1] / n) if n else None


def _tangents(seg):
    """(дотична на початку, дотична в кінці) відрізка."""
    p = seg[1]
    t0 = next((d for q in p[1:] for d in [_dir(p[0], q)] if d), None)
    t1 = next((d for q in reversed(p[:-1]) for d in [_dir(q, p[-1])] if d), None)
    return t0, t1


def _flat(seg):
    kind, p = seg
    if kind == 'line':
        return [p[0], p[1]]
    n = len(p) - 1
    pts = []
    for i in range(STEPS + 1):
        t = i / STEPS
        q = list(p)                                 # де Кастельжо
        for r in range(n):
            q = [((1 - t) * q[k][0] + t * q[k + 1][0], (1 - t) * q[k][1] + t * q[k + 1][1])
                 for k in range(len(q) - 1)]
        pts.append(q[0])
    return pts


def _axis(t):
    a = math.atan2(t[1], t[0]) % (math.pi / 2)
    return min(a, math.pi / 2 - a) < AXIS_TOL


def _smooth(t0, t1):
    return t0 and t1 and t0[0] * t1[0] + t0[1] * t1[1] > math.cos(math.radians(10))


def _square_run(pts, tA, tB, s):
    """Дуга A…B → кут C, де перетинаються дотичні: s = 1 — гострий,
    менше — заокруглений на (1 − s) відстані від кута до кінців дуги."""
    A, B = pts[0], pts[-1]
    den = tA[0] * tB[1] - tA[1] * tB[0]
    turn = math.acos(max(-1.0, min(1.0, tA[0] * tB[0] + tA[1] * tB[1])))
    if abs(den) < 1e-6 or turn < MIN_TURN:
        return pts
    dx, dy = B[0] - A[0], B[1] - A[1]
    a = (dx * tB[1] - dy * tB[0]) / den             # A + a·tA = B − b·tB
    b = (tA[0] * dy - tA[1] * dx) / den
    span = math.hypot(dx, dy)
    if a <= 0 or b <= 0 or a > 3 * span or b > 3 * span:
        return pts                                  # перегин чи дивна дуга — не чіпаємо
    C = (A[0] + a * tA[0], A[1] + a * tA[1])
    # пряма від A, заокруглення (1 − s) від кута, пряма до B
    P = (C[0] - (1 - s) * a * tA[0], C[1] - (1 - s) * a * tA[1])
    Q = (C[0] + (1 - s) * b * tB[0], C[1] + (1 - s) * b * tB[1])
    n = 2 * STEPS
    arc = [((1 - t) ** 2 * P[0] + 2 * t * (1 - t) * C[0] + t * t * Q[0],
            (1 - t) ** 2 * P[1] + 2 * t * (1 - t) * C[1] + t * t * Q[1])
           for t in (i / n for i in range(n + 1))]
    return [A] + arc + [B]


def _contour(segs, s):
    """Замкнений контур → ламана, дуги між крайніми точками — квадратніші."""
    n = len(segs)
    tans = [_tangents(g) for g in segs]
    # межа дуги: не крива з обох боків, злам, або дотична вздовж осі
    brk = []
    for k in range(n):
        prev, cur = segs[k - 1], segs[k]
        t_in, t_out = tans[k - 1][1], tans[k][0]
        brk.append(prev[0] != 'curve' or cur[0] != 'curve' or not _smooth(t_in, t_out)
                   or _axis(t_out))
    if not any(brk):
        return [p for g in segs for p in _flat(g)[:-1]]
    k0 = brk.index(True)
    order = segs[k0:] + segs[:k0]
    tans = tans[k0:] + tans[:k0]
    brk = brk[k0:] + brk[:k0]
    pts, k = [], 0
    while k < n:
        if order[k][0] != 'curve':
            pts.append(order[k][1][0])
            k += 1
            continue
        j = k + 1
        while j < n and not brk[j] and order[j][0] == 'curve':
            j += 1
        run = [order[k][1][0]]
        for g in order[k:j]:
            run += _flat(g)[1:]
        tA, tB = tans[k][0], tans[j - 1][1]
        if tA and tB and s > 0:
            run = _square_run(run, tA, tB, s)
        pts += run[:-1]
        k = j
    return pts


def glyph(path, var, ch, s):
    """(контури в одиницях шрифту, одиниць на кегль) або None — символа в шрифті немає."""
    key = (path, repr(var), ch, round(s, 3))
    got = _glyphs.get(key)
    if got is None:
        from fontTools.pens.recordingPen import DecomposingRecordingPen
        font = _font(path)
        name = font.getBestCmap().get(ord(ch))
        if name is None:
            got = False
        else:
            gs = font.getGlyphSet(location=_location(font, var))
            pen = DecomposingRecordingPen(gs)       # «О» кирилиці — посилання на латинську
            gs[name].draw(pen)
            got = ([_contour(c, s) for c in _segments(pen.value)], font['head'].unitsPerEm)
        if len(_glyphs) > 5000:
            _glyphs.clear()
        _glyphs[key] = got
    return got or None


def draw(m, x, y, path, var, ch, px, s):
    """Намалювати літеру в маску `m` з базовою лінією в (x, y), кегль px.
    False — символа в шрифті немає (малюй звичайним способом)."""
    g = glyph(path, var, ch, s)
    if g is None:
        return False
    contours, upem = g
    k = px / upem
    polys = [[(x + gx * k, y - gy * k) for gx, gy in c] for c in contours if len(c) > 2]
    if not polys:
        return True
    xs = [p[0] for c in polys for p in c]
    ys = [p[1] for c in polys for p in c]
    x0, y0 = math.floor(min(xs)), math.floor(min(ys))
    w, h = math.ceil(max(xs)) - x0 + 1, math.ceil(max(ys)) - y0 + 1
    import numpy as np
    acc = np.zeros((h, w), np.int16)
    for c in polys:
        area = sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(c, c[1:] + c[:1]))
        one = Image.new('L', (w, h))
        ImageDraw.Draw(one).polygon([(px_ - x0, py_ - y0) for px_, py_ in c], fill=1)
        acc += np.asarray(one, np.int16) * (1 if area > 0 else -1)
    ink = Image.fromarray(np.where(acc != 0, 255, 0).astype(np.uint8))
    m.paste(255, (x0, y0, x0 + w, y0 + h), ink)
    return True
