# -*- coding: utf-8 -*-
"""SDF-гліфи TextMeshPro: поле відстані в атласі ↔ маска літери.

Атлас TMP (Alpha8) зберігає не літеру, а поле відстані до її краю: 0.5 — край,
більше — всередині, менше — ззовні, за межами `Padding` пікселів — 0/1. Щоб
скласти нову літеру з наявних (і з i, є з дзеркальної э…), беремо маску
високої роздільності (поле → поріг 0.5), міняємо маску й рахуємо поле назад.

Координати: «шрифтові» одиниці = пікселі атласу (TMP 1.x: метрики гліфа в
пікселях атласу), x праворуч від початку гліфа, y угору від базової лінії.
Гліф g: рамка чорнила в атласі (g.x, g.y — лівий верхній кут, y згори), зсуви
xo (лівий край чорнила), yo (верх чорнила над базовою лінією).
"""
import numpy as np


class Canvas:
    """Сітка високої роздільності в шрифтових одиницях: k пікселів на одиницю."""

    def __init__(self, x0, x1, y0, y1, k=4):
        self.x0, self.y1, self.k = x0, y1, k          # лівий край і верх
        self.w = int(round((x1 - x0) * k))
        self.h = int(round((y1 - y0) * k))
        j = np.arange(self.w)
        i = np.arange(self.h)
        self.xs = x0 + (j + 0.5) / k                   # x центрів пікселів
        self.ys = y1 - (i + 0.5) / k                   # y центрів пікселів

    def empty(self):
        return np.zeros((self.h, self.w), bool)


def _bilinear(a, ax, ay):
    """Значення атласу a (float, H×W) у неперервних координатах (центри пікселів — i+0.5)."""
    H, W = a.shape
    fx, fy = ax - 0.5, ay - 0.5
    x0 = np.clip(np.floor(fx).astype(int), 0, W - 1)
    y0 = np.clip(np.floor(fy).astype(int), 0, H - 1)
    x1, y1 = np.clip(x0 + 1, 0, W - 1), np.clip(y0 + 1, 0, H - 1)
    tx, ty = np.clip(fx - np.floor(fx), 0, 1), np.clip(fy - np.floor(fy), 0, 1)
    return ((a[y0, x0] * (1 - tx) + a[y0, x1] * tx) * (1 - ty)
            + (a[y1, x0] * (1 - tx) + a[y1, x1] * tx) * ty)


def mask_of(atlas, g, cv, pad):
    """Маска гліфа g на полотні cv (поле атласу > 0.5), лише в межах його рамки + pad."""
    X, Y = np.meshgrid(cv.xs, cv.ys)
    ax = g['x'] + (X - g['xo'])
    ay = g['y'] + (g['yo'] - Y)
    inside = ((ax >= g['x'] - pad) & (ax <= g['x'] + g['w'] + pad)
              & (ay >= g['y'] - pad) & (ay <= g['y'] + g['h'] + pad))
    return (_bilinear(atlas, ax, ay) > 0.5) & inside


def bbox(mask, cv):
    """(лівий x, правий x, верх y, низ y) чорнила в шрифтових одиницях або None."""
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    return (cv.x0 + xs.min() / cv.k, cv.x0 + (xs.max() + 1) / cv.k,
            cv.y1 - ys.min() / cv.k, cv.y1 - (ys.max() + 1) / cv.k)


def _edges(mask, cv):
    """Точки краю маски (середини між сусідніми пікселями «всередині»/«ззовні»), шрифтові од."""
    pts = []
    hx = np.nonzero(mask[:, 1:] != mask[:, :-1])
    pts.append(np.stack([cv.x0 + (hx[1] + 1) / cv.k, cv.y1 - (hx[0] + 0.5) / cv.k], 1))
    vy = np.nonzero(mask[1:, :] != mask[:-1, :])
    pts.append(np.stack([cv.x0 + (vy[1] + 0.5) / cv.k, cv.y1 - (vy[0] + 1) / cv.k], 1))
    return np.concatenate(pts)


def signed_distance(mask, cv, px, py):
    """Відстань (шрифтові од.) від точок (px, py) до краю маски: + всередині, − ззовні."""
    e = _edges(mask, cv)
    pts = np.stack([px.ravel(), py.ravel()], 1)
    d = np.empty(len(pts))
    for s in range(0, len(pts), 2048):                 # шматками: пам'ять точки × край
        q = pts[s:s + 2048]
        d[s:s + 2048] = np.sqrt(((q[:, None, :] - e[None, :, :]) ** 2).sum(2)).min(1)
    j = np.clip(((pts[:, 0] - cv.x0) * cv.k).astype(int), 0, cv.w - 1)
    i = np.clip(((cv.y1 - pts[:, 1]) * cv.k).astype(int), 0, cv.h - 1)
    ins = mask[i, j]
    return np.where(ins, d, -d).reshape(px.shape)


def render(mask, cv, pad, slope):
    """Маска → (метрики гліфа без x/y/adv, блок поля W×H з рамкою чорнила на (pad, pad)).
    slope — крутизна поля: значення = 0.5 + slope × відстань (calibrate)."""
    b = bbox(mask, cv)
    if b is None:
        raise ValueError('порожня літера')
    L, R, T, B = b
    w, h = R - L, T - B
    W, H = int(np.ceil(w)) + 2 * pad, int(np.ceil(h)) + 2 * pad
    jx = np.arange(W) + 0.5 - pad                      # від лівого краю чорнила
    iy = np.arange(H) + 0.5 - pad                      # від верху чорнила вниз
    PX, PY = np.meshgrid(L + jx, T - iy)
    d = signed_distance(mask, cv, PX, PY)
    field = np.clip(0.5 + slope * d, 0, 1)
    return {'w': w, 'h': h, 'xo': L, 'yo': T}, field


def calibrate(atlas, glyphs, cv, pad):
    """Крутизна поля атласу: відтворюємо поле наявних гліфів з їхніх масок і підганяємо
    (найменші квадрати). Повертає (slope, середня похибка на пікселях біля краю)."""
    ds, vs = [], []
    for g in glyphs:
        m = mask_of(atlas, g, cv, pad)
        x0, y0 = int(np.floor(g['x'])) - pad, int(np.floor(g['y'])) - pad
        W, H = int(np.ceil(g['w'])) + 2 * pad + 1, int(np.ceil(g['h'])) + 2 * pad + 1
        ax = np.arange(x0, x0 + W) + 0.5
        ay = np.arange(y0, y0 + H) + 0.5
        AX, AY = np.meshgrid(ax, ay)
        FX, FY = g['xo'] + (AX - g['x']), g['yo'] - (AY - g['y'])
        d = signed_distance(m, cv, FX, FY)
        v = atlas[y0:y0 + H, x0:x0 + W]
        sel = (v > 0.08) & (v < 0.92)
        ds.append(d[sel])
        vs.append(v[sel] - 0.5)
    d, v = np.concatenate(ds), np.concatenate(vs)
    slope = float((d * v).sum() / (d * d).sum())
    err = float(np.abs(np.clip(0.5 + slope * d, 0, 1) - 0.5 - v).mean())
    return slope, err


def free_spot(used, W, H, gap=1):
    """Лівий верхній кут вільного прямокутника W×H у масці зайнятого (True — зайнято)."""
    AH, AW = used.shape
    # сумарна таблиця: зайнятість прямокутника за O(1)
    s = np.zeros((AH + 1, AW + 1), np.int32)
    s[1:, 1:] = used.astype(np.int32).cumsum(0).cumsum(1)
    W2, H2 = W + 2 * gap, H + 2 * gap
    if W2 > AW or H2 > AH:
        return None
    tot = s[H2:, W2:] - s[:-H2, W2:] - s[H2:, :-W2] + s[:-H2, :-W2]
    ys, xs = np.nonzero(tot == 0)
    if not len(ys):
        return None
    k = np.lexsort((xs, ys))[0]                        # найвище, потім найлівіше
    return int(xs[k]) + gap, int(ys[k]) + gap
