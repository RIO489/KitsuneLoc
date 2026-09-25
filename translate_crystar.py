#!/usr/bin/env python3
"""Crystar — експорт/імпорт тексту з Unity-бандлів.

  python translate_crystar.py export <StreamingAssets> <work_dir>
  python translate_crystar.py import <StreamingAssets> <work_dir> <out_dir> [--slot en|ja]
  python translate_crystar.py status <work_dir>

Кожна таблиця/сцена існує в чотирьох мовних копіях (`_ja`, `_en`, `_ko`, `_zhtw`),
а кожна сцена ще й у двох бандлах озвучки (`ev_XXXXXX_en`, `ev_XXXXXX_ja`).
Експорт зводить усе це в ОДИН json на сцену/таблицю з англійським і японським
оригіналом поруч. Рядки мов зіставляються за порядковим номером — кількість
і структура копій однакові (перевірено на всіх 365 сценах і 10 таблицях).

Імпорт пише переклад у вибраний мовний слот (`--slot`) — у ВСІ бандли, де ця
сцена трапляється. Гра має бути налаштована на мову тексту = слот.
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import UnityPy
from crystar.unitystr import scan, rebuild
import common as locfile

LANGS = ('en', 'ja')
FMT = 'unity-mb2'


def looks_like_key(s):
    import re
    return bool(re.match(r'^[A-Za-z][A-Za-z0-9_.\-]*$', s)) and (
        '_' in s or any(c.isdigit() for c in s))


def bundles(root, orig_dir=None):
    """(шлях для читання, група). Якщо є чиста копія в orig_dir — читаємо її:
    після першого імпорту бандли в теці гри вже пропатчені."""
    def src(rel):
        if orig_dir:
            o = os.path.join(orig_dir, rel)
            if os.path.exists(o):
                return o
        return os.path.join(root, rel)
    if os.path.exists(os.path.join(root, 'parameter')):
        yield src('parameter'), 'parameter', 'parameter'
    ev = os.path.join(root, 'Event')
    if os.path.isdir(ev):
        for fn in sorted(os.listdir(ev)):
            if fn.startswith('ev_') and '_' in fn[3:]:
                rel = os.path.join('Event', fn)
                yield src(rel), 'Event/' + fn.rsplit('_', 1)[0], rel


def text_objects(env):
    """{(base, lang): object} для всіх мовних таблиць повідомлень у бандлі."""
    out = {}
    for o in env.objects:
        if o.type.name != 'MonoBehaviour':
            continue
        try:
            nm = o.read().m_Name
        except Exception:
            continue
        if 'msg' not in nm and 'Message' not in nm:
            continue
        base, _, lang = nm.rpartition('_')
        if base and lang in ('en', 'ja', 'ko', 'zhtw'):
            out[(base, lang)] = o
    return out


def speaker_names(root, orig_dir=None):
    """EV_CHARA_0026 -> Rei — з таблиці CharacterMessage (англ. і япон.)."""
    names = {'en': {}, 'ja': {}}
    p = os.path.join(orig_dir or '', 'parameter')
    if not (orig_dir and os.path.exists(p)):
        p = os.path.join(root, 'parameter')
    if not os.path.exists(p):
        return names
    objs = text_objects(UnityPy.load(p))
    for lang in LANGS:
        o = objs.get(('Game.ScriptableCharacterMessage', lang))
        if not o:
            continue
        ss = [s for _, _, s in scan(o.get_raw_data())]
        for a, b in zip(ss, ss[1:]):
            if looks_like_key(a) and not looks_like_key(b):
                names[lang].setdefault(a, b)
    return names


def build_entries(en, ja, names):
    """en/ja: списки рядків (індекс 0 — ім'я об'єкта, пропускаємо)."""
    paired = ja is not None and len(ja) == len(en)
    out, ctx = [], ''
    for i in range(1, len(en)):
        s = en[i]
        e = {'id': str(i), 'src': s}
        if paired:
            e['ja'] = ja[i]
        if looks_like_key(s):
            ctx = names['en'].get(s, s) if s.startswith(('EV_CHARA', 'PLAYER')) else s
            e['kind'] = 'key'
        else:
            e['ctx'] = ctx
        out.append(e)
    return out, paired


def cmd_export(a, progress=None):
    names = speaker_names(a.root, getattr(a, 'orig_dir', None))
    seen, n_files, n_unpaired = set(), 0, 0
    items = list(bundles(a.root, getattr(a, 'orig_dir', None)))
    for k, (path, group, _rel) in enumerate(items):
        if progress:
            progress(k + 1, len(items), group)
        try:
            objs = text_objects(UnityPy.load(path))
        except Exception as ex:
            print(f'  ! {group}: {ex}')
            continue
        for (base, lang), o in objs.items():
            if lang != 'en':
                continue
            source = f'{group}/{base}'
            if source in seen:          # другий бандл озвучки тієї ж сцени
                continue
            seen.add(source)
            en = [s for _, _, s in scan(o.get_raw_data())]
            jo = objs.get((base, 'ja'))
            ja = [s for _, _, s in scan(jo.get_raw_data())] if jo else None
            entries, paired = build_entries(en, ja, names)
            if not paired:
                n_unpaired += 1
                print(f'  ! {source}: японська копія не збігається за структурою')
            locfile.save_rich(a.work_dir, 'crystar', source, FMT, entries,
                              {'count': len(en), 'langs': ['en', 'ja'] if paired else ['en']})
            n_files += 1
    print(f'{n_files} таблиць/сцен експортовано'
          + (f', без японської пари: {n_unpaired}' if n_unpaired else ''))
    done, total = locfile.stats(a.work_dir)
    print(f'перекладено рядків: {done}/{total}')


def cmd_import(a, progress=None):
    slot = a.slot
    items = list(bundles(a.root, getattr(a, 'orig_dir', None)))
    total = 0
    for k, (path, group, rel) in enumerate(items):
        if progress:
            progress(k + 1, len(items), group)
        try:
            env = UnityPy.load(path)
        except Exception:
            continue
        changed = 0
        for (base, lang), o in text_objects(env).items():
            if lang != slot:
                continue
            doc = locfile.load_doc(a.work_dir, f'{group}/{base}')
            if not doc:
                continue
            tr = {e['id']: e['tr'] for e in doc['entries'] if e.get('tr')}
            if not tr:
                continue
            raw = o.get_raw_data()
            ss = list(scan(raw))
            if len(ss) != doc.get('count', len(ss)):
                print(f'  ! {group}/{base}_{slot}: інша структура ({len(ss)} ≠ {doc["count"]}), пропускаю')
                continue
            ch = {}
            for eid, t in tr.items():
                off, _ln, s = ss[int(eid)]
                if t != s:
                    ch[off] = t
            if ch:
                o.set_raw_data(rebuild(raw, ch))
                changed += len(ch)
        if not changed:
            continue
        dst = os.path.join(a.out_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(env.file.save(packer='original'))
        total += changed
        print(f'  {rel}: {changed} рядків')
    print(f'Разом записано в слот «{slot}»: {total} рядків')


def cmd_status(a):
    done, total = locfile.stats(a.work_dir)
    print(f'{done}/{total} ({100*done/total if total else 0:.1f}%)')


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest='cmd', required=True)
    e = sp.add_parser('export'); e.add_argument('root'); e.add_argument('work_dir')
    e.add_argument('--orig', dest='orig_dir', help='тека з чистими оригіналами (backup)')
    e.set_defaults(fn=cmd_export)
    i = sp.add_parser('import'); i.add_argument('root'); i.add_argument('work_dir')
    i.add_argument('out_dir'); i.add_argument('--slot', choices=LANGS, default='en')
    i.add_argument('--orig', dest='orig_dir', help='тека з чистими оригіналами (backup)')
    i.set_defaults(fn=cmd_import)
    s = sp.add_parser('status'); s.add_argument('work_dir'); s.set_defaults(fn=cmd_status)
    a = p.parse_args(); a.fn(a)


if __name__ == '__main__':
    main()
