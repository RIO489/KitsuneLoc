# -*- coding: utf-8 -*-
"""«Справжні літери»: кирилицю, що виглядає як латиниця, беремо прямо з
оригінального напису.

А В Е К М Н О Р С Т Х І (і малі а е о р с х і у), а також цифри й розділові
знаки на картинці вже намальовані — з тим самим шрифтом, обведенням,
сяйвом і градієнтом, що й решта напису. Вирізаємо їх з оригіналу
попіксельно, а шрифтом домальовуємо лише решту літер. Для коротких написів
(ITEMS, SKILL, START…) часто виходить, що половина слова — «справжня».

Як працює:
  1. кадр «випрямляємо» (знімаємо нахил стилю), щоб літери стояли вертикально
     й розділялися порожніми стовпчиками;
  2. маска самих літер (letters_mask) → проміжки між літерами →
     шматки; шматків має бути рівно стільки, скільки літер в оригіналі,
     інакше (злиплі літери) — відмова, напис малюється звичайно;
  3. прозорість шматка — наскільки оригінал відрізняється від стертого тла
     (тож разом з літерою переносимо її обведення й сяйво, але не кнопку);
  4. складаємо переклад: справжні шматки + літери, намальовані стилем;
     проміжок між літерами — середній проміжок оригіналу;
  5. повертаємо нахил і ставимо в поле за правилом «вирівняти».
Вмикається в розмітці кадру: "літери з оригіналу": true.
"""
import numpy as np
from PIL import Image

from . import atlas as atl

# кирилиця -> латинська літера того ж вигляду
HOMO = {'А': 'A', 'В': 'B', 'Е': 'E', 'К': 'K', 'М': 'M', 'Н': 'H', 'О': 'O', 'Р': 'P',
        'С': 'C', 'Т': 'T', 'Х': 'X', 'І': 'I',
        'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'х': 'x', 'і': 'i', 'у': 'y'}
SAME = set('0123456789!?.,:;-+%/&()\'"')
MIN_SX = atl.MIN_SX


# ------------------------------------------------------------- маска літер
def _kmeans(x, k=3, it=12):
    """Прості k-середні для кольорів (N×3)."""
    qs = np.quantile(x.sum(1), np.linspace(0.05, 0.95, k))
    c = np.array([x[np.argmin(abs(x.sum(1) - q))] for q in qs], dtype=float)
    for _ in range(it):
        lab = np.argmin(((x[:, None, :] - c[None]) ** 2).sum(2), 1)
        for j in range(k):
            if (lab == j).any():
                c[j] = x[lab == j].mean(0)
    return c, lab


def _fill_rgb(st):
    f = st.get('заливка', '#ffffff')
    cols = f if isinstance(f, list) else [f]
    return np.array([atl._rgba(c)[:3] for c in cols], dtype=float).mean(0)


def letters_mask(frame, spec, st):
    """Маска самих літер (без обведення й сяйва) розміром з кадр: кластер
    кольорів області, найближчий до заливки стилю."""
    ax0, ay0, ax1, ay1 = spec['область']
    ly0, ly1 = spec.get('літери', [ay0, ay1])
    y0, y1 = max(ay0, ly0 - 3), min(ay1, ly1 + 3)
    crop = frame.crop((ax0, y0, ax1, y1))
    a = np.asarray(crop.convert('RGBA')).astype(float)
    rgb = a[..., :3] * (a[..., 3:] / 255.0)        # прозоре — як чорне
    x = rgb.reshape(-1, 3)
    c, lab = _kmeans(x)
    j = int(np.argmin(((c - _fill_rgb(st)) ** 2).sum(1)))
    far = int(np.argmax(((c - c[j]) ** 2).sum(1)))
    mid = next((q for q in range(len(c)) if q not in (j, far)), None)
    seg = c[j] - c[far]
    L2 = float((seg ** 2).sum()) or 1.0
    on_line = False
    if mid is not None:
        t = float(((c[mid] - c[far]) * seg).sum()) / L2
        off = np.sqrt((((c[mid] - c[far]) - t * seg) ** 2).sum() / L2)
        on_line = 0 < t < 1 and off < 0.15
    if on_line:
        # проміжний колір — лише згладжені краї між тлом і заливкою: беремо
        # піксель, якщо він покритий заливкою хоча б наполовину
        cov = ((x - c[far]) @ seg) / L2
        m = (cov > 0.5).reshape(rgb.shape[:2])
    else:
        # проміжний колір — справжнє обведення: літери — лише кластер заливки
        m = (lab == j).reshape(rgb.shape[:2])
    full = np.zeros((frame.height, frame.width), bool)
    full[y0:y0 + m.shape[0], ax0:ax0 + m.shape[1]] = m
    return full


def _shear(im, s, base, back=False):
    """Зняти (back=False) чи повернути нахил навколо базової лінії `base`."""
    if not s:
        return im
    k = s if back else -s
    return im.transform(im.size, Image.AFFINE, (1, k, -k * base, 0, 1, 0), Image.BICUBIC)


def _segments(mask, ly0, ly1):
    """Стовпчики з чорнилом у рядках літер -> [(x0, x1)]."""
    cols = mask[ly0:ly1 + 1].any(0)
    segs, x = [], 0
    while x < len(cols):
        if cols[x]:
            s = x
            while x < len(cols) and cols[x]:
                x += 1
            segs.append((s, x))
        x += 1
    # дрібниці (крапка над i, відірваний піксель) приліплюємо до сусіда
    out = []
    for a, b in segs:
        if out and (b - a <= 1 or a - out[-1][1] <= 0):
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def pieces(orig, erased, spec, st):
    """{латинський символ: (RGBA шматок на всю висоту кадру, x0 чорнила, x1 чорнила)}
    і середній проміжок між літерами — у «випрямленому» просторі кадру.
    None — розрізати не вдалося."""
    text = spec['текст']
    if st.get('регістр') == 'великі':
        text = text.upper()
    if '\n' in text:
        return None
    letters = [c for c in text if not c.isspace()]
    ly0, ly1 = spec.get('літери', spec['область'][1::2])
    s = st.get('нахил', 0)
    mask = letters_mask(orig, spec, st)
    mimg = _shear(Image.fromarray((mask * 255).astype(np.uint8)), s, ly1)
    segs = _segments(np.asarray(mimg) > 110, ly0, ly1)
    if len(segs) != len(letters):
        return None
    # прозорість: чим оригінал відрізняється від стертого тла
    # (колір прозорого пікселя нічого не значить: порівнюємо колір×альфа і саму альфу)
    a = np.asarray(orig.convert('RGBA')).astype(int)
    b = np.asarray(erased.convert('RGBA')).astype(int)
    pa = a[..., :3] * a[..., 3:] // 255
    pb = b[..., :3] * b[..., 3:] // 255
    diff = np.maximum(np.abs(pa - pb).max(2), np.abs(a[..., 3] - b[..., 3]))
    alpha = np.clip(diff * 4, 0, 255).astype(np.uint8)
    ax0, ay0, ax1, ay1 = spec['область']
    keep = np.zeros_like(alpha)
    keep[ay0:ay1, ax0:ax1] = alpha[ay0:ay1, ax0:ax1]
    src = orig.convert('RGBA').copy()
    src.putalpha(Image.fromarray(keep))
    src = _shear(src, s, ly1)
    out = {}
    for k, (ch, (x0, x1)) in enumerate(zip(letters, segs)):
        left = (segs[k - 1][1] + x0) // 2 if k else max(0, x0 - (segs[1][0] - segs[0][1] if len(segs) > 1 else 4))
        right = (x1 + segs[k + 1][0] + 1) // 2 if k + 1 < len(segs) else \
            min(src.width, x1 + (segs[-1][0] - segs[-2][1] if len(segs) > 1 else 4))
        if ch not in out:
            out[ch] = (src.crop((left, 0, right, src.height)), x0 - left, x1 - left)
    gaps = [segs[k + 1][0] - segs[k][1] for k in range(len(segs) - 1)]
    gap = sum(gaps) / len(gaps) if gaps else 2
    # обведення сусідніх літер злиті — до шматка прилипли б чужі контури
    edge = max(st.get('обведення', {}).get('товщина', 0), st.get('обведення2', {}).get('товщина', 0))
    if gaps and min(gaps) < 2 * edge - 1:
        return None
    rows = np.nonzero(np.asarray(mimg)[:, :] > 110)[0]
    return out, gap, (int(rows.min()), int(rows.max()) + 1)


def compose(orig, erased, spec, st, text, size, base_y, field):
    """Шар розміром з кадр з перекладом, де «однакові з латиницею» літери —
    справжні. None — не вийшло (тоді малюємо звичайно).
    `size` — кегль, `base_y` — базова лінія, `field` — (x0, x1) поля."""
    if '\n' in text:
        return None
    got = pieces(orig, erased, spec, st)
    if got is None:
        return None
    cut, gap, (my0, my1) = got
    if st.get('регістр') == 'великі':
        text = text.upper()
    flat = dict(st, нахил=0)
    flat.pop('поворот', None)
    # кегль і базова лінія — за виміряними справжніми літерами, а не за розміткою
    # («літери» в розмітці буває навмисно зменшено, щоб компенсувати обведення)
    src = spec['текст'].upper() if st.get('регістр') == 'великі' else spec['текст']
    size = atl.fit_size(src, flat, my1 - my0)
    rby, rcb = atl.measure(src, flat, size, 'ліво')
    base_y = my0 + (rby - rcb[1])
    items = []                             # (RGBA на висоту кадру, x0 чорнила, x1 чорнила)
    real = 0
    H = orig.height
    for ch in text:
        lat = HOMO.get(ch, ch if ch in SAME else None)
        if ch == ' ':
            items.append(None)
            continue
        if lat is not None and lat in cut:
            items.append(cut[lat])
            real += 1
            continue
        im, _bx, by, cb = atl.render(ch, flat, size, 'ліво')
        layer = Image.new('RGBA', (im.width, H), (0, 0, 0, 0))
        layer.paste(im, (0, round(base_y - by)), im)
        # межі тіла літери (без обведення й сяйва) — як і в справжніх шматків
        items.append((layer, cb[0], cb[2]))
    if not real:
        return None                        # жодної справжньої літери — сенсу нема
    space = atl._font(flat, size * atl.SS).getlength(' ') / atl.SS
    # розкладка у «випрямленому» просторі
    xs, x, prev = [], 0.0, None
    for it in items:
        if it is None:
            x += space
            prev = None
            continue
        if prev is not None:
            x += gap
        xs.append((it, x - it[1]))
        x += it[2] - it[1]
        prev = it
    width = x
    fw = field[1] - field[0]
    pad = int(abs(st.get('нахил', 0)) * H) + 8
    W = int(width) + 2 * pad + 4
    line = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    for (im, _a, _b), px in xs:
        line.alpha_composite(im, (int(round(px)) + pad, 0))
    line = _shear(line, st.get('нахил', 0), base_y, back=True)
    ink = atl._ink(line, 8)
    if not ink:
        return None
    # стискаємо за повною шириною (з обведенням і сяйвом), як і звичайний шлях
    sx = min(1.0, fw / max(1, ink[2] - ink[0]))
    if sx < MIN_SX:
        return None                        # треба зменшувати кегль — хай малює звичайний шлях
    if sx < 1.0:
        line = line.resize((max(1, round(line.width * sx)), H), Image.LANCZOS)
        ink = atl._ink(line, 8)
    how = spec.get('вирівняти', 'центр')
    w = ink[2] - ink[0]
    left = field[0] if how == 'ліво' else field[1] - w if how == 'право' else field[0] + (fw - w) / 2
    layer = Image.new('RGBA', orig.size, (0, 0, 0, 0))
    layer.paste(line, (int(round(left - ink[0])), 0), line)
    return layer, real, sx
