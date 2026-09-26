"""Написи на картинках Neptunia Re;Birth1: текстури .tid у архівах .pac.

Малювання, стирання, стилі й шрифти — спільні з Mary Skelter
(maryskelter/atlas.py, атлас/стилі.json, атлас/шрифти/). Тут лише те, що
відрізняється: джерело текстури (.tid у .pac, див. tid.py) і розмітка —
`атлас/нептун.json`:

  {"data/GAME00000.pac/menu/item/title.tid": {
      "назва": "Заголовок меню: Items",
      "кадри": {"<ключ>": {"рамка": [x0, y0, x1, y1],   # у пікселях текстури
                          "текст": "ITEMS", "стиль": "...", "тло": "прозорий",
                          "область": [...], "літери": [...], "поле": [...],
                          "вирівняти": "ліво"}}}}

Рамка — зазвичай AREA спрайта з парного .ssa (ssa.py); решта координат,
як і в MSK, — відносно рамки. Ключ рядка книги — текст (однакові написи
перекладаються один раз).
"""
from maryskelter import atlas as atl
from .tid import Tid

MARKS = 'нептун.json'


def load_marks():
    import os
    if not os.path.exists(os.path.join(atl.DIR, MARKS)):
        return {}
    return {k: v for k, v in atl.load_json(MARKS).items() if not k.startswith('_')}


def split_src(src):
    """'data/GAME00000.pac/menu/x/title.tid' -> ('data/GAME00000.pac', 'menu/x/title.tid')"""
    i = src.lower().index('.pac/') + 4
    return src[:i], src[i + 1:]


def source_text(spec):
    """Англійський оригінал рядка книги. Слово, розрізане на кілька спрайтів
    (VICTOR + Y!!), має спільний "ключ" і повний текст у "повністю"."""
    return spec.get('повністю') or spec['текст']


def text_for(spec, tr):
    """Що малювати в цьому кадрі: "малювати" — завжди те саме (хвіст «!!»
    розрізаного слова), "обрізати" — прибрати ці символи з кінця перекладу
    (вони намальовані в сусідньому кадрі)."""
    if spec.get('малювати'):
        return spec['малювати']
    if 'частина' in spec:                   # два спрайти поруч (NEW | RECORD!): слово — сюди,
        parts = tr.split(' ', 1)             # решта — у другий
        i = spec['частина']
        return parts[i] if i < len(parts) else ''
    if spec.get('обрізати'):
        return tr.rstrip(spec['обрізати']) or tr
    return tr


def rebuild(blob, mark, tr, styles):
    """Перемалювати написи однієї текстури. -> (байти .tid | None, к-сть, [попередження])."""
    todo = [(k, s) for k, s in mark['кадри'].items() if tr.get(atl.key_of(s))]
    if not todo:
        return None, 0, []
    t = Tid(blob)
    img = t.image()
    warns, rects = [], []
    for k, spec in sorted(todo, key=atl.order):
        box = tuple(spec['рамка'])
        text = text_for(spec, tr[atl.key_of(spec)])
        warns += [f'{k}: {w}' for w in atl.draw(img, box, spec, styles, text)]
        rects.append(box)
    return t.patch(img, rects), len(rects), warns


def sprite(blob, spec):
    """Оригінальний спрайт кадру (RGBA) — для прев'ю в книзі."""
    return Tid(blob).image().crop(tuple(spec['рамка']))
