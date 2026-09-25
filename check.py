#!/usr/bin/env python3
"""Швидка самоперевірка: експорт -> підміна одного рядка -> імпорт -> читання назад."""
import os, sys, json, glob, shutil, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STEAM = r'C:\Users\Alien m15\Games\Steam\steamapps\common'
# шляхи можна перевизначити змінними оточення MSK_DIR / CRY_DIR
MSK = os.environ.get('MSK_DIR') or os.path.join(STEAM, 'Mary Skelter Nightmares')
CRY = os.environ.get('CRY_DIR') or os.path.join(
    STEAM, 'Crystar', 'CRYSTAR_Data', 'StreamingAssets')
NEP = os.environ.get('NEP_DIR') or os.path.join(STEAM, 'Neptunia Rebirth1')
PROBE = 'ПРОБА кирилиці'


def run(*args):
    print('$', ' '.join(args))
    subprocess.run([sys.executable] + list(args), cwd=HERE, check=True)


def poke(pattern):
    hits = sorted(glob.glob(pattern, recursive=True))
    assert hits, f'немає файлів за {pattern}'
    p = hits[0]
    doc = json.load(open(p, encoding='utf-8'))
    doc['entries'][1]['tr'] = PROBE
    json.dump(doc, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return p, doc['source'], doc['entries'][1]['id']


def check_msk():
    print('\n=== Mary Skelter ===')
    work, out = os.path.join(HERE, 'work', 'msk'), os.path.join(HERE, 'out', 'msk')
    shutil.rmtree(out, ignore_errors=True)
    run('translate_msk.py', 'export', MSK, work, '--archives', 'Script.bra', '--only', '_EN')
    p, source, sid = poke(os.path.join(work, 'Script.bra', '_EN', '**', '*.json'))
    run('translate_msk.py', 'import', MSK, work, out, '--archives', 'Script.bra')
    from maryskelter import Bra, Gbnl
    arc, inner = source.split('/', 1)
    orig, new = Bra(os.path.join(MSK, arc)), Bra(os.path.join(out, arc))
    rec, fo = (int(x) for x in sid.split(':'))
    got = dict(((i, f), s) for i, f, s in Gbnl(new.read(inner.replace('/', '\\'))).strings())
    assert got[(rec, fo)] == PROBE, got[(rec, fo)]
    same = sum(1 for e in orig.entries if new.read(e.name) == orig.read(e.name))
    print(f'OK  рядок підмінено, решта файлів без змін: {same}/{len(orig.entries)}')


def check_crystar():
    print('\n=== Crystar ===')
    work, out = os.path.join(HERE, 'work', 'crystar'), os.path.join(HERE, 'out', 'crystar')
    shutil.rmtree(out, ignore_errors=True)
    run('translate_crystar.py', 'export', CRY, work, '--lang', 'en')
    p, source, sid = poke(os.path.join(work, 'parameter', '*SystemMessage_en.json'))
    run('translate_crystar.py', 'import', CRY, work, out, '--lang', 'en')
    import UnityPy
    from crystar import scan
    rel = source.rsplit('/', 1)
    env = UnityPy.load(os.path.join(out, *rel[0].split('/')))
    for o in env.objects:
        if o.type.name == 'MonoBehaviour' and o.read().m_Name == rel[1]:
            got = {f'{off:x}': s for off, ln, s in scan(o.get_raw_data())}
            assert got[sid] == PROBE, got[sid]
            print('OK  рядок підмінено, бандл перезібрано')
            return
    raise AssertionError('об\'єкт не знайдено у перезібраному бандлі')


def check_nep():
    """Neptunia: підміна рядка в таблиці й у сцені (з ростом тексту) і шрифти —
    без запису в теку гри; оригінали — з backup/nep, якщо вони там є."""
    print('\n=== Neptunia Re;Birth1 ===')
    import translate_nep as t
    from neptunia import Pac, Gbnl, Ffu, chars, fontfix
    ns = type('a', (), {})()
    ns.game_dir, ns.orig_dir = NEP, os.path.join(HERE, 'backup', 'nep')
    sysp = Pac(t._orig(ns, t.SYSTEM))
    g = Gbnl(sysp.read('database/strmenu.gstr'))
    i, fo, _s, _c = g.strings()[1]
    out, _ = g.build({(i, fo): chars.encode(PROBE)})
    assert dict(((a, b), x) for a, b, x, _ in Gbnl(out).strings())[(i, fo)] == PROBE
    print('OK  таблиця GSTR')
    blob = Pac(t._orig(ns, t.MAIN)).read('event/script/0101/main.cl3')
    c, st, addr, g = t._stcm_gbnl(blob)
    new = {(a, b): chars.encode(PROBE * 3) for a, b, _x, _c in g.strings()}
    gb, _ = g.build(new)
    c.replace(c.files[0][0], st.replace_data(addr, gb))
    c2, st2, _a, g2 = t._stcm_gbnl(c.build(t.CL3_ALIGN))
    assert st2.shape() == st.shape(), "зсуви в сценарії з'їхали"
    assert all(x == PROBE * 3 for _a, _b, x, _c in g2.strings())
    print('OK  сцена STCM (текст утричі довший, усі вказівники на місці)')
    for fn in t.FONTS:
        data, rep = fontfix.fix(sysp.read(fn))
        f = Ffu(data)
        assert all(f.index(v) is not None for v in chars.CODE.values()), fn
        assert not rep['missing'], rep
    print('OK  шрифти: 66 українських літер у кожному')
    from neptunia.tid import Tid
    from neptunia import atlas as natl
    blob = Pac(t._orig(ns, t.MAIN)).read('menu/item/title.tid')
    tid = Tid(blob)
    assert tid.patch(tid.image(), [(0, 0, 64, 64)]) == blob, 'текстура змінилась без правок'
    marks = natl.load_marks()
    src = t.MAIN + '/menu/item/title.tid'
    if src in marks:
        from maryskelter import atlas as atl
        key = atl.key_of(next(iter(marks[src]['кадри'].values())))
        new, n, _w = natl.rebuild(blob, marks[src], {key: PROBE}, atl.load_json('стилі.json'))
        assert n == 1 and Tid(new).image().size == tid.image().size
    print('OK  написи на картинках (.tid)')


if __name__ == '__main__':
    which = sys.argv[1] if len(sys.argv) > 1 else 'both'
    if which in ('both', 'msk'):
        check_msk()
    if which in ('both', 'crystar'):
        check_crystar()
    if which in ('nep',):
        check_nep()
    print('\nВсе гаразд.')
