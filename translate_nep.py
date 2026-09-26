#!/usr/bin/env python3
"""Hyperdimension Neptunia Re;Birth1 — експорт/імпорт тексту.

  python translate_nep.py export <тека гри> <work_dir> [--orig ТЕКА]
  python translate_nep.py import <тека гри> <work_dir> <out_dir> [--orig ТЕКА]
  python translate_nep.py seed   <тека гри> <work_dir> <тека з перекладеними .pac>
         (одноразово: забрати вже зроблений переклад зі старих перекладених архівів)
  python translate_nep.py font   <тека гри> <out_dir>   (лише шрифти, для перевірки)

Де текст (усе — в архівах DW_PACK .pac, neptunia/pac.py):
  data/SYSTEM00000.pac  database/*.gbin|*.gstr — меню, предмети, навички, Непедія…;
                        global/script/**/main.cl3 — повідомлення на карті;
                        window/font/*.ffu — шрифти.
  data/GAME00000.pac    event/script/NNNN/main.cl3 — сюжетні сцени;
                        battle/parts/**/main.cl3 — написи в бою.
  DLC/*/*.pac           database/*.gbin, event/script/*, ver101/dlcNNN.txt.
Сцени: CL3 -> STCM (байткод) -> таблиця GBNL з репліками (neptunia/stcm.py).
Українські літери пишуться однобайтово (neptunia/chars.py), шрифти
перебудовуються з оригіналів при кожному імпорті (neptunia/fontfix.py).

Змінені файли в архіві пишуться нестисненими, порядок записів зберігається —
тож індекси data/*.cpk (ім'я -> номер запису) лишаються дійсними.
"""
import argparse, glob, os, re, sys
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')   # numpy (через openpyxl) інакше резервує ~30 МБ на кожне ядро
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from neptunia.pac import Pac
from neptunia.gbnl import Gbnl
from neptunia.stcm import Stcm
from neptunia import chars, fontfix
from maryskelter.cl3 import Cl3
import common as locfile

GAME = 'nep'
SYSTEM = 'data/SYSTEM00000.pac'
MAIN = 'data/GAME00000.pac'
EXTRA = 'data/GAME00001.pac'                   # назви міст і карта світу (картинки)
ATLAS_SRC = '@атлас/нептун'                     # написи на картинках: один документ
NL_GSTR = '#n'
EVENT_NAMES = 'database/strevent.gstr'          # IDS_EVT_TITLE_NAME_SUB_<n> — імена мовців
FONTS = ('window/font/sysfont.ffu', 'window/font/msgfont.ffu', 'window/font/advfont.ffu')
# шрифт меню — похилий, з гліфів msgfont (так було в старій схемі перекладу);
# False — оригінальний прямий sysfont
SYSFONT_ITALIC = True


def _font_source(pac, fn, f=None):
    """Оригінальний шрифт, з якого будується переклад."""
    if fn == FONTS[0] and SYSFONT_ITALIC:
        return fontfix.italic_sysfont(pac.read(FONTS[0], f), pac.read(FONTS[1], f))
    return pac.read(fn, f)
CL3_ALIGN = 0x40
_CODE_ONLY = re.compile(r'^(#\w+\[[^\]]*\]\s*)+$')
_KEY = re.compile(r'^[A-Z][A-Z0-9_]*_[A-Z0-9_]+$')


# ------------------------------------------------------------------ шляхи
def archives(game_dir):
    """Відносні шляхи архівів з текстом (скісні вперед)."""
    out = [SYSTEM, MAIN, EXTRA]
    for p in sorted(glob.glob(os.path.join(game_dir, 'DLC', '*', '*.pac'))):
        out.append(os.path.relpath(p, game_dir).replace('\\', '/'))
    return [a for a in out if os.path.exists(os.path.join(game_dir, *a.split('/')))]


def _orig(a, rel):
    """Чистий оригінал архіву: з резервної копії, якщо вона є."""
    o = getattr(a, 'orig_dir', None)
    if o and os.path.exists(os.path.join(o, *rel.split('/'))):
        return os.path.join(o, *rel.split('/'))
    return os.path.join(a.game_dir, *rel.split('/'))


def _source(arc, name):
    return arc + '/' + name.replace('\\', '/')


def _kind(name):
    n = name.lower()
    if n.endswith(('.gbin', '.gstr')):
        return 'gbnl'
    if n.endswith('main.cl3') and ('script' in n or n.startswith('battle')):
        return 'stcm'
    if re.search(r'(^|\\)dlc\d+\.txt$', n):
        return 'dlctxt'
    return None


def _is_key(s):
    return bool(_KEY.match(s) or _CODE_ONLY.match(s))


# ------------------------------------------------------- розбір окремих файлів
def _gbnl_entries(g, is_gstr):
    """-> [entry] для таблиці. id = 'запис.зсув'."""
    rows = {}
    for i, fo, text, cap in g.strings():
        rows.setdefault(i, []).append((fo, text, cap))
    out = []
    for i, fields in rows.items():
        head = next((t for _fo, t, _c in fields if t and not _is_key(t)), '')
        key = fields[0][1] if is_gstr else ''
        for fo, text, cap in fields:
            if not text:
                continue
            e = {'id': f'{i}.{fo}', 'src': text.replace(NL_GSTR, '\n') if is_gstr else text}
            if (is_gstr and fo == fields[0][0]) or _is_key(text):
                e['kind'], e['ctx'] = 'key', ''
            else:
                e['ctx'] = key if is_gstr else ('' if text == head else head)
            if cap is not None:
                e['cap'] = cap + 1
            if is_gstr and '\n' in text and NL_GSTR not in text:
                # справжній перенос, не #n: так записані підписи сейва (World\n,
                # Save Data %02u\n) — екран сейва #n не розуміє, рядки злипнуться
                e['nl'] = 'raw'
            out.append(e)
    return out


def _stcm_gbnl(blob):
    """CL3 зі сценарієм -> (Cl3, Stcm, адреса блоку, Gbnl) або None."""
    c = Cl3(blob)
    if not c.files or c.files[0][1][:5] != b'STCM2':
        return None
    s = Stcm(bytes(c.files[0][1]))
    blocks = s.gbnl_blocks()
    if not blocks:
        return None
    addr, gb = blocks[0]
    return c, s, addr, Gbnl(gb)


def _stcm_entries(g, names):
    out = []
    for i, fo, text, cap in g.strings():
        if not text:
            continue
        who = names.get(g.record_u32(i, 4), '') if g.struct_size >= 8 else ''
        out.append({'id': f'{i}.{fo}', 'src': text, 'ctx': who})
    return out


def _dlctxt_entries(raw):
    """dlcNNN.txt: «NNN,Назва», далі блоки через ';'. Рядок файлу = рядок книги."""
    out = []
    for n, line in enumerate(chars.decode(raw).split('\r\n')):
        if not line.strip() or line.strip() == ';':
            continue
        m = re.match(r'^(\d{3}),(.*)$', line)
        text = m.group(2) if m and n == 0 else line
        out.append({'id': str(n), 'src': text, 'ctx': 'назва DLC' if n == 0 else 'опис DLC'})
    return out


def _event_names(a):
    """{номер мовця: ім'я} з strevent.gstr (для колонки «Хто»)."""
    try:
        g = Gbnl(Pac(_orig(a, SYSTEM)).read(EVENT_NAMES))
    except Exception:
        return {}
    by_rec = {}
    for i, fo, text, _c in g.strings():
        by_rec.setdefault(i, []).append(text)
    out = {}
    for vals in by_rec.values():
        m = re.match(r'^IDS_EVT_TITLE_NAME_SUB_(\d+)$', vals[0])
        if m and len(vals) > 1:
            out[int(m.group(1))] = vals[1]
    return out


def _looks_translated(texts):
    """Чи це вже перекладений файл (наша однобайтова або двобайтова кирилиця)."""
    n = sum(1 for t in texts if any(c in chars.CODE or 'Ѐ' <= c <= 'ӿ' for c in t))
    return n


# --------------------------------------------------------------------- експорт
def cmd_export(a, progress=None):
    names = _event_names(a)
    total_cyr = {}
    for arc in archives(a.game_dir):
        pac = Pac(_orig(a, arc))
        todo = [e for e in pac.entries if _kind(e.name)]
        n_docs = n_str = 0
        with open(pac.path, 'rb') as f:
            for k, e in enumerate(todo):
                if progress:
                    progress(k + 1, len(todo), f'{os.path.basename(arc)}: {e.name}')
                kind = _kind(e.name)
                blob = pac.read(e, f)
                try:
                    if kind == 'gbnl':
                        items = _gbnl_entries(Gbnl(blob), e.name.lower().endswith('.gstr'))
                        meta = {'newline': NL_GSTR} if e.name.lower().endswith('.gstr') else {}
                    elif kind == 'stcm':
                        r = _stcm_gbnl(blob)
                        if r is None:
                            continue
                        items, meta = _stcm_entries(r[3], names), {}
                    else:
                        items, meta = _dlctxt_entries(blob), {}
                except Exception as ex:
                    print(f'! {arc}/{e.name}: не розібрав ({ex})')
                    continue
                if not items:
                    continue
                total_cyr[arc] = total_cyr.get(arc, 0) + _looks_translated(
                    x['src'] for x in items)
                meta['encoding'] = 'nep'
                locfile.save_rich(a.work_dir, GAME, _source(arc, e.name), kind, items, meta)
                n_docs += 1
                n_str += len(items)
        if n_docs:
            print(f'  {arc}: файлів {n_docs}, рядків {n_str}')
    n_atl = _export_atlas(a)
    if n_atl:
        print(f'  написи на картинках: {n_atl}')
    bad = [arc for arc, n in total_cyr.items() if n > 10]
    if bad:
        raise RuntimeError(
            'У грі лежать уже перекладені архіви, а не оригінали:\n  ' + '\n  '.join(bad) +
            '\nПоверни оригінали: Steam -> Neptunia Re;Birth1 -> Властивості -> Встановлені '
            'файли -> «Перевірити цілісність файлів гри», і натисни «1» ще раз.')
    done, total = locfile.stats(a.work_dir)
    print(f'перекладено рядків: {done}/{total}')


# --------------------------------------------------------------------- імпорт
def _edges(src, t):
    """Пробіли й переноси з країв оригіналу — назад у переклад. Excel і книга
    їх обрізають, а в грі вони значущі: ' (Lv. %u)\\n' — пробіл відділяє рівень
    від імені, перенос у кінці — наступний рядок на екрані сейва."""
    lead = src[:len(src) - len(src.lstrip())]
    trail = src[len(src.rstrip()):]
    if lead and not t.startswith(lead):
        t = lead + t.lstrip()
    if trail and not t.endswith(trail):
        t = t.rstrip() + trail
    return t


def _tr_map(doc):
    nl = doc.get('newline')
    out = {}
    for e in doc['entries']:
        t = e.get('tr')
        if not t or t == e['src']:
            continue
        t = _edges(e['src'], t)
        out[e['id']] = t.replace('\n', nl) if nl and e.get('nl') != 'raw' else t
    return out


def _encode_all(tr, where, warns):
    new = {}
    for sid, t in tr.items():
        bad = chars.bad_chars(t)
        if bad:
            warns.append(f'{where} [{sid}]: гра не покаже {" ".join(bad)} — замінено на ?')
        i, fo = sid.split('.')
        new[(int(i), int(fo))] = chars.encode(t, 'replace')
    return new


def _build_one(a, arc, name, blob, warns):
    """Зібрати один файл з перекладом. -> (байти|None, к-сть рядків)."""
    doc = locfile.load_doc(a.work_dir, _source(arc, name))
    if not doc:
        return None, 0
    tr = _tr_map(doc)
    if not tr:
        return None, 0
    where = _source(arc, name)
    kind = doc.get('format')
    if kind == 'dlctxt':
        lines = chars.decode(blob).split('\r\n')
        for sid, t in tr.items():
            n = int(sid)
            m = re.match(r'^(\d{3}),', lines[n])
            lines[n] = (m.group(1) + ',' if m and n == 0 else '') + t.replace('\n', ' ')
        return b'\r\n'.join(chars.encode(x, 'replace') for x in lines), len(tr)
    new = _encode_all(tr, where, warns)
    if kind == 'gbnl':
        out, long_ = Gbnl(blob).build(new)
    else:
        c, s, addr, g = _stcm_gbnl(blob)
        gb, long_ = g.build(new)
        c.replace(c.files[0][0], s.replace_data(addr, gb))
        out = c.build(CL3_ALIGN)
    for i, fo, need, cap in long_:
        warns.append(f'{where} [{i}.{fo}]: {need} Б не влазить у {cap} Б — лишено англійським')
    return out, len(new) - len(long_)


def cmd_import(a, progress=None):
    os.makedirs(a.out_dir, exist_ok=True)
    warns, n_all = [], 0
    pics = _import_atlas(a)
    for arc in archives(a.game_dir):
        pac = Pac(_orig(a, arc))
        repl, n = dict(pics.pop(arc, {})), 0
        todo = [e for e in pac.entries if _kind(e.name)]
        with open(pac.path, 'rb') as f:
            for k, e in enumerate(todo):
                if progress:
                    progress(k + 1, len(todo), f'{os.path.basename(arc)}: {e.name}')
                if not locfile.load_doc(a.work_dir, _source(arc, e.name)):
                    continue
                out, cnt = _build_one(a, arc, e.name, pac.read(e, f), warns)
                if out is not None:
                    repl[e.name] = out
                    n += cnt
            if arc == SYSTEM:
                for fn in FONTS:
                    try:
                        repl[fn.replace('/', '\\')], rep = fontfix.fix(_font_source(pac, fn, f))
                    except Exception as ex:
                        print(f'! шрифт {fn}: {ex}')
                print(f'  шрифти: {len(FONTS)} (кирилиця з оригінальних гліфів, '
                      f'однобайтові слоти)')
        if not repl:
            continue
        print(f'  {arc}: файлів {len(repl)}, рядків {n} — перепаковую…')
        dst = os.path.join(a.out_dir, *arc.split('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        pac.repack(dst, repl, progress=_packer(progress, arc))
        n_all += n
    print(f'  усього рядків перекладу: {n_all}')
    for arc in pics:
        print(f'! написи для {arc}: такого архіву в грі немає')
    for w in warns[:30]:
        print('  !', w)
    if len(warns) > 30:
        print(f'  ! …і ще {len(warns) - 30}')


# --------------------------------------------------------- написи на картинках
def _atlas_blobs(a, srcs):
    """{джерело: байти .tid} з чистих оригіналів."""
    from neptunia import atlas as natl
    by_arc = {}
    for s in srcs:
        arc, inner = natl.split_src(s)
        by_arc.setdefault(arc, []).append(inner)
    out = {}
    for arc, names in by_arc.items():
        path = _orig(a, arc)
        if not os.path.exists(path):
            continue
        for name, blob in Pac(path).read_some(names).items():
            out[arc + '/' + name] = blob
    return out


def _export_atlas(a):
    """Один документ на всі написи; рядок на унікальний напис + прев'ю спрайта."""
    from neptunia import atlas as natl
    from maryskelter import atlas as atl
    from PIL import Image
    marks = natl.load_marks()
    if not marks:
        return 0
    blobs = _atlas_blobs(a, marks)
    prev = os.path.join(a.work_dir, '_атлас')
    os.makedirs(prev, exist_ok=True)
    known = _menu_translations(a)
    items, seen = [], {}
    for src, mark in marks.items():
        if src not in blobs:
            print(f'! немає текстури {src}')
            continue
        where = mark.get('назва', src)
        img = None
        for k, spec in sorted(mark['кадри'].items(), key=atl.order):
            key = atl.key_of(spec)
            if key in seen:
                if where not in seen[key]['ctx'].split(', '):
                    seen[key]['ctx'] += ', ' + where
                continue
            if img is None:
                from neptunia.tid import Tid
                img = Tid(blobs[src]).image()
            spr = img.crop(tuple(spec['рамка']))
            pic = Image.new('RGBA', spr.size, (32, 32, 40, 255))
            pic.alpha_composite(spr)
            pic.thumbnail((260, 40))
            fn = os.path.join(prev, f'{len(items):04d}.png')
            pic.convert('RGB').save(fn)
            it = {'id': key, 'src': natl.source_text(spec), 'ctx': where, 'kind': 'text', 'preview': fn,
                  'hint': 'напис на картинці: кегль підбере програма; що довше за '
                          'оригінал — то дрібніше вийде'}
            seen[key] = it
            items.append(it)
    locfile.save_rich(a.work_dir, GAME, ATLAS_SRC, 'atlas', items)
    # перший раз підказуємо переклад з меню гри (позначка «перевір» у книзі)
    doc = locfile.load_doc(a.work_dir, ATLAS_SRC)
    hit = False
    for e in doc['entries']:
        t = known.get(e['src'].upper())
        if t and not e.get('tr'):
            e['tr'] = e['auto'] = t.upper()
            hit = True
    if hit:
        import json
        json.dump(doc, open(locfile.path_for(a.work_dir, ATLAS_SRC), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
    return len(items)


def _menu_translations(a):
    """{АНГЛ. ВЕЛИКИМИ: переклад} з уже перекладених рядків меню."""
    out = {}
    for p in locfile.walk(os.path.join(a.work_dir, *SYSTEM.split('/'))):
        doc = locfile.load_json(p)
        if not doc or not doc['source'].endswith('.gstr'):
            continue
        for e in doc['entries']:
            if e.get('tr') and len(e['src']) < 30:
                out.setdefault(e['src'].upper(), e['tr'])
    return out


def _import_atlas(a):
    """Перемалювати текстури за перекладом. -> {архів: {файл: байти .tid}}."""
    from neptunia import atlas as natl
    from maryskelter import atlas as atl
    doc = locfile.load_doc(a.work_dir, ATLAS_SRC)
    if not doc:
        return {}
    tr = {e['id']: e['tr'] for e in doc['entries'] if e.get('tr') and e['tr'] != e['src']}
    marks = natl.load_marks()
    todo = [s for s, m in marks.items() if any(atl.key_of(x) in tr for x in m['кадри'].values())]
    if not todo:
        return {}
    styles = atl.load_json('стилі.json')
    out, n, warns = {}, 0, []
    for src, blob in _atlas_blobs(a, todo).items():
        new, k, w = atl.cached(src, blob, marks[src], tr, styles, natl.rebuild)
        warns += [f'{marks[src].get("назва", src)}, {x}' for x in w]
        if new:
            arc, inner = natl.split_src(src)
            out.setdefault(arc, {})[inner.replace('/', '\\')] = new
            n += k
    for w in warns[:30]:
        print('  !', w)
    print(f'  написи на картинках: {n} спрайтів у {len(todo)} текстурах')
    return out


def _packer(progress, arc):
    if not progress:
        return None
    return lambda i, n, name: progress(i + 1, n, f'Перепаковую {os.path.basename(arc)}')


# ------------------------------------------------ перенесення старого перекладу
def cmd_seed(a, progress=None):
    """Забрати переклад з уже перекладених архівів (стара схема stcm-editor +
    nr1_packer). Рядок вважається перекладеним, якщо відрізняється від оригіналу.
    Наявних перекладів у work не чіпає."""
    took = 0
    for arc in archives(a.game_dir):
        src_path = os.path.join(a.seed_dir, *arc.split('/'))
        if not os.path.exists(src_path):
            src_path = os.path.join(a.seed_dir, os.path.basename(arc))
        if not os.path.exists(src_path):
            continue
        ua = Pac(src_path)
        docs = [(p, locfile.load_json(p)) for p in locfile.walk(
            os.path.join(a.work_dir, *arc.split('/')))]
        with open(ua.path, 'rb') as f:
            for k, (p, doc) in enumerate(docs):
                if not doc:
                    continue
                inner = doc['source'][len(arc) + 1:]
                e = ua.get(inner)
                if e is None:
                    continue
                if progress:
                    progress(k + 1, len(docs), f'{os.path.basename(arc)}: {inner}')
                try:
                    blob = ua.read(e, f)
                    if doc['format'] == 'gbnl':
                        g = Gbnl(blob)
                    elif doc['format'] == 'stcm':
                        r = _stcm_gbnl(blob)
                        if r is None:
                            continue
                        g = r[3]
                    else:
                        continue
                except Exception:
                    continue
                nl = doc.get('newline')
                have = {f'{i}.{fo}': (t.replace(nl, '\n') if nl else t)
                        for i, fo, t, _c in g.strings()}
                hit = 0
                for x in doc['entries']:
                    t = have.get(x['id'])
                    if x.get('tr') or not t or t == x['src'] or x.get('kind') == 'key':
                        continue
                    x['tr'] = t
                    hit += 1
                if hit:
                    import json
                    json.dump(doc, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
                    took += hit
    print(f'перенесено перекладених рядків: {took}')


def _txt_source(rel, docs_by_pac):
    """Шлях .txt у теці перекладача -> source документа work.
    GAME00000/event/…/main.cl3.txt -> data/GAME00000.pac/event/…/main.cl3;
    DLC/<будь-що>/<архів>/<шлях>.txt -> <той архів у DLC>/<шлях>."""
    rel = rel.replace('\\', '/')
    # dlcNNN.txt — сам файл гри; решта — «<файл гри>.txt»
    parts = (rel if re.search(r'(^|/)dlc\d+\.txt$', rel, re.I) else rel[:-4]).split('/')
    for k, p in enumerate(parts):
        arc = docs_by_pac.get(p.lower())
        if arc:
            return arc + '/' + '/'.join(parts[k + 1:])
    return None


def cmd_txt(a, progress=None):
    """Забрати переклад з текстів stcm-editor/Crowdin (теки SYSTEM00000,
    GAME00000, DLC). Де рядок перекладача відрізняється від англійського —
    беремо його (це новіша версія); однаковий з оригіналом — не чіпаємо."""
    import json
    from neptunia import crowdin
    by_pac = {os.path.basename(arc)[:-4].lower(): arc for arc in archives(a.game_dir)}
    files = []
    for dp, _d, fs in os.walk(a.txt_dir):
        for fn in fs:
            if fn.lower().endswith('.txt'):
                files.append(os.path.relpath(os.path.join(dp, fn), a.txt_dir))
    st = {'файлів': 0, 'нових': 0, 'замінено': 0, 'без змін': 0, 'не знайдено': 0}
    missing, orphans, long_ = [], [], []
    for k, rel in enumerate(sorted(files)):
        if progress:
            progress(k + 1, len(files), rel)
        src = _txt_source(rel, by_pac)
        doc = locfile.load_doc(a.work_dir, src) if src else None
        if not doc:
            orphans.append(rel)
            continue
        raw = open(os.path.join(a.txt_dir, rel), 'rb').read()
        arc = src[:src.lower().index('.pac/') + 4]
        inner = src[len(arc) + 1:]
        if doc['format'] == 'dlctxt':
            lines = chars.decode(raw).translate(crowdin.GREEK).split('\r\n')
            en = chars.decode(Pac(_orig(a, arc)).read(inner)).split('\r\n')
            got = {}
            if len(lines) != len(en) or [x == ';' for x in lines] != [x == ';' for x in en]:
                orphans.append(rel + ' (будова не як в англійському файлі — пропущено)')
                continue
            for x in doc['entries']:
                n = int(x['id'])
                if n < len(lines):
                    t = lines[n]
                    m = re.match(r'^(\d{3}),(.*)$', t)
                    got[x['id']] = m.group(2) if m and n == 0 else t
        else:
            blob = Pac(_orig(a, arc)).read(inner)
            g = Gbnl(blob) if doc['format'] == 'gbnl' else _stcm_gbnl(blob)[3]
            idmap = crowdin.ids(g)
            got = {}
            for nid, text in crowdin.parse(raw).items():
                sid = idmap.get(nid)
                if sid is None:
                    missing.append(f'{rel} #{nid}')
                    continue
                got[sid] = text
        nl = doc.get('newline')
        hit = False
        for x in doc['entries']:
            t = got.get(x['id'])
            if t is None:
                continue
            if nl:
                t = t.replace(nl, '\n')
            t = t.rstrip(' \n') if not x['src'].endswith((' ', '\n')) else t
            if not t or t == x['src'] or x.get('kind') == 'key':
                st['без змін'] += 1
                continue
            if re.search('[぀-ヿ㐀-鿿！-～]', t):
                st['японською — пропущено'] = st.get('японською — пропущено', 0) + 1
                continue          # проєкт Crowdin DLC зроблено з японської: це неперекладене
            if x.get('cap') and len(chars.encode(t, 'replace')) + 1 > x['cap']:
                long_.append(f'{src} [{x["id"]}] {t!r}')
            if x.get('tr') == t:
                st['без змін'] += 1
                continue
            st['замінено' if x.get('tr') else 'нових'] += 1
            x['tr'] = t
            x.pop('auto', None)
            hit = True
        st['файлів'] += 1
        if hit:
            json.dump(doc, open(locfile.path_for(a.work_dir, src), 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=1)
    st['не знайдено'] = len(missing)
    print('  ' + ', '.join(f'{k}: {v}' for k, v in st.items()))
    for x in orphans[:20]:
        print(f'! не знаю, куди це: {x}')
    for x in missing[:10]:
        print(f'! немає такого рядка в грі: {x}')
    for x in long_[:20]:
        print(f'! задовге для поля фіксованої довжини: {x}')
    if len(long_) > 20:
        print(f'! …і ще {len(long_) - 20} задовгих')
    return st


def cmd_font(a, progress=None):
    pac = Pac(_orig(a, SYSTEM))
    os.makedirs(a.out_dir, exist_ok=True)
    for fn in FONTS:
        data, rep = fontfix.fix(_font_source(pac, fn))
        open(os.path.join(a.out_dir, os.path.basename(fn)), 'wb').write(data)
        print(fn, rep)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest='cmd', required=True)
    e = sp.add_parser('export'); e.add_argument('game_dir'); e.add_argument('work_dir')
    e.add_argument('--orig', dest='orig_dir'); e.set_defaults(fn=cmd_export)
    i = sp.add_parser('import'); i.add_argument('game_dir'); i.add_argument('work_dir')
    i.add_argument('out_dir'); i.add_argument('--orig', dest='orig_dir')
    i.set_defaults(fn=cmd_import)
    s = sp.add_parser('seed'); s.add_argument('game_dir'); s.add_argument('work_dir')
    s.add_argument('seed_dir'); s.add_argument('--orig', dest='orig_dir')
    s.set_defaults(fn=cmd_seed)
    tx = sp.add_parser('txt'); tx.add_argument('game_dir'); tx.add_argument('work_dir')
    tx.add_argument('txt_dir'); tx.add_argument('--orig', dest='orig_dir')
    tx.set_defaults(fn=cmd_txt)
    fo = sp.add_parser('font'); fo.add_argument('game_dir'); fo.add_argument('out_dir')
    fo.add_argument('--orig', dest='orig_dir'); fo.set_defaults(fn=cmd_font)
    a = p.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
