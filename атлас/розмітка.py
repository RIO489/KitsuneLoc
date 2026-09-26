#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Інструмент розмітки написів на картинках (для того, хто розмічає, не для перекладача).

  python атлас/розмітка.py огляд   <джерело>            сітка кадрів з номерами
  python атлас/розмітка.py додати  <джерело> <стиль> <тло> <кадр>=<текст> ...
                                   [--назва "Титульний екран"]
  python атлас/розмітка.py тло      <джерело> <кадр> <файл.png>   порожній кадр -> атлас/тло/
  python атлас/розмітка.py перевірка [джерело ...]      аркуш: оригінал | стерто |
                                                          наш англійський | український

<джерело> — як у написи.json: TTM3.bra/TEXTURE/title/titlemenu_difficulty.CL3
Картинки лягають у атлас/_огляд/. Гра береться з settings.json (ключ "msk").
"""
import json, os, sys
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')   # numpy (через openpyxl) інакше резервує ~30 МБ на кожне ядро

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw                                   # noqa: E402
from maryskelter import atlas as atl, dds                          # noqa: E402
from maryskelter.bra import Bra                                    # noqa: E402
from maryskelter.cl3 import Cl3                                    # noqa: E402

MARKS = os.path.join(HERE, 'написи.json')
OUT = os.path.join(HERE, '_огляд')
BG = (32, 32, 40, 255)
SAMPLE = 'Приклад Їжаків Ґанок Єдність Щастя'
# пробні переклади для аркуша перевірки (у гру не йдуть)
SAMPLES = {
    'START': 'ПОЧАТИ', 'LOAD GAME': 'ЗАВАНТАЖИТИ', 'BONUS': 'БОНУСИ', 'OPTION': 'НАЛАШТУВАННЯ',
    'QUIT': 'ВИХІД', 'DREAM': 'СОН', 'NORMAL': 'ЗВИЧАЙНА', 'HARD': 'ВАЖКА', 'HORROR': 'ЖАХ',
    'PUSH ANY BUTTON': 'НАТИСНІТЬ БУДЬ-ЯКУ КНОПКУ',
    'Skip': 'Пропустити', 'Purge': 'Очистити', 'Back': 'Назад', 'Save': 'Зберегти',
    'Transfer': 'Перенести', 'Remove': 'Прибрати', 'YES': 'ТАК', 'NO': 'НІ', 'OK': 'ГАРАЗД',
    'Weapons': 'Зброя', 'Head': 'Голова', 'Body': 'Тіло', 'Accessory 1': 'Аксесуар 1',
    'Accessory 2': 'Аксесуар 2', 'Have': 'Маєте', 'Item Name': 'Назва предмета',
    'Equippable Weapon': 'Доступна зброя', 'Check Equippable Weapon': 'Переглянути доступну зброю',
    'Toggle': 'Перемкнути', 'Floor:': 'Поверх:', 'Coordinates:': 'Координати:',
    'Controls': 'Керування', 'Saving...': 'Збереження...',
    "Alice's Room": 'Кімната Аліси', "Sleeping Beauty's Room": 'Кімната Сплячої красуні',
    'General Store': 'Крамниця', 'Talk': 'Розмова', 'Buy': 'Купити', 'Sell': 'Продати',
}


def game_dir():
    s = json.load(open(os.path.join(ROOT, 'settings.json'), encoding='utf-8'))
    return s['msk']


def read_cl3(src):
    arc, _, name = src.partition('/')
    bk = os.path.join(ROOT, 'backup', 'msk', arc)
    path = bk if os.path.exists(bk) else os.path.join(game_dir(), arc)
    return Cl3(Bra.read_some(path, [name])[name])


def load_marks():
    return json.load(open(MARKS, encoding='utf-8')) if os.path.exists(MARKS) else {}


def save_marks(m):
    with open(MARKS, 'w', encoding='utf-8') as f:
        f.write(dump(m))


def dump(m):
    """Компактно: один кадр — один рядок, щоб файл було зручно правити руками."""
    lines = ['{']
    srcs = list(m.items())
    for si, (src, mk) in enumerate(srcs):
        lines.append(f'  {json.dumps(src, ensure_ascii=False)}: {{')
        head = [(k, v) for k, v in mk.items() if k != 'кадри']
        for k, v in head:
            lines.append(f'    {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)},')
        lines.append('    "кадри": {')
        fr = sorted(mk.get('кадри', {}).items(), key=atl.order)
        for fi, (i, spec) in enumerate(fr):
            comma = ',' if fi < len(fr) - 1 else ''
            lines.append(f'      {json.dumps(i)}: {json.dumps(spec, ensure_ascii=False)}{comma}')
        lines.append('    }')
        lines.append('  }' + (',' if si < len(srcs) - 1 else ''))
    lines.append('}')
    return '\n'.join(lines) + '\n'


STYLES = os.path.join(HERE, 'стилі.json')


def load_styles():
    return json.load(open(STYLES, encoding='utf-8'))


def save_styles(st):
    """Один стиль — один рядок: так файл легко читати й правити руками."""
    items = list(st.items())
    lines = ['{']
    for k, (name, v) in enumerate(items):
        comma = ',' if k < len(items) - 1 else ''
        lines.append(f'  {json.dumps(name, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}{comma}')
    lines.append('}')
    with open(STYLES, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def texture(cl3, stem=None):
    return dds.decode(bytes(atl.pair(cl3, stem)[1][1]))


def short(src):
    return src.rsplit('/', 1)[-1].rsplit('.', 1)[0]


def cmd_overview(src):
    cl3 = read_cl3(src)
    img = texture(cl3)
    g = Image.new('RGBA', img.size, BG)
    g.alpha_composite(img)
    d = ImageDraw.Draw(g)
    marks = load_marks().get(src, {}).get('кадри', {})
    for i, (x0, y0, x1, y1) in atl.frames(cl3).items():
        col = (0, 255, 120) if str(i) in marks else (255, 80, 80)
        d.rectangle((x0, y0, x1 - 1, y1 - 1), outline=col)
        d.text((x0 + 2, y0 + 1), str(i), fill=(255, 255, 0))
        print(i, x0, y0, x1, y1)
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, short(src) + '_сітка.png')
    g.save(p)
    print(p)


def period(frame, area, lo=16, hi=120):
    """Крок смуг по горизонталі: зсув, за якого рядки над і під написом
    найкраще збігаються самі з собою."""
    g = frame.convert('L').load()
    a = frame.getchannel('A').load()
    w, h = frame.size
    rows = [y for y in list(range(max(0, area[1] - 14), area[1] - 2)) +
            list(range(area[3] + 2, min(h, area[3] + 14)))]
    best, best_p = None, lo
    for p in range(lo, min(hi, w // 2)):
        s = n = 0
        for y in rows:
            for x in range(0, w - p, 2):
                if a[x, y] > 200 and a[x + p, y] > 200:
                    s += abs(g[x, y] - g[x + p, y])
                    n += 1
        if n > 50 and (best is None or s / n < best):
            best, best_p = s / n, p
    return best_p


def after_icon(frame, gap=6):
    """x, з якого починається текст після іконки: перший вертикальний проміжок
    без пікселів шириною ≥ gap після першого непорожнього стовпця."""
    a = frame.getchannel('A').point(lambda v: 255 if v > 60 else 0)
    w, h = frame.size
    cols = [a.crop((x, 0, x + 1, h)).getbbox() is not None for x in range(w)]
    x = next((i for i, c in enumerate(cols) if c), None)
    if x is None:
        return None
    run = 0
    while x < w:
        run = run + 1 if not cols[x] else 0
        if run >= gap:
            nxt = next((i for i in range(x, w) if cols[i]), None)
            return max(0, nxt - 2) if nxt is not None else None
        x += 1
    return None


def cmd_add(src, style, bg, pairs, title=None, opts=None):
    opts = opts or {}
    m = load_marks()
    mk = m.setdefault(src, {})
    if title:
        mk['назва'] = title
    mk.setdefault('кадри', {})
    cl3 = read_cl3(src)
    img, boxes = texture(cl3, mk.get('текстура')), atl.frames(cl3, mk.get('текстура'))
    for p in pairs:
        i, _, text = p.partition('=')
        i, _, rect = i.partition('@')                 # ключ@x0,y0,x1,y1 — рамка вручну
        text, _, like = text.partition('~')           # текст~кадр — геометрія як у того кадру
        text = text.replace('\\n', '\n')
        spec = {'текст': text}
        if rect:
            spec['рамка'] = [int(v) for v in rect.split(',')]
        box = atl.box_of(i, spec, boxes)
        if like:
            src_spec = mk['кадри'][like]
            spec.update({'стиль': style, 'тло': bg})
            for k in ('область', 'літери', 'поле', 'вирівняти'):
                if k in src_spec:
                    spec[k] = src_spec[k]
        else:
            crop = img.crop(box)
            if opts.get('--обертання'):
                crop = crop.rotate(-int(opts['--обертання']), expand=True)
                spec['обертання'] = int(opts['--обертання'])
            area, let = atl.suggest(crop, bg)
            spec.update({'стиль': style, 'тло': bg, 'область': area})
            if let:
                spec['літери'] = let
            if area:
                w = crop.width
                spec['поле'] = atl.suggest_field(area, w, bg)
                if opts.get('--ліво'):
                    spec['вирівняти'] = 'ліво'
                    spec['поле'] = [area[0], round(w * 0.94)]
                elif opts.get('--право'):
                    spec['вирівняти'] = 'право'
                    spec['поле'] = [round(w * 0.06), area[2]]
        if opts.get('--після-іконки') and spec.get('область'):
            x_text = after_icon(img.crop(box))
            if x_text:
                a0 = spec['область']
                spec['область'] = [x_text] + a0[1:]
                spec['поле'] = [x_text, spec['поле'][1]]
                spec['вирівняти'] = 'ліво'
        if opts.get('--розширити') and spec.get('область'):
            g = int(opts['--розширити'])
            w, h = (crop.size if not like else (box[2] - box[0], box[3] - box[1]))
            a0 = spec['область']
            spec['область'] = [max(0, a0[0] - g), max(0, a0[1] - g), min(w, a0[2] + g), min(h, a0[3] + g)]
        if bg == 'шаблон':
            spec['зразок'] = list(boxes[int(opts['--зразок'])][:2])
        if bg == 'картинка':
            spec['файл'] = opts['--файл']
        if bg == 'смуги':
            spec['період'] = period(img.crop(box), spec['область'])
        if opts.get('--правки'):
            spec['правки'] = json.loads(opts['--правки'])
        mk['кадри'][i] = spec
        print(i, spec)
    save_marks(m)


def find_lines(img, region, light=150, gap=28, min_h=8, ch='g'):
    """Рядки світлого тексту в області атласу: [(x0, y0, x1, y1)] в координатах атласу.
    Слова, між якими менше `gap` пікселів, — один напис."""
    from PIL import ImageChops
    x0, y0, x1, y1 = region
    f = img.crop(region)
    r_, g, b, a = f.split()
    c = {'g': g, 'r': r_, 'b': b}[ch]
    m = ImageChops.multiply(c.point(lambda v: 255 if v > light else 0),
                            a.point(lambda v: 255 if v > 200 else 0))
    w, h = f.size
    px = m.load()
    rows = [any(px[x, y] for x in range(0, w, 1)) for y in range(h)]
    out, y = [], 0
    while y < h:
        if not rows[y]:
            y += 1
            continue
        ys = y
        while y < h and rows[y]:
            y += 1
        if y - ys < min_h:
            continue
        cols = [any(px[x, yy] for yy in range(ys, y)) for x in range(w)]
        x = 0
        while x < w:
            if not cols[x]:
                x += 1
                continue
            xs, run, xe = x, 0, x
            while x < w and run < gap:
                if cols[x]:
                    run, xe = 0, x + 1
                else:
                    run += 1
                x += 1
            out.append((x0 + xs, y0 + ys, x0 + xe, y0 + y))
    return out


def cmd_lines(src, region):
    cl3 = read_cl3(src)
    img = texture(cl3)
    fr = atl.frames(cl3)
    reg = fr[int(region)] if region.isdigit() else tuple(int(v) for v in region.split(','))
    for b in find_lines(img, reg):
        print(','.join(map(str, b)), f'{b[2] - b[0]}x{b[3] - b[1]}')


def cmd_audit():
    """Кожна рамка — всередині кадру нарізки, область — всередині рамки."""
    bad = 0
    for src, mk in load_marks().items():
        cl3 = read_cl3(src)
        fr = atl.frames(cl3, mk.get('текстура'))
        for i, s in mk['кадри'].items():
            box = atl.box_of(i, s, fr)
            if box is None:
                print(f'{src} {i}: немає кадру'); bad += 1; continue
            if s.get('рамка') and not s.get('без кадру') and not any(f[0] <= box[0] and f[1] <= box[1] and box[2] <= f[2]
                                          and box[3] <= f[3] for f in fr.values()):
                print(f'{src} {i}: рамка {box} не всередині жодного кадру'); bad += 1
            w, h = box[2] - box[0], box[3] - box[1]
            if s.get('обертання') in (90, -90, 270):
                w, h = h, w
            a = s.get('область')
            if not a or a[0] < 0 or a[1] < 0 or a[2] > w or a[3] > h or a[0] >= a[2] or a[1] >= a[3]:
                print(f'{src} {i}: область {a} поза рамкою {w}x{h}'); bad += 1
    print('проблем:', bad)


def cmd_bg(src, i, name):
    """Зберегти порожній кадр як готове тло для режиму "картинка"."""
    cl3 = read_cl3(src)
    img = texture(cl3)
    os.makedirs(os.path.join(HERE, 'тло'), exist_ok=True)
    img.crop(atl.frames(cl3)[int(i)]).save(os.path.join(HERE, 'тло', name))
    print(name)


def cmd_check(srcs):
    m = load_marks()
    styles = atl.load_json('стилі.json')
    os.makedirs(OUT, exist_ok=True)
    for src in srcs or list(m):
        mk = m[src]
        cl3 = read_cl3(src)
        img, boxes = texture(cl3, mk.get('текстура')), atl.frames(cl3, mk.get('текстура'))
        rows = []
        for i, spec in sorted(mk['кадри'].items(), key=atl.order):
            box = atl.box_of(i, spec, boxes)
            orig = img.crop(box)
            er = img.copy()
            atl.erase(er, box, spec)
            cells = [orig, er.crop(box)]
            for text in (spec['текст'], SAMPLES.get(atl.key_of(spec)) or SAMPLE[:max(4, len(spec['текст']))]):
                t = img.copy()
                w = atl.draw(t, box, spec, styles, text)
                cells.append(t.crop(box))
                if w:
                    print(f'  {i}: {w}')
            rows.append((i, cells))
        cw = max(c.width for _i, cs in rows for c in cs)
        H = sum(max(c.height for c in cs) + 8 for _i, cs in rows)
        sheet = Image.new('RGBA', (40 + 4 * (cw + 8), H), (90, 90, 96, 255))
        d = ImageDraw.Draw(sheet)
        y = 0
        for i, cs in rows:
            d.text((4, y + 4), i, fill=(255, 255, 0))
            for k, c in enumerate(cs):
                sheet.alpha_composite(c, (40 + k * (cw + 8), y))
            y += max(c.height for c in cs) + 8
        p = os.path.join(OUT, short(src) + '_перевірка.png')
        sheet.save(p)
        print(p)


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    if a[0] == 'огляд':
        cmd_overview(a[1])
    elif a[0] == 'додати':
        opts = {}
        for flag, has_val in (('--назва', True), ('--зразок', True), ('--правки', True),
                              ('--розширити', True), ('--файл', True), ('--обертання', True),
                              ('--ліво', False), ('--право', False), ('--після-іконки', False)):
            if flag in a:
                k = a.index(flag)
                opts[flag] = a[k + 1] if has_val else True
                a = a[:k] + a[k + (2 if has_val else 1):]
        cmd_add(a[1], a[2], a[3], a[4:], opts.get('--назва'), opts)
    elif a[0] == 'контроль':
        cmd_audit()
    elif a[0] == 'рядки':
        cmd_lines(a[1], a[2])
    elif a[0] == 'тло':
        cmd_bg(a[1], a[2], a[3])
    elif a[0] == 'перевірка':
        cmd_check(a[1:])
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
