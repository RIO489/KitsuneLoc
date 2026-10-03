#!/usr/bin/env python3
"""Швидка самоперевірка: експорт -> підміна одного рядка -> імпорт -> читання назад."""
import os, sys, json, glob, shutil, subprocess
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')   # numpy (через openpyxl) інакше резервує ~30 МБ на кожне ядро

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
    """Crystar: рядок таблиці, напис у префабі й шрифт — без запису на диск;
    оригінали — з backup/crystar, якщо вони там є."""
    print('\n=== Crystar ===')
    import UnityPy
    import numpy as np
    import translate_crystar as t
    from crystar.unitystr import scan, rebuild
    from unity import tmptext, fontfix
    from unity.tmpfont import TmpFont, is_tmp_font
    ns = type('a', (), {})()
    ns.root, ns.orig_dir = CRY, os.path.join(HERE, 'backup', 'crystar')
    env = UnityPy.load(t._src(ns, 'parameter'))
    o = t.text_objects(env)[('Game.ScriptableSystemMessage', 'en')]
    raw = o.get_raw_data()
    ss = list(scan(raw))
    new = [s for _o, _l, s in scan(rebuild(raw, {ss[1][0]: PROBE * 3}))]
    assert new[1] == PROBE * 3 and new[2:] == [s for _o, _l, s in ss[2:]]
    print('OK  таблиця (рядок утричі довший, решта на місці)')
    env = UnityPy.load(t._src(ns, 'uiscene'))
    items = tmptext.texts(env)
    assert len(items) > 900, len(items)
    o, _go, text = next(x for x in items if x[2] == 'Programmers')
    raw = o.get_raw_data()
    assert tmptext.with_text(raw, text) == raw
    assert tmptext.text_field(tmptext.with_text(raw, PROBE))[2] == PROBE
    print(f'OK  написи в префабах ({len(items)} TMP, підміна й повтор без змін)')
    env = UnityPy.load(t._src(ns, 'uistatic'))
    fixed = dict(fontfix.fix_bundle(env))
    assert 'Stella-FOT_ja' in fixed, fixed
    env = UnityPy.load(env.file.save(packer='original'))        # як запише імпорт
    f = next(TmpFont(x.get_raw_data()) for x in env.objects
             if x.type.name == 'MonoBehaviour' and is_tmp_font(x) and x.read().m_Name == 'Stella-FOT_ja')
    by = f.by_char()
    assert all(ord(c) in by for c in fontfix.UKR), 'немає і ї є ґ'
    assert by[ord('э')]['adv'] < 0.8 * f.face['PointSize'], 'кирилиця досі повноширинна'
    print('OK  шрифт: і ї є ґ І Ї Є Ґ додано, кирилиця з відступами латиниці')


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
        data, rep = fontfix.fix(t._font_source(sysp, fn))
        f = Ffu(data)
        assert all(f.index(v) is not None for v in chars.CODE.values()), fn
        assert not rep['missing'], rep
        assert rep['quotes'] == 2 and all(f.glyph(v) for v in chars.QUOTES.values()), fn
    print('OK  шрифти: 66 українських літер і « » у кожному')
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


def check_ch():
    """Рушій Compile Heart: спільний FFU і виправлення шрифтів обох ігор + автоматика
    для незнайомої гри (без профілю). Оригінали — з backup/msk і backup/nep."""
    print('\n=== Рушій Compile Heart (шрифти) ===')
    from compileheart.ffu import Ffu
    from compileheart import fontfix as chfix, scheme as chs
    from maryskelter.bra import Bra
    from maryskelter import fontfix as mfix
    from neptunia.pac import Pac
    from neptunia import fontfix as nfix
    import translate_nep as t
    fonts = []
    p = os.path.join(HERE, 'backup', 'msk', 'System.bra')
    if os.path.exists(p):
        fonts.append(('msk', mfix, Bra.read_some(p, ['window\\font\\msgfont.ffu'])['window\\font\\msgfont.ffu']))
    ns = type('a', (), {})()
    ns.game_dir, ns.orig_dir = NEP, os.path.join(HERE, 'backup', 'nep')
    fonts.append(('nep', nfix, Pac(t._orig(ns, t.SYSTEM)).read(t.FONTS[1])))
    for game, mod, raw in fonts:
        f = Ffu(raw)
        assert f.build() == raw, f'{game}: FFU без змін не збирається в той самий файл'
        _out, rep = mod.fix(raw)
        assert not rep['missing'] and len(rep['added']) == 8, (game, rep)
        sch, prof = chs.auto(f, used='')
        _out, rep2 = chfix.fix(raw, sch, prof)
        assert not rep2['missing'], (game, rep2['missing'])
        if game == 'msk':
            assert sch.slots == mfix.SLOT, sch.slots          # автоматика обрала ті самі слоти
        else:
            assert sch.codes == nfix.SCHEME.codes             # і ті самі однобайтові коди
        print(f'OK  {game}: FFU {f.encoding}, профіль гри — і ї є ґ додано; без профілю — '
              f'схема «{sch.kind}» та сама, що обрано вручну')


def check_mt():
    """Машинний переклад без мережі: коди -> заглушки -> назад, фальшивий сервіс, що спершу
    губить код (повторний запит його виправляє), запис у поле «Машинний» проєкту."""
    print('\n=== Машинний переклад (без мережі) ===')
    import tempfile, threading
    import project
    from mt import codes, job
    m = codes.mask('Got <ITEM> x%d! <#ff0000>Red</color> {Amount}', codes.GENERIC)
    assert m.text == 'Got ⟦0⟧ x⟦1⟧! ⟦2⟧Red⟦3⟧ ⟦4⟧', m.text
    assert m.unmask('Є ⟦0⟧ ×⟦1⟧! ⟦2⟧Червоний⟦3⟧ ⟦4⟧') == ('Є <ITEM> ×%d! <#ff0000>Червоний</color> {Amount}', None)
    assert m.unmask('Є ⟦0⟧')[1].startswith('загублено')
    x = codes.mask('A & B <i>c</i>\nnext %s', codes.GENERIC, style='xml')
    assert x.unmask('А &amp; Б <x i="0"></x>в<x i="1"/><br>далі <x i="2"/>', 'xml') == ('А & Б <i>в</i>\nдалі %s', None)
    print('OK  коди -> заглушки -> назад (⟦N⟧ і <x/>), загублений код ловиться')
    tmp = tempfile.mkdtemp()
    try:
        work, xl = os.path.join(tmp, 'work'), os.path.join(tmp, 'xl')
        import common
        common.save_rich(work, 'crystar', 'Event/ev_000000/ev_000000_msg', 'unity-mb2', [
            {'id': '0', 'src': 'Take <ITEM> now.'}, {'id': '1', 'src': 'Hello.'},
            {'id': '2', 'src': 'Hello.'}])
        pr = project.Project('crystar', work, xl)
        calls = []

        class Fake:
            name, label, style = 'fake', 'Тест', 'br'
            batch_rows, batch_chars = 10, 1000

            def translate(self, items, scene):
                calls.append(items)
                return {it['id']: ('УКР ' + it['text']).replace('⟦0⟧', '' if not it.get('retry') else '⟦0⟧')
                        for it in items}

            def spent(self):
                return ''

        rows = job.pick(pr, pr.rows)
        assert len(rows) == 2                       # «Hello.» двічі — перекладається раз
        out = []
        j = job.Job(pr, Fake(), rows, None, [], None, lambda k, d: out.append((k, d)), threading.Event())
        j.run()
        res = [kv for k, d in out if k == 'result' for kv in d]
        assert len(calls) == 2 and calls[1][0].get('problem', '').startswith('загублено'), calls
        for k, t in res:
            pr.set_mt(k, t, 'Тест')
        got = {r['e']['id']: pr.mt(r) for r in pr.rows}
        assert got == {'0': 'УКР Take <ITEM> now.', '1': 'УКР Hello.', '2': 'УКР Hello.'}, got
        assert not any(r['e'].get('tr') for r in pr.rows)
        print('OK  пакет: однакові — раз, загублений код — повторний запит, машинний у всіх однакових, '
              'переклад не чіпано')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    which = sys.argv[1] if len(sys.argv) > 1 else 'both'
    if which in ('both', 'msk'):
        check_msk()
    if which in ('both', 'crystar'):
        check_crystar()
    if which in ('nep',):
        check_nep()
    if which in ('ch',):
        check_ch()
    if which in ('mt',):
        check_mt()
    print('\nВсе гаразд.')
