# -*- coding: utf-8 -*-
"""Свої картинки перекладача: текстура гри, перемальована вручну (Photoshop, GIMP…).

Вікно «Знайти написи» вивантажує чистий оригінал текстури в PNG у теку
`Переклад\\<гра>\\Свої картинки\\`; перекладач малює й кладе файл назад (кнопка
«Завантажити свою…» або просто зберігає поверх). При «2. Залити переклад у гру»
така картинка стає оригіналом цієї текстури: розмічені написи програма й далі
малює поверх, у BC7/DXT перекодовуються лише блоки 4×4, що відрізняються від
оригіналу. Прибрав файл — у грі знову оригінал.

Ім'я файла — джерело текстури з «__» замість «/» (+ «@текстура», якщо в атласі
CL3 кілька текстур), напр. `TTM3.bra__TEXTURE__bonus__Novel__000001.dds.png`.
"""
import os

from PIL import Image

FOLDER = 'Свої картинки'


def folder(xl_dir):
    return os.path.join(xl_dir, FOLDER)


def name_of(src, stem=''):
    return src.replace('/', '__') + (f'@{stem}' if stem else '') + '.png'


def parse(fn):
    """'TTM3.bra__TEXTURE__x.CL3@stem.png' -> ('TTM3.bra/TEXTURE/x.CL3', 'stem') | None."""
    if not fn.lower().endswith('.png') or '__' not in fn:
        return None
    src, _, stem = fn[:-4].partition('@')
    return src.replace('__', '/'), stem


def found(xl_dir):
    """{джерело: {текстура: шлях до PNG}} — усі свої картинки гри."""
    d = folder(xl_dir) if xl_dir else None
    out = {}
    if not d or not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        got = parse(fn)
        if got:
            out.setdefault(got[0], {})[got[1]] = os.path.join(d, fn)
    return out


def load(path, orig):
    """Своя картинка як RGBA розміру оригіналу `orig`. Без прозорості в файлі (редактор
    зберіг RGB) — прозорість береться з оригіналу, інакше прозоре в грі стало б чорним."""
    im = Image.open(path)
    im.load()
    if im.size != orig.size:
        raise ValueError(f'{os.path.basename(path)}: розмір {im.size[0]}×{im.size[1]}, '
                         f'а текстура гри — {orig.size[0]}×{orig.size[1]}')
    if 'A' not in im.getbands() and 'transparency' not in im.info:
        im = im.convert('RGB')
        im.putalpha(orig.getchannel('A'))
        return im
    return im.convert('RGBA')


def changed_rects(orig, img):
    """Прямокутники з блоків 4×4, де `img` відрізняється від `orig` (суміжні блоки рядка —
    одним прямокутником): перекодовуємо лише їх, решта байтів текстури — оригінальні."""
    import numpy as np
    a = np.asarray(orig.convert('RGBA'))
    b = np.asarray(img.convert('RGBA'))
    h, w = a.shape[:2]
    H4, W4 = (h + 3) // 4, (w + 3) // 4
    d = np.zeros((H4 * 4, W4 * 4), bool)
    d[:h, :w] = np.any(a != b, axis=2)
    d4 = d.reshape(H4, 4, W4, 4).any(axis=(1, 3))
    rects = []
    for by in range(H4):
        xs = np.nonzero(d4[by])[0]
        if not len(xs):
            continue
        start = prev = xs[0]
        for x in list(xs[1:]) + [None]:
            if x is not None and x == prev + 1:
                prev = x
                continue
            rects.append((int(start) * 4, by * 4, min(w, (int(prev) + 1) * 4), min(h, by * 4 + 4)))
            if x is not None:
                start = prev = x
    return rects
