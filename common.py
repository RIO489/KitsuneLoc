"""Shared translation-file format: one JSON per source file, mirrored tree.

{
  "game": "msk", "source": "Script.bra/_EN/EVENT/DATA/000001.gbin",
  "format": "gbin",
  "entries": [{"id": "0:368", "src": "original", "tr": ""}]
}
`tr` empty  -> keep the original string.
"""
import json, os

SUFFIX = '.json'
# службові файли в теці work — це не документи перекладу
SERVICE = {'прогрес.json'}


def path_for(root, source):
    return os.path.join(root, *source.split('/')) + SUFFIX


def save(root, game, source, fmt, entries, keep_existing=True):
    """entries: [(id, src)]. Existing translations for matching ids are kept."""
    dst = path_for(root, source)
    old = {}
    if keep_existing and os.path.exists(dst):
        for e in json.load(open(dst, encoding='utf-8'))['entries']:
            if e.get('tr'):
                old[e['id']] = e['tr']
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    doc = {'game': game, 'source': source, 'format': fmt,
           'entries': [{'id': i, 'src': s, 'tr': old.get(i, '')} for i, s in entries]}
    with open(dst, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return dst


def load(root, source):
    dst = path_for(root, source)
    if not os.path.exists(dst):
        return None
    doc = json.load(open(dst, encoding='utf-8'))
    return {e['id']: e['tr'] for e in doc['entries'] if e.get('tr')}


def walk(root):
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.endswith(SUFFIX) and fn not in SERVICE:
                yield os.path.join(dirpath, fn)


def load_json(path):
    """Прочитати документ перекладу; None, якщо це щось інше."""
    try:
        doc = json.load(open(path, encoding='utf-8'))
    except Exception:
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get('entries'), list) else None


def stats(root):
    total = done = 0
    for p in walk(root):
        doc = load_json(p)
        if not doc:
            continue
        for e in doc['entries']:
            total += 1
            done += bool(e.get('tr'))
    return done, total


def save_rich(root, game, source, fmt, entries, meta=None):
    """entries: [dict(id=..., src=..., інші поля)]. Наявні `tr` за тим самим id зберігаються."""
    dst = path_for(root, source)
    keep = ('tr', 'note', 'auto')          # те, що вносить людина, — переживає повторний експорт
    old = {}
    if os.path.exists(dst):
        for e in json.load(open(dst, encoding='utf-8'))['entries']:
            kept = {k: e[k] for k in keep if e.get(k)}
            if kept:
                old[e['id']] = kept
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    doc = {'game': game, 'source': source, 'format': fmt}
    doc.update(meta or {})
    doc['entries'] = []
    for e in entries:
        ne = dict(e)
        ne.update(old.get(e['id'], {}))
        ne.setdefault('tr', '')
        doc['entries'].append(ne)
    with open(dst, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return dst


def fingerprint(path, chunk=1 << 20):
    """«Відбиток» великого файлу гри: розмір + md5 першого й останнього мегабайта.
    Перепакований (перекладений) архів майже завжди має інший розмір, а хвіст/
    початок з індексом — інші байти; читати весь файл (сотні МБ) не треба."""
    import hashlib
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        head = hashlib.md5(f.read(chunk)).hexdigest()
        f.seek(max(0, size - chunk))
        tail = hashlib.md5(f.read(chunk)).hexdigest()
    return [size, head, tail]


PATCH_EPOCH = 1609459200          # 2021-01-01: у справжніх .bra MSK усі файли 2017–2018 р.


def looks_patched(path):
    """Чи сліди перепакування в архіві: True / False / None (не знаю формату).
    Не залежить від збірки гри в Steam (на відміну від відбитка):
      .bra (MSK) — є файл із датою після 2021 р. (Bra.repack ставить «зараз»);
      .pac (Neptunia) — є нестиснений файл (оригінали стиснені повністю,
      змінені пишемо нестисненими — і ми, і стара схема nr1_packer)."""
    import struct
    low = path.lower()
    with open(path, 'rb') as f:
        if low.endswith('.bra'):
            magic, _v, off, cnt = struct.unpack('<4sIII', f.read(16))
            if magic != b'PDA\0':
                return None
            f.seek(off)
            idx, p = f.read(), 0
            for _ in range(cnt):
                mtime, nlen = struct.unpack_from('<I', idx, p)[0], struct.unpack_from('<H', idx, p + 16)[0]
                if mtime >= PATCH_EPOCH:
                    return True
                p += 24 + nlen
            return False
        if low.endswith('.pac'):
            magic, _f, cnt, _s = struct.unpack('<8sIII', f.read(20))
            if magic != b'DW_PACK\0':
                return None
            raw = f.read(288 * cnt)
            for k in range(cnt):
                size, _u, packed = struct.unpack_from('<III', raw, 288 * k + 272)
                if size and packed != 1:
                    return True
            return False
    return None


def is_original(path, want=None):
    """Відбиток збігся — точно оригінал; ні — вирішують сліди перепакування
    (інша збірка гри в Steam має інші відбитки, але слідів не має)."""
    if want and fingerprint(path) == want:
        return True
    return looks_patched(path) is False


def load_doc(root, source):
    dst = path_for(root, source)
    return json.load(open(dst, encoding='utf-8')) if os.path.exists(dst) else None
