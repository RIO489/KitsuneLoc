# -*- coding: utf-8 -*-
"""Свій розпізнавач тексту гри: трафарети — гліфи шрифтів самої гри.

Англійський Windows OCR українську читає ненадійно (латинські «двійники» або
нічого). А гра малює текст відомими гліфами (тими самими, що кладе fontfix),
тож букву можна не вгадувати, а знайти прикладанням трафаретів:

  1. `lines(кадр)` — рядки тексту: світлі пікселі з темним обведенням поруч,
     злиті по горизонталі в рядки;
  2. `Reader` — один шрифт гри в одному масштабі: для рядка шукає вертикальне
     положення клітинки і читає буква за буквою (промінь з кількох варіантів):
     наступна буква — там, куди веде ширина попередньої (xadv), плюс розрядка
     рядка (у меню гра розсуває літери), ± кілька пікселів;
  3. порівняння — нормована кореляція трафарета з кадром лише по «чорнилу»
     трафарета; усі трафарети за раз (numpy einsum).

Масштаб шрифту залежить від розміру вікна гри; знаходиться перебором на
першому рядку й запам'ятовується (див. `FontOCR`).
"""
import time

import numpy as np
from PIL import Image, ImageFilter

# ------------------------------------------------------------------ рядки
BRIGHT = 0.55          # яскравість «чорнила» тексту (текст гри світлий)
DARK = 0.35            # поруч має бути темне (обведення / тло вікна)


def text_mask(gray):
    """Маска пікселів, схожих на текст: світлі, а поруч (5×5) — темне."""
    # «темне поруч» — на зменшеному вдвічі (мінімум 2×2, потім 3×3 ≈ окіл 6×6): фільтр
    # мінімуму на повному кадрі 1600×900 був найдорожчим кроком пошуку рядків
    h, w = gray.shape
    h2, w2 = h - h % 2, w - w % 2
    g2 = gray[:h2, :w2].reshape(h2 // 2, 2, w2 // 2, 2).min(axis=(1, 3))
    im = Image.fromarray((g2 * 255).astype(np.uint8))
    mn2 = np.asarray(im.filter(ImageFilter.MinFilter(3)), dtype=np.float32) / 255
    near = np.ones((h, w), dtype=bool)
    near[:h2, :w2] = np.repeat(np.repeat(mn2 < DARK, 2, axis=0), 2, axis=1)
    return (gray > BRIGHT) & near


def _components(mask):
    """Зв'язні області булевої маски (4-зв'язність) -> [(y0, x0, y1, x1, пікселів)]."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
    ys, xs = np.nonzero(mask)
    for y, x in zip(ys.tolist(), xs.tolist()):
        if seen[y, x]:
            continue
        stack = [(y, x)]
        seen[y, x] = True
        y0 = y1 = y
        x0 = x1 = x
        n = 0
        while stack:
            cy, cx = stack.pop()
            n += 1
            y0, y1, x0, x1 = min(y0, cy), max(y1, cy), min(x0, cx), max(x1, cx)
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        out.append((y0, x0, y1 + 1, x1 + 1, n))
    return out


def _components_runs(mask):
    """Те саме, що _components, але з горизонтальних відрізків: відрізки сусідніх рядків,
    що перекриваються, — одна область (об'єднання-пошук). Кроків — за відрізками,
    а не за пікселями: у кілька разів швидше."""
    h, w = mask.shape
    pad = np.zeros((h, w + 2), dtype=np.int8)
    pad[:, 1:-1] = mask
    d = np.diff(pad, axis=1)
    ys_s, xs_s = np.nonzero(d == 1)                       # початки відрізків (x включно)
    ys_e, xs_e = np.nonzero(d == -1)                      # кінці (x не включно)
    runs = list(zip(ys_s.tolist(), xs_s.tolist(), xs_e.tolist()))   # у порядку рядків і x
    parent = list(range(len(runs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_row = {}
    for i, (y, a, b) in enumerate(runs):
        by_row.setdefault(y, []).append(i)
    for y, cur in by_row.items():
        prev = by_row.get(y - 1)
        if not prev:
            continue
        j = 0
        for i in cur:
            a, b = runs[i][1], runs[i][2]
            while j < len(prev) and runs[prev[j]][2] <= a:
                j += 1
            k = j
            while k < len(prev) and runs[prev[k]][1] < b:
                ri, rk = find(i), find(prev[k])
                if ri != rk:
                    parent[ri] = rk
                k += 1
    comp = {}
    for i, (y, a, b) in enumerate(runs):
        r = find(i)
        c = comp.get(r)
        if c is None:
            comp[r] = [y, a, y, b, b - a]
        else:
            c[0] = min(c[0], y); c[1] = min(c[1], a); c[2] = max(c[2], y); c[3] = max(c[3], b)
            c[4] += b - a
    return [(y0, x0, y1 + 1, x1, n) for y0, x0, y1, x1, n in comp.values()]


def lines(gray, min_h=10, max_h=90):
    """Рамки рядків тексту [(x0, y0, x1, y1)] на сірому кадрі (float 0..1).
    Шукаємо на зменшеній удвічі масці, злитій по горизонталі (букви -> слова ->
    рядок), потім уточнюємо рамку за повною маскою."""
    m = text_mask(gray)
    k = 2
    h, w = m.shape
    small = m[:h - h % k, :w - w % k].reshape(h // k, k, w // k, k).any(axis=(1, 3))
    # злити по горизонталі на ~12 px (проміжки між літерами й словами)
    run = 6
    cs = np.cumsum(np.pad(small, ((0, 0), (run, run))).astype(np.int32), axis=1)
    wide = (cs[:, 2 * run:] - cs[:, :-2 * run]) > 0
    wide = wide[:, :small.shape[1]]
    out = []
    for y0, x0, y1, x1, n in _components_runs(wide):
        Y0, Y1, X0, X1 = y0 * k, y1 * k, x0 * k, x1 * k
        sub = m[Y0:Y1, X0:X1]
        rows = np.nonzero(sub.any(axis=1))[0]
        cols = np.nonzero(sub.any(axis=0))[0]
        if not len(rows):
            continue
        Y0, Y1 = Y0 + rows[0], Y0 + rows[-1] + 1
        X0, X1 = X0 + cols[0], X0 + cols[-1] + 1
        hh, ww = Y1 - Y0, X1 - X0
        if not (min_h <= hh <= max_h) or ww < hh * 0.6:
            continue
        if sub.sum() < hh * 3:                           # надто рідко — не текст
            continue
        out.append((int(X0), int(Y0), int(X1), int(Y1)))
    return _join_row(out)


def _join_row(boxes, gap_k=1.3):
    """Рамки на одній висоті з проміжком менше gap_k висот — один рядок. Злиття
    вище бере проміжки до ~16 px, а подвійний пробіл перекладача («пафосу...  уже»)
    чи ширший проміжок — 17–25 px: рядок розпадався на шматки, і пошук бачив лише слова.
    Колонки меню («Англійська» | «Японська», ~90 px) лишаються окремими."""
    boxes = sorted(boxes, key=lambda b: (b[1], b[0]))
    changed = True
    while changed:
        changed = False
        boxes.sort(key=lambda b: (b[0], b[1]))
        for i, a in enumerate(boxes):
            for j in range(i + 1, len(boxes)):
                b = boxes[j]
                h = min(a[3] - a[1], b[3] - b[1])
                over = min(a[3], b[3]) - max(a[1], b[1])
                gap = b[0] - a[2]
                if over >= 0.6 * h and -2 <= gap < gap_k * h:
                    boxes[i] = (a[0], min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    del boxes[j]
                    changed = True
                    break
            if changed:
                break
    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


def _content(gray, box):
    """Відбиток вмісту рамки (грубо: 16 рівнів яскравості) — ключ пам'яті прочитаного."""
    import hashlib
    x0, y0, x1, y1 = box
    q = (gray[y0:y1, x0:x1] * 15.99).astype(np.uint8)
    return hashlib.blake2b(q.tobytes(), digest_size=12).digest()


# ------------------------------------------------------------------ читання
WORK_H = 32            # висота клітинки шрифту, до якої зменшуємо (швидкість)
TRACK_BELOW = 0.75     # розрядку підбираємо лише за такого поганого прочитання без неї
START_MIN = 0.72       # найкраща перша буква слабша — у рамці не текст
LINE_GOOD = 0.9        # кореляція всього рядка (line_score), з якої прочитання певне (хибне ~0.85)
EXTRA = '…«»《》♪☆一・“”’—♡∵'


def charset(font):
    """Символи, для яких у шрифті є гліф (українські, ASCII, уживані знаки)."""
    from neptunia import chars
    want = chars.UPPER + chars.LOWER + ''.join(chr(c) for c in range(0x21, 0x7f)) + EXTRA
    return [c for c in dict.fromkeys(want) if font.glyph(c)]


class Reader:
    """Один шрифт гри в масштабі s (гліф гри × s = пікселі кадру). Працює на
    кадрі, зменшеному в f разів так, щоб клітинка була ~WORK_H пікселів."""

    def __init__(self, font, s, chars=None):
        self.font, self.s = font, s
        self.f = min(1.0, WORK_H / (font.cell_h * s))
        k = s * self.f                                   # гліф -> робочі пікселі
        self.k = k
        chars = chars or charset(font)
        H = max(1, round(font.cell_h * k))
        items = []
        for ch in chars:
            xadv, m = font.glyph(ch)
            a = np.asarray(m, dtype=np.float32) / 255
            cols = np.nonzero(a.max(axis=0) > 0.2)[0]
            if not len(cols):
                continue
            w = max(1, round(a.shape[1] * k))
            im = Image.fromarray((a * 255).astype(np.uint8)).resize((w, H), Image.BILINEAR)
            t = np.asarray(im, dtype=np.float32) / 255
            # порівнюємо всю ширину, яку буква займає (від початку клітинки до
            # наступної букви), а не лише її чорнило: інакше вузька «і» чи «l»
            # «знаходилась» у першому штриху широкої «Щ» чи «н»
            ink_hi = int(np.ceil((cols[-1] + 1) * k))
            hi = max(ink_hi, int(round(xadv * k)))
            if hi > t.shape[1]:
                t = np.pad(t, ((0, 0), (0, hi - t.shape[1])))
            items.append((ch, xadv * k, t, 0, max(1, hi)))
        self.H = H
        self.W = max(t.shape[1] for _c, _x, t, _l, _h in items) + 1
        G = len(items)
        T = np.zeros((G, H, self.W), dtype=np.float32)
        self.lo = np.zeros(G, dtype=np.int32)
        self.hi = np.zeros(G, dtype=np.int32)
        self.norm = np.zeros(G, dtype=np.float32)
        for i, (ch, _x, t, lo, hi) in enumerate(items):
            r = t[:, lo:hi]
            r = r - r.mean()
            T[i, :, lo:hi] = r
            self.lo[i], self.hi[i] = lo, hi
            self.norm[i] = np.sqrt((r * r).sum()) or 1.0
        self.T = T
        self.raw = [t for _c, _x, t, _l, _h in items]         # гліфи як є (для line_score)
        self.chars = [c for c, *_ in items]
        self.adv = np.array([x for _c, x, *_ in items], dtype=np.float32)
        sp = font.glyph(' ')
        self.space = (sp[0] if sp else font.cell_h // 3) * k
        self.track_hint = None                  # розрядка, знайдена на попередніх рядках
        self.start_mean = -2.0
        self.start_alts = []

    def scores(self, G, cy, ox):
        """Кореляція кожного гліфа, поставленого клітинкою в (ox, cy) робочого кадру."""
        ox = int(round(ox))
        h, w = G.shape
        win = np.zeros((self.H, self.W), dtype=np.float32)
        ya, yb = max(0, cy), min(h, cy + self.H)
        xa, xb = max(0, ox), min(w, ox + self.W)
        if ya >= yb or xa >= xb:
            return np.full(len(self.chars), -1.0, dtype=np.float32)
        win[ya - cy:yb - cy, xa - ox:xb - ox] = G[ya:yb, xa:xb]
        num = np.tensordot(self.T, win, axes=([1, 2], [0, 1]))
        c1 = np.concatenate([[0], np.cumsum(win.sum(axis=0))])
        c2 = np.concatenate([[0], np.cumsum((win * win).sum(axis=0))])
        n = (self.hi - self.lo) * self.H
        s1 = c1[self.hi] - c1[self.lo]
        s2 = c2[self.hi] - c2[self.lo]
        var = np.maximum(s2 - s1 * s1 / n, 1e-6)
        return num / (self.norm * np.sqrt(var))

    def _win(self, G, cy, ox):
        ox = int(round(ox))
        h, w = G.shape
        win = np.zeros((self.H, self.W), dtype=np.float32)
        ya, yb = max(0, cy), min(h, cy + self.H)
        xa, xb = max(0, ox), min(w, ox + self.W)
        if ya < yb and xa < xb:
            win[ya - cy:yb - cy, xa - ox:xb - ox] = G[ya:yb, xa:xb]
        return win

    def scores_many(self, G, cy, oxs):
        """scores() для кількох позицій за одну операцію -> масив (позицій, гліфів)."""
        wins = np.stack([self._win(G, cy, ox) for ox in oxs])
        num = np.tensordot(wins, self.T, axes=([1, 2], [1, 2]))       # (позицій, гліфів)
        z = np.zeros((len(oxs), 1), dtype=np.float32)
        c1 = np.concatenate([z, np.cumsum(wins.sum(axis=1), axis=1)], axis=1)
        c2 = np.concatenate([z, np.cumsum((wins * wins).sum(axis=1), axis=1)], axis=1)
        n = (self.hi - self.lo) * self.H
        s1 = c1[:, self.hi] - c1[:, self.lo]
        s2 = c2[:, self.hi] - c2[:, self.lo]
        var = np.maximum(s2 - s1 * s1 / n, 1e-6)
        return num / (self.norm * np.sqrt(var))

    def ink(self, G, cy, xa, xb):
        """Чи є «чорнило» (світле) в смузі [xa, xb) рядка."""
        xa, xb = max(0, int(xa)), int(xb)
        if xb <= xa:
            return 0.0
        return float(G[max(0, cy):cy + self.H, xa:xb].max(initial=0.0))

    def start(self, G, box):
        """Вертикаль клітинки й перша буква рядка: перебір невеликого вікна.
        -> (кореляція, cy, ox, індекс гліфа)."""
        x0, y0, x1, y1 = box
        # спершу грубо (крок 2), потім точно навколо найкращих
        coarse = []
        oxs = list(range(x0 - self.W // 2, x0 + 2, 2))
        for cy in range(y1 - self.H - 2, y0 + 3, 2):
            sc = self.scores_many(G, cy, oxs).max(axis=1)
            coarse += [(float(v), cy, ox) for v, ox in zip(sc, oxs)]
        coarse.sort(reverse=True)
        self.start_mean = -2.0
        self.start_alts = []
        if not coarse or coarse[0][0] < START_MIN:
            # жодна буква тут не схожа — шум малюнка; далі не читаємо (це найдорожче)
            v, cy, ox = coarse[0] if coarse else (-2.0, 0, 0)
            return (v, cy, ox, 0)
        cands, done_pos = [], set()
        for _v, cy0, ox0 in coarse[:3]:
            for cy in range(cy0 - 1, cy0 + 2):
                for ox in range(ox0 - 1, ox0 + 2):
                    if (cy, ox) in done_pos:
                        continue
                    done_pos.add((cy, ox))
                    sc = self.scores(G, cy, ox)
                    for i in np.argsort(sc)[-2:]:
                        cands.append((float(sc[i]), cy, ox, int(i)))
        self.start_mean = -2.0
        cands.sort(reverse=True)
        # перша буква окремо обманлива («І» — перший штрих «Щ»): беремо той
        # початок, з якого найкраще читаються кілька перших букв
        ranked = []
        seen = set()
        for c in cands:
            if len(seen) >= 6 or c[0] < 0.5:
                break
            if (c[3], c[1]) in seen:
                continue
            seen.add((c[3], c[1]))
            _t, m, _e, _p = self.decode(G, c[1], c[2], c[3], c[0], x1, beam=2, limit=4,
                                    track=self.track_hint or 0.0)
            ranked.append((m, c))
        if not ranked:
            self.start_alts = []
            return (-2.0, 0, 0, 0)
        ranked.sort(key=lambda r: -r[0])
        best_m, best = ranked[0]
        # друга за якістю перша буква (інша) — read() дочитає обидві й вибере за line_score:
        # «ІІJо» і «Що» за першими буквами майже рівні, а цілим рядком — ні
        self.start_alts = [c for m, c in ranked[1:] if c[3] != best[3] and m >= best_m - 0.06][:1]
        self.start_mean = best_m                 # як добре читаються перші букви (вибір масштабу)
        return best

    def decode(self, G, cy, ox, i, v, x1, track=0.0, beam=5, limit=None):
        """Буква за буквою від першої (i в ox) з розрядкою track -> (текст, середня
        кореляція, чи дійшли до кінця рядка). Наступна буква — там, куди веде xadv
        попередньої (+ track), ±2 px; порожнє місце — лише пробіл."""
        states = [(v, [self.chars[i]], ox + self.adv[i], 1, [(i, ox)])]
        done = []
        for _step in range(300):
            if not states:
                break
            nxt = []
            for tot, text, px, n, pos in states:
                if px > x1 or (limit and n >= limit):
                    done.append((True, tot / n, ''.join(text), pos))
                    continue
                p = px + track
                if self.ink(G, cy, p, x1 + 1) < BRIGHT:          # далі чорнила немає — кінець
                    done.append((True, tot / n, ''.join(text), pos))
                    continue
                # кілька пробілів поспіль бувають (перекладач ставить подвійний), але не безмежно
                if self.ink(G, cy, p + 1, p + self.space - 1) < BRIGHT and text[-3:] != [' '] * 3:
                    nxt.append((tot + 0.9, text + [' '], p + self.space, n + 1, pos))
                cand = []
                ds = [d for d in range(-2, 3)                   # порожньо — не буква
                      if self.ink(G, cy, p + d, p + d + self.space) >= BRIGHT]
                if ds:
                    M = self.scores_many(G, cy, [p + d for d in ds])
                    for d, sc in zip(ds, M):
                        for j in np.argpartition(sc, -3)[-3:]:
                            cand.append((float(sc[j]), d, int(j)))
                cand.sort(reverse=True)
                took = 0
                for c, d, j in cand:
                    if c < 0.4 or took >= beam:
                        break
                    nxt.append((tot + c, text + [self.chars[j]], p + d + self.adv[j], n + 1,
                                pos + [(j, p + d)]))
                    took += 1
                if not took and not nxt:
                    done.append((False, tot / n, ''.join(text), pos))
            uniq = {}
            for st in nxt:                                      # та сама позиція — кращий варіант
                key = (round(st[2]), st[1][-1] == ' ')
                if key not in uniq or st[0] / st[3] > uniq[key][0] / uniq[key][3]:
                    uniq[key] = st
            states = sorted(uniq.values(), key=lambda st: -st[0] / st[3])[:beam]
        if not done:
            return '', 0.0, False, []
        end, mean, text, pos = max(done, key=lambda d: (d[0], d[1]))
        return text.rstrip(), mean, end, pos

    def line_score(self, G, cy, pos, box):
        """Перевірка всього рядка: прочитане, намальоване гліфами на знайдених місцях,
        проти кадру (кореляція). Правильне прочитання пояснює все чорнило рядка;
        хибне (не той масштаб, не та розрядка) — ні, хоч окремі букви й схожі."""
        if not pos:
            return -1.0
        x0, _y0, x1, _y1 = box
        xa = int(min(x0, min(x for _j, x in pos))) - 1
        # лише до кінця рамки: для частини рядка (проба масштабу) далі — чорнило букв,
        # яких ми не малювали, і оцінка падала з 0,97 до 0,87 навіть на правильному масштабі
        xb = int(x1) + 1
        img = np.zeros((self.H, xb - xa), dtype=np.float32)
        for j, x in pos:
            a = int(round(x)) - xa
            w = self.hi[j]
            seg = img[:, a:a + w]
            np.maximum(seg, self.raw[j][:, :seg.shape[1]], out=seg)
        crop = np.zeros_like(img)
        h, w = G.shape
        ya, yb = max(0, cy), min(h, cy + self.H)
        ca, cb = max(0, xa), min(w, xb)
        crop[ya - cy:yb - cy, ca - xa:cb - xa] = G[ya:yb, ca:cb]
        a = img - img.mean()
        b = crop - crop.mean()
        d = float(np.sqrt((a * a).sum() * (b * b).sum()))
        return float((a * b).sum() / d) if d else -1.0

    def fits(self, box):
        """Чи може рядок цієї висоти бути цим шрифтом у цьому масштабі (у робочих px)."""
        h = box[3] - box[1]
        return 0.3 * self.H <= h <= 1.15 * self.H

    def read(self, G, box):
        """Прочитати рядок у рамці box (робочі координати) -> (текст, середня кореляція).
        Спершу без розрядки; погано — підбираємо розрядку (меню розсуває літери)."""
        v, cy, ox, i = self.start(G, box)
        if v < 0.5:
            return '', v
        x1 = box[2]
        tried = {}

        def attempt(track):
            if track not in tried:
                text, _mean, end, pos = self.decode(G, cy, ox, i, v, x1, track=track)
                sc = self.line_score(G, cy, pos, box) if end else -1.0
                tried[track] = (sc, text)
            return tried[track]

        # без розрядки; розрядка цього масштабу, знайдена раніше (меню); підбір
        attempt(0.0)
        for av, acy, aox, ai in self.start_alts:          # інша перша буква — цілим рядком
            text, _mean, end, pos = self.decode(G, acy, aox, ai, av, x1)
            sc = self.line_score(G, acy, pos, box) if end else -1.0
            if sc > tried[0.0][0]:
                tried[0.0] = (sc, text)
        if tried[0.0][0] >= LINE_GOOD:
            return tried[0.0][1], tried[0.0][0]
        if self.track_hint and attempt(self.track_hint)[0] >= LINE_GOOD:
            return tried[self.track_hint][1], tried[self.track_hint][0]
        if tried[0.0][0] >= TRACK_BELOW:
            # прочитано, хоч і не певно (похилий шрифт, дрібне) — розрядка тут ні до чого,
            # а її підбір — 20+ прочитань рядка
            return tried[0.0][1], tried[0.0][0]
        best_t, best_m = 0.0, -1.0
        for t in range(1, max(2, int(self.H * 0.7))):
            _tx, m, _e, _p = self.decode(G, cy, ox, i, v, x1, track=float(t), beam=2, limit=5)
            if m > best_m:
                best_t, best_m = float(t), m
        sc, _t = attempt(best_t)
        if best_t and sc >= LINE_GOOD:
            self.track_hint = best_t
        sc, text = max(tried.values())
        return text, sc



# ------------------------------------------------------------------ кілька шрифтів
COOLDOWN = 30          # стільки кадрів не шукаємо масштаб там, де не вдалося
GOOD = LINE_GOOD       # з такої кореляції всього рядка (line_score) прочитано певно
THIN = set('іlI1|!.,:;"`*_-—一・\'')   # тонкі знаки: шум малюнка «читається» ними


def plausible(text, need=2):
    """Чи схоже прочитане на текст, а не на шум (волосся, листя): щонайменше
    need «повних» букв і тонких знаків не більшість."""
    t = text.replace(' ', '')
    full = [c for c in t if c.isalnum() and c not in THIN]
    return len(full) >= need and len(full) >= len(t) * 0.5


class FontOCR:
    """Шрифти гри (Neptunia: advfont — головне вікно діалогу; msgfont — історія,
    імена, меню) у масштабах, які трапились у цьому вікні гри. Масштаб рядка
    невідомий — оцінюємо за висотою рамки, уточнюємо перебором і запам'ятовуємо:
    наступні рядки й кадри спершу пробують знайдені масштаби."""

    def __init__(self, fonts):
        self.fonts = fonts                      # {назва: preview.GameFont}
        self.readers = {}                       # (назва, s) -> Reader
        self.known = []                         # [(назва, s)] — знайдені масштаби, найуживаніші спершу
        self.frame_h = None
        self.frame_no = 0
        self.failed = {}                        # рамка (грубо) -> кадр, де підбір не вдався
        self.memo = {}                          # (рамка, вміст) -> прочитане: той самий рядок не читаємо двічі
        self.memo_known = None
        # висота «чорнила» рядка в клітинці (від верху великих до низу хвостиків)
        self.ink_h = {}
        for name, f in fonts.items():
            tops, bots = [], []
            for ch in 'ДЙЩбдрукАВПОСТ':
                g = f.glyph(ch)
                if not g:
                    continue
                a = np.asarray(g[1]) > 50
                rows = np.nonzero(a.any(axis=1))[0]
                if len(rows):
                    tops.append(rows[0]); bots.append(rows[-1])
            self.ink_h[name] = (max(bots) - min(tops) + 1) if tops else f.cell_h

    def reader(self, name, s):
        key = (name, round(s, 3))
        if key not in self.readers:
            if len(self.readers) >= 80:          # ~0,6 МБ кожен — старі прибираємо
                for k in list(self.readers)[:20]:
                    if (k[0], k[1]) not in self.known:
                        del self.readers[k]
            self.readers[key] = Reader(self.fonts[name], s)
        return self.readers[key]

    def _read_with(self, gray, box, name, s):
        R = self.reader(name, s)
        G = self._work(gray, R.f)
        b = tuple(int(round(v * R.f)) for v in box)
        if not R.fits(b):
            return '', -1.0
        return R.read(G, b)

    def _work(self, gray, f):
        key = round(f, 4)
        if key not in self._cache:
            h, w = gray.shape
            im = Image.fromarray((gray * 255).astype(np.uint8))
            self._cache[key] = np.asarray(im.resize((max(1, round(w * f)), max(1, round(h * f))),
                                                    Image.BILINEAR), dtype=np.float32) / 255
        return self._cache[key]

    def _search(self, gray, box, deadline=None):
        """Невідомий масштаб: оцінка з висоти рамки для кожного шрифту, грубий перебір
        ±15%, потім точний. Приймаємо лише осмислене прочитання (шум малюнка теж
        «читається» тонкими знаками — іііі, l*)."""
        h = box[3] - box[1]
        best = (-1.0, None)

        def trial(name, s):
            if deadline and time.time() > deadline:
                return -1.0
            f = self.fonts[name]
            if not 16 <= f.cell_h * s <= 110:
                return -1.0
            R = self.reader(name, s)
            # лише ділянка навколо рядка (зменшувати весь кадр на кожен масштаб — дорого)
            H0, W0 = gray.shape
            m = int(f.cell_h * s) + 8
            ya, yb, xa, xb = max(0, box[1] - m), min(H0, box[3] + m), max(0, box[0] - m), min(W0, box[2] + m)
            crop = Image.fromarray((gray[ya:yb, xa:xb] * 255).astype(np.uint8))
            G = np.asarray(crop.resize((max(1, round((xb - xa) * R.f)), max(1, round((yb - ya) * R.f))),
                                       Image.BILINEAR), dtype=np.float32) / 255
            b = tuple(int(round(v * R.f)) for v in (box[0] - xa, box[1] - ya, box[2] - xa, box[3] - ya))
            if not R.fits(b):
                return -1.0
            # дешево: перші 8 букв і перевірка лише прочитаного шматка рядка
            v, cy, ox, i = R.start(G, b)
            if v < START_MIN:
                return -1.0
            t, _m, _e, pos = R.decode(G, cy, ox, i, v, b[2], beam=2, limit=8)
            if not pos:
                return -1.0
            j, x = pos[-1]
            part = (b[0], b[1], min(b[2], int(x + R.adv[j])), b[3])
            return R.line_score(G, cy, pos, part) if plausible(t, 3) else -1.0

        # крок 2%: пік оцінки рядка вузький (1.02–1.07 — «Гучність музики», 0.99 — уже
        # «Гуннiсть», 0.92 — сміття); раз на розмір вікна, далі масштаб запам'ятовано
        for name in self.fonts:
            s0 = h / self.ink_h[name]
            # масштаби — на сітці 0.01: трафарети тоді перевикористовуються між кадрами
            grid = sorted({round(s0 * k, 2) for k in np.arange(0.84, 1.50, 0.02)})   # без хвостиків — більший
            for sc in grid:
                m = trial(name, sc)
                if m > best[0]:
                    best = (m, (name, sc))
        return best[1] if best[0] >= GOOD else None

    @staticmethod
    def _geo(b):
        return (b[0] // 16, b[1] // 16, (b[2] - b[0]) // 16, (b[3] - b[1]) // 8)

    def _best(self, gray, box):
        """Серед знайдених масштабів — той, з яким найкраще читаються перші букви;
        повністю читаємо лише ним (повне читання кожним — удвічі-втричі довше)."""
        ranked = []
        for name, s in self.known:
            R = self.reader(name, s)
            G = self._work(gray, R.f)
            b = tuple(int(round(v * R.f)) for v in box)
            if not R.fits(b):
                continue
            v, *_ = R.start(G, b)
            if v >= START_MIN and R.start_mean > 0:
                ranked.append((R.start_mean, name, s))
        got = ('', -1.0, None)
        for _m, name, s in sorted(ranked, reverse=True)[:2]:
            t, m = self._read_with(gray, box, name, s)
            if m > got[1] and plausible(t):
                got = (t, m, (name, s))
            if got[1] >= GOOD:
                break
        return got

    def read_frame(self, img, max_search=3, budget=2.0):
        """Кадр (PIL) -> [(текст, (x0, y0, x1, y1), кореляція)] для рядків, прочитаних певно.
        Невідомий масштаб підбираємо на найширших непрочитаних рядках (найімовірніше
        справжній текст), не більше max_search за кадр — підбір дорогий."""
        gray = np.asarray(img.convert('L'), dtype=np.float32) / 255
        if self.frame_h != gray.shape[0]:        # інше вікно — масштаби інші
            self.frame_h, self.known = gray.shape[0], []
        self._cache = {}
        boxes = lines(gray)
        # між кадрами більшість рядків та сама (меню — рухається лише курсор; діалог — лише
        # репліка; хибні рамки малюнка ті самі): прочитане пам'ятаємо за вмістом рамки
        if self.memo_known != self.known:        # нові масштаби — стара пам'ять неповна
            self.memo, self.memo_known = {}, list(self.known)
        keys = {b: (b, _content(gray, b)) for b in boxes}
        got = {}
        for b in boxes:
            m = self.memo.get(keys[b])
            got[b] = m if m is not None else self._best(gray, b)
        todo = [b for b in boxes if got[b][1] < GOOD and (b[2] - b[0]) >= 2.5 * (b[3] - b[1])]
        todo.sort(key=lambda b: -(b[2] - b[0]))
        # масштаби вже є і кадр читається — не шукаємо нових на кожному кадрі (шум малюнка
        # щоразу «непрочитаний»); підбір — поки масштабів немає або кадр не читається зовсім
        # де підбір уже не вдався (шум пейзажу щоразу на тих самих місцях) — не повторюємо
        self.frame_no += 1
        todo = [b for b in todo if self.failed.get(self._geo(b), -10**9) < self.frame_no - COOLDOWN]
        read_some = any(g[1] >= GOOD for g in got.values())
        if read_some and self.frame_no % 10:
            # кадр і так читається (діалог) — інший масштаб (табличка імені) шукаємо
            # лише раз на 10 кадрів: підбір — найдорожче, а шум малюнка щоразу «непрочитаний»
            todo = []
        t_end = time.time() + (30.0 if not self.known else budget)
        for b in todo[:max_search]:
            if time.time() > t_end:
                break
            if got[b][1] >= GOOD:                # уже прочитано новим масштабом
                continue
            fs = self._search(gray, b, t_end)
            if not fs:                           # і не вклався в час — теж: інакше щокадру наново
                self.failed[self._geo(b)] = self.frame_no
            if fs and fs not in self.known:
                self.known.append(fs)
                for bb in boxes:                 # новий масштаб — перечитати непрочитане
                    if got[bb][1] < GOOD:
                        t, m = self._read_with(gray, bb, *fs)
                        if m > got[bb][1] and plausible(t):
                            got[bb] = (t, m, fs)
        if self.memo_known == self.known:
            if len(self.memo) > 4000:
                self.memo = {}
            for b in boxes:
                self.memo[keys[b]] = got[b]
        out = []
        for b in boxes:
            t, m, fs = got[b]
            if m >= GOOD - 0.05 and plausible(t):
                out.append((t, b, m))
                if fs in self.known:             # найуживаніший масштаб — першим
                    self.known.remove(fs); self.known.insert(0, fs)
        self._cache = {}
        return out
