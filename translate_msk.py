#!/usr/bin/env python3
"""Mary Skelter: Nightmares — експорт/імпорт тексту діалогів.

  python translate_msk.py export <тека гри> <work_dir>
  python translate_msk.py import <тека гри> <work_dir> <out_dir>
  python translate_msk.py status <work_dir>
  python translate_msk.py ls     <archive.bra>
  python translate_msk.py cpk    <file.cpk> [--out ТЕКА] [--only ФРАГМЕНТ_ШЛЯХУ]
         (японська версія: список або розпакування CPK-архіву)

Діалоги лежать у Script.bra двічі: `_EN\\EVENT\\DATA` і `EVENT\\DATA`. Обидва
дерева англійські (японського тексту в Steam-версії немає), з однаковими
рядками й різними нетекстовими полями. Експортується лише `_EN`, а імпорт
пише переклад в обидва дерева — так він видно незалежно від того, котре з них
гра читає за поточних налаштувань.

Game.bra\\StringData і DATABASE — залишки рушія з іншої гри (Monster Monpiece),
не експортуються. Інтерфейс гри — у TTM1.bra (Text\\*.bin, data\\table.enc),
TextData*.bin підтримуються, table.enc — ні (стиснений контейнер, у роботі).
"""
import argparse, json, os, re, sys, shutil
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')   # numpy (через openpyxl) інакше резервує ~30 МБ на кожне ядро
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maryskelter.bra import Bra
from maryskelter.gbnl import Gbnl
from maryskelter.textdata import TextData
from maryskelter.cpk import Cpk
from maryskelter.enc import Enc
from maryskelter import table as tbl
from maryskelter import chars
from maryskelter import fontfix
from maryskelter import atlas as atl
import common as locfile

DIALOGUE = ('Script.bra', '_EN\\EVENT\\DATA\\')
UI_ARC = 'TTM1.bra'
UI_FILES = ('Text\\TextData.bin', 'Text\\TextDataEx.bin')   # системні повідомлення, екран статусу
TABLE_FILE = 'data\\table.enc'      # спорядження, навички, завдання, локації…
TABLE_ENC = 'utf-8'                 # кодування рядків у таблицях
FONT_ARC = 'System.bra'           # шрифти: window\\font\\*.ffu
DLC_ARC = 'DLC.bra'                 # описи DLC: DLCINFO/*.enc, той самий контейнер
ATLAS_SRC = '@атлас/написи'         # написи на картинках: один документ на всі атласи
NL = '#n'                                                     # перенос рядка в інтерфейсі
TWIN_PREFIX = '_EN\\'
# у файлах Shift-JIS немає ні українських літер, ні латинських слотів із
# chars.SUBST — там доводиться обходитись схожими за виглядом символами
SJIS_FALLBACK = str.maketrans({
    'і': 'i', 'І': 'I', 'ì': 'i', 'Ì': 'I',
    'ї': 'i', 'Ї': 'I', 'ò': 'i', 'Ò': 'I',
    'є': 'е', 'Є': 'Е', 'ù': 'е', 'Ù': 'Е',
    'ґ': 'г', 'Ґ': 'Г', 'ã': 'г', 'Ã': 'Г',
})


def _orig(a, arc):
    """Чистий оригінал архіву: з резервної копії, якщо вона є (після першого
    імпорту файл у теці гри вже пропатчений і читати його як оригінал не можна)."""
    o = getattr(a, 'orig_dir', None)
    if o and os.path.exists(os.path.join(o, arc)):
        return os.path.join(o, arc)
    return os.path.join(a.game_dir, arc)


JP_SCRIPT = 'SCRIPT.cpk'     # японська версія: діалоги EVENT/DATA/*.gbin
JP_TTM = 'TTM.cpk'           # японська версія: text/textdata.bin, data/table.enc
JP_HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'maryskelter')
# уся японська, витягнута з .cpk ({source: {id: текст}}): .cpk великі й авторські,
# на інших ПК їх немає — цей файл можна передати окремо, і колонка «Японська» буде
JP_FILE = os.path.join(JP_HERE, 'японська.json')
# англійська TextData.bin = японська + рядок 389 («Toggle voice language.») + рядки
# в кінці; решта йде тим самим порядком (перевірено за кодами %s/%d: 200 з 200)
TD_EN_ONLY = 389


def _cpk(a, name):
    """Шукаємо .cpk японської версії: в явно вказаній теці (--jp), у теці
    maryskelter поруч зі скриптами або в теці гри."""
    for d in (getattr(a, 'jp_dir', None), JP_HERE, a.game_dir):
        if d and os.path.exists(os.path.join(d, name)):
            return Cpk(os.path.join(d, name))
    return None


class _Jp:
    """Японський оригінал для колонки «Японська»: з .cpk, а без них — з JP_FILE."""

    def __init__(self, a):
        self.script, self.ttm = _cpk(a, JP_SCRIPT), _cpk(a, JP_TTM)
        self.saved = {}
        if os.path.exists(JP_FILE):
            with open(JP_FILE, encoding='utf-8') as f:
                self.saved = json.load(f)
        self.got = {}

    def put(self, source, ja):
        if ja:
            self.got[source] = ja

    def apply(self, source, items):
        """Дописує `ja` у рядки; повертає, чи знайшлась японська."""
        ja = self.got.get(source) or self.saved.get(source) or {}
        n = 0
        for x in items:
            s = ja.get(x['id'])
            if s and s != x['src']:
                x['ja'] = s
                n += 1
        return n > 0

    def save(self):
        new = dict(self.saved)
        new.update(self.got)
        if new != self.saved:
            with open(JP_FILE, 'w', encoding='utf-8') as f:
                json.dump(new, f, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def _jp_textdata(en, jp):
    """{індекс англійського рядка: японський} для Text/TextData.bin."""
    pairs = {}
    for i, s in enumerate(en):
        k = i if i < TD_EN_ONLY else i - 1
        if s and i != TD_EN_ONLY and k < len(jp) and jp[k]:
            pairs[i] = jp[k]
    # перевірка зіставлення: коди підстановки мають збігатися
    codes = lambda s: sorted(re.findall(r'%[-+ #0-9.]*[sdfuxX]|#Icon\[\d+\]', s))
    both = [(codes(en[i]), codes(j)) for i, j in pairs.items() if codes(en[i]) or codes(j)]
    if not both or sum(a_ == b_ for a_, b_ in both) < 0.95 * len(both):
        print('  ! японський TextData.bin не зіставився з англійським — без японської')
        return {}
    return {str(i): j for i, j in pairs.items()}


def _jp_table(de, dj):
    """{зсув: японський} для секції .enc: поля в обох версіях за тими самими зсувами.
    Слот-продовження (після переносу чи не-ASCII символу в англійському) пропускаємо —
    японський текст поля дістається першому слоту цілком."""
    out = {}
    for off, t, _cap in tbl.slots(de, TABLE_ENC):
        if off and _continues(de, off):
            continue
        end = dj.find(b'\0', off)
        try:
            s = dj[off:end].decode('utf-8')
        except UnicodeDecodeError:
            continue
        if s and s != t and not s.isascii():
            out[str(off)] = s
    return out


def _continues(d, off):
    """Чи продовжує байт перед `off` текст того самого рядка."""
    c = d[off - 1]
    if c == 10 or 0x20 <= c < 0x7f:
        return True
    for n in (2, 3, 4):
        try:
            if len(d[max(0, off - n):off].decode('utf-8')) == 1:
                return True
        except UnicodeDecodeError:
            pass
    return False


def _sid(rec, fo):
    return f'{rec}:{fo}'


def _source(arc, name):
    return arc + '/' + name.replace('\\', '/')


def cmd_export(a, progress=None):
    arc, prefix = DIALOGUE
    b = Bra(_orig(a, arc))
    todo = [e for e in b.entries
            if e.name.startswith(prefix) and e.name.lower().endswith('.gbin')]
    jp = _Jp(a)
    n = n_ja = 0
    for k, e in enumerate(todo):
        if progress:
            progress(k + 1, len(todo), e.name)
        g = Gbnl(b.read(e))
        items = [{'id': _sid(i, fo), 'src': s} for i, fo, s in g.strings()]
        if not items:
            continue
        source = _source(arc, e.name)
        jname = 'EVENT/DATA/' + e.name.split('\\')[-1]
        if jp.script is not None and jname.lower() in jp.script.by_name:
            gj = Gbnl(jp.script.read(jname))
            ja = {_sid(i, fo): s for i, fo, s in gj.strings()}
            # пари беремо, лише якщо структура сцени однакова (так у 439 з 440)
            if gj.struct_count == g.struct_count and set(ja) == {x['id'] for x in items}:
                jp.put(source, ja)
        n_ja += jp.apply(source, items)
        locfile.save_rich(a.work_dir, 'msk', source, 'gbin', items,
                          {'encoding': g.encoding})
        n += 1
    print(f'  {arc}: {n} файлів діалогів'
          + (f', з японським оригіналом: {n_ja}' if n_ja else
             f' (ні японських .cpk, ні maryskelter\\{os.path.basename(JP_FILE)} — без японської колонки)'))
    ui = os.path.join(a.game_dir, UI_ARC)
    if os.path.exists(ui):
        t = Bra(_orig(a, UI_ARC))
        n_ui_ja = 0
        for name in UI_FILES:
            td = TextData(t.read(name))
            strs = td.strings()
            items = [{'id': str(i), 'src': s_.replace(NL, '\n')}
                     for i, s_ in enumerate(strs) if s_]
            source = _source(UI_ARC, name)
            # TextDataEx (налагодження) і в англійській версії японський — пари не треба
            if jp.ttm is not None and name == UI_FILES[0]:
                ja = _jp_textdata(strs, TextData(jp.ttm.read('text/textdata.bin')).strings())
                jp.put(source, {i: s_.replace(NL, '\n') for i, s_ in ja.items()})
            n_ui_ja += jp.apply(source, items)
            locfile.save_rich(a.work_dir, 'msk', source, 'textdata', items,
                              {'encoding': 'utf-8', 'newline': NL})
        print(f'  {UI_ARC}: інтерфейс ({len(UI_FILES)} таблиці'
              + (', з японською)' if n_ui_ja else ')'))
        n_tab = _export_tables(a, t, jp)
        if n_tab:
            print(f'  {UI_ARC}\\{TABLE_FILE}: {n_tab} рядків у таблицях')
    jp.save()
    dlc = os.path.join(a.game_dir, DLC_ARC)
    if os.path.exists(dlc):
        db = Bra(_orig(a, DLC_ARC))
        n_dlc = sum(_export_enc(a, DLC_ARC, nm, db.read(nm)) for nm in _dlc_files(db))
        if n_dlc:
            print(f'  {DLC_ARC}: {n_dlc} рядків в описах DLC')
    n_atl = _export_atlas(a)
    if n_atl:
        print(f'  написи на картинках: {n_atl}')
    done, total = locfile.stats(a.work_dir)
    print(f'перекладено рядків: {done}/{total}')


def _apply(g, tr_by_id, src_by_id=None):
    """Повертає ({(rec, fo): текст}, [попередження])."""
    new, warn = {}, []
    for i, fo, s in g.strings():
        sid = _sid(i, fo)
        t = tr_by_id.get(sid)
        if not t or t == s:
            continue
        if src_by_id is not None and src_by_id.get(sid) != s:
            continue                         # у дзеркальному дереві інший оригінал
        t = chars.apply(t)
        if not g.can_encode(t):
            t2 = t.translate(SJIS_FALLBACK)
            if not g.can_encode(t2):
                bad = sorted({c for c in t2 if not g.can_encode(c)})
                warn.append(f'{sid}: немає в {g.encoding}: {" ".join(bad)}')
                continue
            t = t2
        new[(i, fo)] = t
    return new, warn


def cmd_import(a, progress=None):
    os.makedirs(a.out_dir, exist_ok=True)
    arc, prefix = DIALOGUE
    b = Bra(_orig(a, arc))
    repl, n_str, warns = {}, 0, []
    todo = [e for e in b.entries if e.name.lower().endswith('.gbin')
            and (e.name.startswith(prefix) or e.name.startswith(prefix[len(TWIN_PREFIX):]))]
    for k, e in enumerate(todo):
        if progress:
            progress(k + 1, len(todo), e.name)
        twin = not e.name.startswith(TWIN_PREFIX)
        en_name = TWIN_PREFIX + e.name if twin else e.name
        doc = locfile.load_doc(a.work_dir, _source(arc, en_name))
        if not doc:
            continue
        tr = {x['id']: x['tr'] for x in doc['entries'] if x.get('tr')}
        if not tr:
            continue
        g = Gbnl(b.read(e))
        src = {x['id']: x['src'] for x in doc['entries']} if twin else None
        new, w = _apply(g, tr, src)
        warns += [f'{e.name} {x}' for x in w]
        if new:
            repl[e.name] = g.build(new)
            n_str += len(new)
    if repl:
        dst = os.path.join(a.out_dir, arc)
        b.repack(dst, repl, progress=_packer(progress, arc))
        print(f'  {arc}: файлів {len(repl)}, рядків {n_str} (з урахуванням обох дерев)')
    else:
        print('  нема що записувати')
    # атласи з написами лежать у кількох архівах; TTM1 і DLC і так перепаковуються —
    # їхні атласи йдуть у той самий прохід, решта архівів — окремо
    pics = _import_atlas(a)
    _import_ui(a, progress, pics.pop(UI_ARC, None))
    _import_dlc(a, progress, pics.pop(DLC_ARC, None))
    _import_font(a, progress)
    for arc_, rp in pics.items():
        print(f'  {arc_}: перепаковую архів текстур…')
        Bra(_orig(a, arc_)).repack(os.path.join(a.out_dir, arc_), rp,
                                   progress=_packer(progress, arc_))
    for w in warns[:30]:
        print('  !', w)
    if len(warns) > 30:
        print(f'  ! …і ще {len(warns) - 30}')


def _export_enc(a, arc, name, blob, jp=None, jp_blob=None):
    """Експортувати рядкові слоти одного .enc-контейнера. Повертає к-сть рядків.
    jp_blob — той самий контейнер японської версії (поля за тими самими зсувами)."""
    e = Enc(blob)
    ej = Enc(jp_blob) if jp_blob else None
    total = 0
    for sec in e.sections:
        data = e.read(sec)
        source = _source(arc, f'{name}#{sec.index}')
        items = []
        for off, text, cap in tbl.slots(data, TABLE_ENC):
            it = {'id': str(off), 'src': text, 'cap': cap}
            if tbl.is_service(text):
                it['kind'] = 'key'
            items.append(it)
        if len(items) < 3:
            continue
        if all(x.get('kind') == 'key' for x in items):
            continue          # суцільні шляхи до моделей і текстур — не для перекладу
        if jp is not None:
            if ej is not None and sec.index < len(ej.sections):
                jp.put(source, _jp_table(data, ej.read(ej.sections[sec.index])))
            jp.apply(source, [x for x in items if x.get('kind') != 'key'])
        locfile.save_rich(a.work_dir, 'msk', source, 'table', items, {'encoding': TABLE_ENC})
        total += len(items)
    return total


def _import_enc(a, arc, name, blob):
    """Зібрати змінений .enc. Повертає (к-сть рядків, попередження, байти|None)."""
    e = Enc(blob)
    new, n, warns = {}, 0, []
    for sec in e.sections:
        doc = locfile.load_doc(a.work_dir, _source(arc, f'{name}#{sec.index}'))
        if not doc:
            continue
        data = e.read(sec)
        cur = {str(o): (txt, cap) for o, txt, cap in tbl.slots(data, TABLE_ENC)}
        ch = {}
        for x in doc['entries']:
            t_ = x.get('tr')
            if not t_:
                continue
            t_ = chars.apply(t_)
            got = cur.get(x['id'])
            if got and t_ != got[0]:
                ch[int(x['id'])] = (t_, got[1])
        if not ch:
            continue
        body, bad = tbl.build(data, ch, TABLE_ENC)
        for off, need, cap in bad:
            warns.append(f'{name}#{sec.index} зсув {off}: переклад {need} Б не влазить у {cap} Б')
        new[sec.index] = body
        n += len(ch) - len(bad)
    return (n, warns, e.build(new) if new else None)


def _export_tables(a, t, jp=None):
    jp_blob = jp.ttm.read('data/table.enc') if jp is not None and jp.ttm is not None else None
    return _export_enc(a, UI_ARC, TABLE_FILE, t.read(TABLE_FILE), jp, jp_blob)


def _dlc_files(b):
    return [e.name for e in b.entries
            if e.name.startswith('DLCINFO\\') and e.name.lower().endswith('.enc')]


def _import_tables(a, t):
    n, warns, blob = _import_enc(a, UI_ARC, TABLE_FILE, t.read(TABLE_FILE))
    return (n, warns, blob) if blob else (0, warns)


def _packer(progress, arc):
    """Прогрес для перепакування архіву: найдовший крок не має мовчати."""
    if not progress:
        return None
    return lambda i, n, name: progress(i + 1, n, f'Перепаковую {arc}')


def _import_ui(a, progress=None, extra=None):
    if not os.path.exists(os.path.join(a.game_dir, UI_ARC)):
        return
    repl, n = dict(extra or {}), 0
    t = None
    for name in UI_FILES:
        doc = locfile.load_doc(a.work_dir, _source(UI_ARC, name))
        if not doc:
            continue
        tr = {int(e['id']): chars.apply(e['tr']).replace('\n', NL)
              for e in doc['entries'] if e.get('tr')}
        if not tr:
            continue
        t = t or Bra(_orig(a, UI_ARC))
        td = TextData(t.read(name))
        cur = td.strings()
        tr = {i: v for i, v in tr.items() if v != cur[i]}
        if tr:
            repl[name] = td.build(tr)
            n += len(tr)
    t = t or Bra(_orig(a, UI_ARC))
    res = _import_tables(a, t)
    if len(res) == 3:
        n_tab, warns, blob = res
        repl[TABLE_FILE] = blob
        for w in warns[:20]:
            print('  !', w)
        if warns:
            print(f'  ! рядків, що не влізли: {len(warns)} — їх лишено англійськими')
        print(f'  {UI_ARC}\\{TABLE_FILE}: {n_tab} рядків у таблицях')
    if repl:
        print(f'  {UI_ARC}: перепаковую архів інтерфейсу (~400 МБ, це найдовший крок)…')
        t.repack(os.path.join(a.out_dir, UI_ARC), repl, progress=_packer(progress, UI_ARC))
        print(f'  {UI_ARC}: рядків інтерфейсу {n}')


def _import_font(a, progress=None):
    """Перебудувати шрифти: підтягнути кирилицю (прибрати повну ширину)
    і домалювати і І ї Ї є Є ґ Ґ, яких у шрифті просто немає."""
    if not os.path.exists(os.path.join(a.game_dir, FONT_ARC)):
        return
    b = Bra(_orig(a, FONT_ARC))
    repl, rep = {}, None
    for e in b.entries:
        if not e.name.lower().endswith('.ffu'):
            continue
        try:
            data, rep = fontfix.fix(b.read(e))
        except Exception as ex:
            print(f'  ! шрифт {e.name}: {ex}')
            continue
        repl[e.name] = data
    if not repl:
        return
    b.repack(os.path.join(a.out_dir, FONT_ARC), repl, progress=_packer(progress, FONT_ARC))
    print(f'  {FONT_ARC}: шрифтів {len(repl)}, звужено кирилиці {rep["tightened"]}, '
          f'домальовано {"".join(rep["added"])}')


def _import_dlc(a, progress=None, extra=None):
    if not os.path.exists(os.path.join(a.game_dir, DLC_ARC)):
        return
    b = Bra(_orig(a, DLC_ARC))
    repl, n, warns = dict(extra or {}), 0, []
    for nm in _dlc_files(b):
        cnt, w, blob = _import_enc(a, DLC_ARC, nm, b.read(nm))
        warns += w
        if blob:
            repl[nm] = blob
            n += cnt
    if not repl:
        return
    b.repack(os.path.join(a.out_dir, DLC_ARC), repl, progress=_packer(progress, DLC_ARC))
    for w in warns[:10]:
        print('  !', w)
    print(f'  {DLC_ARC}: {n} рядків в описах DLC')


def _atlas_marks():
    """Розмітка написів на картинках: {'TTM3.bra/TEXTURE/…/x.CL3': {...}}."""
    if not os.path.exists(os.path.join(atl.DIR, 'написи.json')):
        return {}
    return {k: v for k, v in atl.load_json('написи.json').items() if not k.startswith('_')}


def _atlas_path(src):
    """'TTM3.bra/TEXTURE/title/x.CL3' -> ('TTM3.bra', 'TEXTURE\\title\\x.CL3')"""
    arc, _, name = src.partition('/')
    return arc, name.replace('/', '\\')


def _read_atlases(a, srcs):
    """{джерело: байти CL3} з чистих оригіналів; архіви не вантажимо цілком."""
    by_arc = {}
    for s in srcs:
        arc, name = _atlas_path(s)
        by_arc.setdefault(arc, []).append(name)
    out = {}
    for arc, names in by_arc.items():
        path = _orig(a, arc)
        if not os.path.exists(path):
            continue
        for name, blob in Bra.read_some(path, names).items():
            out[_source(arc, name)] = blob
    return out


def _export_atlas(a):
    """Написи на картинках -> один документ, рядок на кожен унікальний напис.
    Поруч кладемо прев'ю оригінальних спрайтів — вони підуть у книгу."""
    marks = _atlas_marks()
    if not marks:
        return 0
    from PIL import Image
    from maryskelter import dds
    from maryskelter.cl3 import Cl3
    blobs = _read_atlases(a, marks)
    prev = os.path.join(a.work_dir, '_атлас')
    os.makedirs(prev, exist_ok=True)
    # прев'ю залежать лише від розмітки й оригінальних атласів: якщо ні те, ні
    # інше не змінилось, текстури не розкодовуємо й картинки не переписуємо
    import hashlib
    h = hashlib.md5(json.dumps(marks, sort_keys=True, ensure_ascii=False).encode('utf-8'))
    for src in sorted(blobs):
        h.update(hashlib.md5(blobs[src]).digest())
    sig_path = os.path.join(prev, '_відбиток.txt')
    try:
        same = open(sig_path, encoding='utf-8').read() == h.hexdigest()
    except OSError:
        same = False
    items, seen = [], {}
    for src, mark in marks.items():
        if src not in blobs:
            print(f'  ! немає атласу {src}')
            continue
        where = mark.get('назва', src)
        cl3 = Cl3(blobs[src])
        img, boxes = None, atl.frames(cl3, mark.get('текстура'))
        for i, spec in sorted(mark['кадри'].items(), key=atl.order):
            k = atl.key_of(spec)
            if k in seen:
                if where not in seen[k]['ctx'].split(', '):
                    seen[k]['ctx'] += ', ' + where
                continue
            box = atl.box_of(i, spec, boxes)
            if box is None:
                print(f'  ! {where}: кадру {i} немає в нарізці')
                continue
            fn = os.path.join(prev, f'{len(items):04d}.png')
            if not (same and os.path.exists(fn)):
                if img is None:
                    img = dds.decode(bytes(atl.pair(cl3, mark.get('текстура'))[1][1]))
                spr = img.crop(box)
                pic = Image.new('RGBA', spr.size, (32, 32, 40, 255))
                pic.alpha_composite(spr)
                pic.thumbnail((260, 40))
                pic.convert('RGB').save(fn)
            it = {'id': k, 'src': spec['текст'], 'ctx': where, 'kind': 'text', 'preview': fn,
                  'hint': 'напис на картинці: кегль підбере програма; що довше за '
                          'оригінал — то дрібніше вийде'}
            seen[k] = it
            items.append(it)
    locfile.save_rich(a.work_dir, 'msk', ATLAS_SRC, 'atlas', items)
    with open(sig_path, 'w', encoding='utf-8') as f:
        f.write(h.hexdigest())
    return len(items)


def _import_atlas(a):
    """Перемалювати атласи за перекладом. Повертає {архів: {файл: байти CL3}}."""
    doc = locfile.load_doc(a.work_dir, ATLAS_SRC)
    if not doc:
        return {}
    # порожньо або те саме, що в оригіналі, — спрайт лишається як був
    tr = {e['id']: e['tr'] for e in doc['entries'] if e.get('tr') and e['tr'] != e['src']}
    marks = _atlas_marks()
    todo = [s for s, m in marks.items() if any(atl.key_of(x) in tr for x in m['кадри'].values())]
    if not todo:
        return {}
    styles = atl.load_json('стилі.json')
    out, n, warns = {}, 0, []
    for src, blob in _read_atlases(a, todo).items():
        new, k, w = atl.cached(src, blob, marks[src], tr, styles, atl.rebuild)
        warns += [f'{marks[src].get("назва", src)}, {x}' for x in w]
        if new:
            arc, name = _atlas_path(src)
            out.setdefault(arc, {})[name] = new
            n += k
    for w in warns[:30]:
        print('  !', w)
    print(f'  написи на картинках: {n} спрайтів у {len(todo)} атласах')
    return out


def cmd_status(a):
    done, total = locfile.stats(a.work_dir)
    print(f'{done}/{total} ({100 * done / total if total else 0:.1f}%)')


def cmd_ls(a):
    b = Bra(a.archive)
    for e in b.entries:
        print(f'{e.raw_size:10} {e.name}')
    print(f'-- {len(b.entries)} файлів')


def cmd_cpk(a):
    c = Cpk(a.archive)
    todo = [x for x in c.files if not a.only or a.only.lower() in x['name'].lower()]
    if not a.out:
        for x in todo:
            z = '  (стиснено)' if x['size'] < x['extract'] else ''
            print(f"{x['extract']:10} {x['name']}{z}")
        print(f'-- {len(todo)} із {len(c.files)} файлів')
        return
    for k, x in enumerate(todo, 1):
        dst = os.path.join(a.out, *x['name'].split('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(c.read(x))
        print(f'  [{k}/{len(todo)}] {x["name"]}')


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest='cmd', required=True)
    e = sp.add_parser('export'); e.add_argument('game_dir'); e.add_argument('work_dir')
    e.add_argument('--orig', dest='orig_dir', help='тека з чистими оригіналами (backup)')
    e.add_argument('--jp', dest='jp_dir', help='тека з SCRIPT.cpk японської версії')
    e.set_defaults(fn=cmd_export)
    i = sp.add_parser('import'); i.add_argument('game_dir'); i.add_argument('work_dir')
    i.add_argument('out_dir')
    i.add_argument('--orig', dest='orig_dir', help='тека з чистими оригіналами (backup)')
    i.set_defaults(fn=cmd_import)
    s = sp.add_parser('status'); s.add_argument('work_dir'); s.set_defaults(fn=cmd_status)
    l = sp.add_parser('ls'); l.add_argument('archive'); l.set_defaults(fn=cmd_ls)
    c = sp.add_parser('cpk'); c.add_argument('archive'); c.add_argument('--out')
    c.add_argument('--only'); c.set_defaults(fn=cmd_cpk)
    a = p.parse_args(); a.fn(a)


if __name__ == '__main__':
    main()
