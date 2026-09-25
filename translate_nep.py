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
NL_GSTR = '#n'
EVENT_NAMES = 'database/strevent.gstr'          # IDS_EVT_TITLE_NAME_SUB_<n> — імена мовців
FONTS = ('window/font/sysfont.ffu', 'window/font/msgfont.ffu', 'window/font/advfont.ffu')
CL3_ALIGN = 0x40
_CODE_ONLY = re.compile(r'^(#\w+\[[^\]]*\]\s*)+$')
_KEY = re.compile(r'^[A-Z][A-Z0-9_]*_[A-Z0-9_]+$')


# ------------------------------------------------------------------ шляхи
def archives(game_dir):
    """Відносні шляхи архівів з текстом (скісні вперед)."""
    out = [SYSTEM, MAIN]
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
    bad = [arc for arc, n in total_cyr.items() if n > 10]
    if bad:
        raise RuntimeError(
            'У грі лежать уже перекладені архіви, а не оригінали:\n  ' + '\n  '.join(bad) +
            '\nПоверни оригінали: Steam -> Neptunia Re;Birth1 -> Властивості -> Встановлені '
            'файли -> «Перевірити цілісність файлів гри», і натисни «1» ще раз.')
    done, total = locfile.stats(a.work_dir)
    print(f'перекладено рядків: {done}/{total}')


# --------------------------------------------------------------------- імпорт
def _tr_map(doc):
    nl = doc.get('newline')
    out = {}
    for e in doc['entries']:
        t = e.get('tr')
        if not t or t == e['src']:
            continue
        out[e['id']] = t.replace('\n', nl) if nl else t
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
    for arc in archives(a.game_dir):
        pac = Pac(_orig(a, arc))
        repl, n = {}, 0
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
                        repl[fn.replace('/', '\\')], rep = fontfix.fix(pac.read(fn, f))
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
    for w in warns[:30]:
        print('  !', w)
    if len(warns) > 30:
        print(f'  ! …і ще {len(warns) - 30}')


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


def cmd_font(a, progress=None):
    pac = Pac(_orig(a, SYSTEM))
    os.makedirs(a.out_dir, exist_ok=True)
    for fn in FONTS:
        data, rep = fontfix.fix(pac.read(fn))
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
    fo = sp.add_parser('font'); fo.add_argument('game_dir'); fo.add_argument('out_dir')
    fo.add_argument('--orig', dest='orig_dir'); fo.set_defaults(fn=cmd_font)
    a = p.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
