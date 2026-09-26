#!/usr/bin/env python3
"""Розмітка написів на картинках Neptunia Re;Birth1 (атлас/нептун.json).

  python атлас/розмітка_неп.py кадри     <src>                  рамки з .ssa + огляд у _огляд/
  python атлас/розмітка_неп.py додати    <src> <стиль> <тло> <рамка>=<текст> ... [--право|--ліво|--центр] [--назва "…"]
         <рамка> — x0,y0,x1,y1 у пікселях текстури або номер кадру з «кадри»;
         область і висота літер міряються самі (тло «прозорий» — по альфі,
         «рядки» — по світлому тексту на плашці)
  python атлас/розмітка_неп.py перевірка [src…]                 аркуш: оригінал|стерто|EN|UA
  python атлас/розмітка_неп.py контроль                         межі областей (має бути 0)

<src> = data/GAME00000.pac/menu/item/title.tid. Текстури читаються з чистих
оригіналів (backup\\nep, інакше тека гри).
"""
import json, os, sys
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')   # numpy (через openpyxl) інакше резервує ~30 МБ на кожне ядро
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from PIL import Image, ImageDraw                                    # noqa: E402
from maryskelter import atlas as atl                              # noqa: E402
from neptunia import ssa                                          # noqa: E402
from neptunia.pac import Pac                                      # noqa: E402
from neptunia.tid import Tid                                      # noqa: E402
from neptunia import atlas as natl                                # noqa: E402

OUT = os.path.join(HERE, '_огляд')
SAMPLE = 'Українська'
_pacs = {}


def game_dir():
    try:
        s = json.load(open(os.path.join(ROOT, 'settings.json'), encoding='utf-8'))
        if s.get('nep'):
            return s['nep']
    except (OSError, ValueError):
        pass
    sys.path.insert(0, ROOT)
    import runpy
    gui = runpy.run_path(os.path.join(ROOT, 'Переклад.pyw'), run_name='x')
    return gui['find_game'](gui['GAMES']['nep'])


def pac(arc):
    if arc not in _pacs:
        bk = os.path.join(ROOT, 'backup', 'nep', *arc.split('/'))
        _pacs[arc] = Pac(bk if os.path.exists(bk) else os.path.join(game_dir(), *arc.split('/')))
    return _pacs[arc]


def read(src):
    arc, inner = natl.split_src(src)
    return pac(arc).read(inner)


def texture(src):
    return Tid(read(src)).image()


def ssa_frames(src):
    """Рамки з усіх .ssa у тій самій теці архіву."""
    arc, inner = natl.split_src(src)
    folder = inner.rsplit('/', 1)[0].replace('/', '\\').lower() + '\\'
    p = pac(arc)
    blobs = [p.read(e) for e in p.entries
             if e.name.lower().startswith(folder) and e.name.lower().endswith('.ssa')
             and '\\' not in e.name[len(folder):]]
    return ssa.frames(blobs)


def load_marks():
    p = os.path.join(HERE, natl.MARKS)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}


def save_marks(m):
    """Одна текстура — один блок, один кадр — один рядок (зручно дивитись дифи)."""
    out = ['{']
    items = list(m.items())
    for n, (src, mk) in enumerate(items):
        head = {k: v for k, v in mk.items() if k != 'кадри'}
        out.append(f'  {json.dumps(src, ensure_ascii=False)}: {{')
        for k, v in head.items():
            out.append(f'    {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)},')
        out.append('    "кадри": {')
        fr = list(mk.get('кадри', {}).items())
        for j, (k, s) in enumerate(fr):
            out.append(f'      {json.dumps(k, ensure_ascii=False)}: {json.dumps(s, ensure_ascii=False)}'
                       + (',' if j < len(fr) - 1 else ''))
        out.append('    }')
        out.append('  }' + (',' if n < len(items) - 1 else ''))
    out.append('}')
    with open(os.path.join(HERE, natl.MARKS), 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('\n'.join(out) + '\n')


# ------------------------------------------------------------------- заміри
def measure(frame, bg):
    """(область, літери) у координатах кадру."""
    w, h = frame.size
    px = frame.load()
    if bg == 'прозорий':
        a = frame.getchannel('A').point(lambda v: 255 if v > 8 else 0)
        bb = a.getbbox()
        core = [y for y in range(h)
                if sum(1 for x in range(w) if px[x, y][3] > 200 and sum(px[x, y][:3]) > 450) >= 2]
    else:
        # світлий текст на темній плашці
        bright = [[sum(px[x, y][:3]) > 330 and px[x, y][3] > 200 for x in range(w)] for y in range(h)]
        xs = [x for y in range(h) for x in range(w) if bright[y][x]]
        ys = [y for y in range(h) if any(bright[y])]
        bb = (min(xs), min(ys), max(xs) + 1, max(ys) + 1) if xs else None
        core = ys
    if not bb:
        return None, None
    pad = 2
    area = [max(0, bb[0] - pad), max(0, bb[1] - pad), min(w, bb[2] + pad), min(h, bb[3] + pad)]
    let = [min(core), max(core) + 1] if core else [area[1], area[3]]
    return area, let


def cmd_frames(src):
    img = texture(src)
    fr = ssa_frames(src)
    sheet = Image.new('RGBA', img.size, (60, 60, 80, 255))
    sheet.alpha_composite(img)
    d = ImageDraw.Draw(sheet)
    for i, b in fr.items():
        d.rectangle([b[0], b[1], b[2] - 1, b[3] - 1], outline=(255, 255, 0, 255))
        d.text((b[0] + 2, b[1] + 1), str(i), fill=(255, 255, 0, 255))
        print(f'{i:3}  {b}')
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, src.replace('/', '_') + '.png')
    sheet.save(p)
    print('огляд:', p)


def cmd_add(src, style, bg, pairs, title=None, align='ліво'):
    m = load_marks()
    mk = m.setdefault(src, {'назва': title or src.rsplit('/', 2)[-2], 'кадри': {}})
    if title:
        mk['назва'] = title
    img = texture(src)
    fr = None
    for pair in pairs:
        where, text = pair.split('=', 1)
        text = text.replace('\\n', '\n')
        if ',' in where:
            box = [int(v) for v in where.split(',')]
            key = where
        else:
            fr = fr or ssa_frames(src)
            box = list(fr[int(where)])
            key = where
        area, let = measure(img.crop(box), bg)
        if area is None:
            print(f'! {where}: у рамці нічого не видно')
            continue
        spec = {'рамка': box, 'текст': text, 'стиль': style, 'тло': bg,
                'область': area, 'літери': let,
                # ліво — від початку старого напису до краю кадру; центр/право — весь кадр
                'поле': [area[0] if align == 'ліво' else 2, box[2] - box[0] - 2],
                'вирівняти': align}
        mk['кадри'][key] = spec
        print(f'  {key}: {text!r} область {area} літери {let}')
    save_marks(m)


def cmd_check(srcs):
    m = load_marks()
    styles = atl.load_json('стилі.json')
    srcs = srcs or [k for k in m if not k.startswith('_')]
    rows = []
    for src in srcs:
        img = texture(src)
        for k, spec in m[src]['кадри'].items():
            box = tuple(spec['рамка'])
            er = img.copy()
            atl.erase(er, box, spec)
            cells = [img.crop(box), er.crop(box)]
            for text in (natl.source_text(spec), spec.get('приклад') or SAMPLE):
                t = img.copy()
                w = atl.draw(t, box, spec, styles, natl.text_for(spec, text))
                cells.append(t.crop(box))
                for x in w:
                    print(f'! {src} {k}: {x}')
            rows.append((f'{m[src].get("назва", src)} [{k}]', cells))
    if not rows:
        return
    W = max(sum(c.width + 8 for c in cells) for _t, cells in rows)
    H = sum(max(c.height for c in cells) + 18 for _t, cells in rows)
    sheet = Image.new('RGBA', (W + 8, H + 8), (46, 46, 60, 255))
    d = ImageDraw.Draw(sheet)
    y = 4
    for title, cells in rows:
        d.text((6, y), title, fill=(255, 255, 0, 255))
        x, y = 4, y + 14
        for c in cells:
            bg = Image.new('RGBA', c.size, (20, 90, 60, 255))
            bg.alpha_composite(c)
            sheet.paste(bg, (x, y))
            x += c.width + 8
        y += max(c.height for c in cells) + 4
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, 'перевірка_неп.png')
    sheet.save(p)
    print('аркуш:', p)


def cmd_audit():
    bad = 0
    for src, mk in load_marks().items():
        if src.startswith('_'):
            continue
        # рамка має лежати всередині спрайта з .ssa: гра показує лише його
        # (CONTINUE у титульному меню — 200 px, а не 224, і напис обрізався)
        fr = list(ssa_frames(src).values())
        for k, s in mk['кадри'].items():
            x0, y0, x1, y1 = s['рамка']
            w, h = x1 - x0, y1 - y0
            if fr and not any(b[0] <= x0 and b[1] <= y0 and x1 <= b[2] and y1 <= b[3] for b in fr):
                print(f'! {src} [{k}]: рамка виходить за спрайти .ssa — гра обріже напис')
                bad += 1
            a = s['область']
            if not (0 <= a[0] < a[2] <= w and 0 <= a[1] < a[3] <= h):
                print(f'! {src} [{k}]: область {a} поза рамкою {w}x{h}')
                bad += 1
    print(f'проблем: {bad}')


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return
    cmd, rest = a[0], a[1:]
    opts = [x for x in rest if x.startswith('--')]
    rest = [x for x in rest if not x.startswith('--')]
    align = 'право' if '--право' in opts else 'центр' if '--центр' in opts else 'ліво'
    title = next((x.split('=', 1)[1] for x in opts if x.startswith('--назва=')), None)
    if cmd == 'кадри':
        cmd_frames(rest[0])
    elif cmd == 'додати':
        cmd_add(rest[0], rest[1], rest[2], rest[3:], title, align)
    elif cmd == 'перевірка':
        cmd_check(rest)
    elif cmd == 'контроль':
        cmd_audit()
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
