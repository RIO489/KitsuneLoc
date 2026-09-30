# -*- coding: utf-8 -*-
"""Глосарій термінів перекладу (Neptune → Нептун, HP → ОЗ).

Лежить у теці книг гри — `Переклад\\<гра>\\терміни.json`: це рішення перекладача,
як і самі книги (git і оновлення програми його не чіпають).
  [{"en": "Neptune", "ua": "Нептун", "примітка": "..."}, ...]
У "ua" можна кілька варіантів через «/» (Нептун/Нептуна) — перевірка
приймає будь-який.

Бази за жанром — `терміни\\<жанр>.json` поруч з програмою (у git, тож приходять з
оновленням): загальні ігрові терміни (STR, DEF, AGI, Poison…) з готовим перекладом.
Жанр гри вибирають у вікні «Терміни…» (`Переклад\\<гра>\\жанр.json`); терміни бази
доходять до підказок разом зі своїми, мають позначку `"жанр"`, у терміни.json не
пишуться, а свій термін з тим самим "en" їх перекриває. Перевірка «термін … у
перекладі не знайдено» терміни бази не чіпає: вони загальні, і в прозі їх часто
передають інакше.

Де вживається:
  * вікно «Терміни…» (terms_window.py) — пошук, правка, як перекладено в книгах;
  * колонка «Терміни» в книгах (sheets.write_book) — які терміни є в рядку;
  * редактор перекладу — терміни рядка, вставка кліком;
  * sheets.validate — термін в оригіналі є, а його перекладу в рядку немає.
"""
import json, os, re

FILE = 'терміни.json'
GENRE_FILE = 'жанр.json'
GENRES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'терміни')


def _read_list(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    return [t for t in d if isinstance(t, dict) and t.get('en')] if isinstance(d, list) else []


def genres():
    """Назви баз термінів за жанром (файли терміни\\*.json)."""
    try:
        return sorted(f[:-5] for f in os.listdir(GENRES_DIR) if f.lower().endswith('.json'))
    except OSError:
        return []


def genre(xlsx_dir):
    """Жанр гри, вибраний у «Терміни…», або ''."""
    try:
        with open(os.path.join(xlsx_dir, GENRE_FILE), encoding='utf-8') as f:
            return json.load(f).get('жанр', '') or ''
    except (OSError, ValueError, AttributeError):
        return ''


def set_genre(xlsx_dir, name):
    os.makedirs(xlsx_dir, exist_ok=True)
    with open(os.path.join(xlsx_dir, GENRE_FILE), 'w', encoding='utf-8') as f:
        json.dump({'жанр': name or ''}, f, ensure_ascii=False)


def load(xlsx_dir, with_genre=True):
    """Свої терміни гри + (with_genre) терміни бази її жанру, яких немає серед своїх."""
    own = _read_list(os.path.join(xlsx_dir, FILE))
    name = genre(xlsx_dir) if with_genre else ''
    if not name:
        return own
    have = {t['en'].lower() for t in own}
    base = [dict(t, жанр=name) for t in _read_list(os.path.join(GENRES_DIR, name + '.json'))
            if t['en'].lower() not in have]
    return own + base


def save(xlsx_dir, terms):
    """Зберегти свої терміни (терміни бази жанру сюди не пишуться)."""
    os.makedirs(xlsx_dir, exist_ok=True)
    terms = sorted((t for t in terms if not t.get('жанр')), key=lambda t: t['en'].lower())
    p = os.path.join(xlsx_dir, FILE)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(terms, f, ensure_ascii=False, indent=1)
    os.replace(p + '.tmp', p)


# ------------------------------------------------ глосарії інших програм (Crowdin)
# Crowdin вивантажує глосарій у TBX (v2 «martif» і v3 «tbx»), CSV і XLSX з колонками
# «Term [en]» / «Term [uk]». Кілька варіантів перекладу (синоніми) зберігає лише TBX v3 —
# CSV/XLSX лишають один, тому можна дати кілька файлів: варіанти об'єднуються.

SRC_LANG, DST_LANG = 'en', 'uk'
_LATIN = re.compile('[A-Za-z]')


def _lang(e):
    for k, v in e.attrib.items():
        if k.endswith('}lang') or k == 'lang':
            return v.split('-')[0].lower()
    return ''


def _read_tbx(path):
    import xml.etree.ElementTree as ET
    out = []
    for entry in ET.parse(path).getroot().iter():
        if entry.tag.split('}')[-1] not in ('termEntry', 'conceptEntry'):
            continue
        terms, notes = {}, []
        for x in entry.iter():
            tag = x.tag.split('}')[-1]
            if tag in ('langSet', 'langSec'):
                lang = _lang(x)
                for sec in x:
                    if sec.tag.split('}')[-1] not in ('tig', 'termSec', 'ntig'):
                        continue
                    t = next((y.text for y in sec.iter() if y.tag.split('}')[-1] == 'term'), None)
                    pref = any('preferred' in (y.text or '') for y in sec.iter()
                               if y.tag.split('}')[-1] == 'termNote')
                    if t and t.strip():
                        lst = terms.setdefault(lang, [])
                        lst.insert(0, t.strip()) if pref else lst.append(t.strip())
            elif tag == 'descrip' and x.get('type') in ('context', 'definition') and (x.text or '').strip():
                notes.append(x.text.strip())
        out.append((terms.get(SRC_LANG, []), terms.get(DST_LANG, []), '; '.join(dict.fromkeys(notes))))
    return out


def _read_table(rows):
    """Рядки таблиці (перший — заголовки) з колонками Crowdin «Term [en]», «Term [uk]»,
    «Description [..]» / «Note [..]»."""
    rows = iter(rows)
    head = [str(h or '').strip() for h in next(rows, [])]
    col = {h.lower(): i for i, h in enumerate(head)}
    src, dst = col.get(f'term [{SRC_LANG}]'), col.get(f'term [{DST_LANG}]')
    if src is None or dst is None:
        raise ValueError('немає колонок «Term [en]» і «Term [uk]» — це не глосарій Crowdin')
    note_cols = [i for h, i in col.items() if h.split(' [')[0] in ('description', 'note', 'concept definition',
                                                                     'concept note')]
    out = []
    for r in rows:
        cell = lambda i: str(r[i]).strip() if i < len(r) and r[i] is not None else ''
        notes = [cell(i) for i in note_cols if cell(i)]
        out.append(([cell(src)] if cell(src) else [], [cell(dst)] if cell(dst) else [],
                    '; '.join(dict.fromkeys(notes))))
    return out


def read_external(path):
    """Глосарій Crowdin (.tbx / .csv / .xlsx) -> [{"en", "ua", "примітка"}]. Кілька варіантів
    перекладу — через «/», як у своєму глосарії. Записи без перекладу чи без латиниці в
    англійському (кирилиця, вписана не в ту колонку) пропускаються."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.tbx':
        raw = _read_tbx(path)
    elif ext == '.csv':
        import csv
        with open(path, encoding='utf-8-sig', newline='') as f:
            raw = _read_table(list(csv.reader(f)))
    elif ext in ('.xlsx', '.xlsm'):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True)
        try:
            raw = _read_table(list(wb.worksheets[0].iter_rows(values_only=True)))
        finally:
            wb.close()
    else:
        raise ValueError(f'невідомий формат {ext or "без розширення"} (потрібен .tbx, .csv або .xlsx)')
    got = {}
    for srcs, dsts, note in raw:
        for en in srcs:
            if not dsts or not _LATIN.search(en):
                continue
            t = got.setdefault(en.lower(), {'en': en, 'ua': [], 'примітка': []})
            t['ua'] += [v for v in dsts if v not in t['ua']]
            if note and note not in t['примітка']:
                t['примітка'].append(note)
    return [{'en': t['en'], 'ua': '/'.join(t['ua']), 'примітка': '; '.join(t['примітка'])}
            for t in got.values()]


def read_many(paths):
    """Кілька файлів глосарію (усі формати, що дав Crowdin) -> один список. Для кожного
    терміна першим іде файл, де в нього найбільше варіантів: лише TBX v3 має всі варіанти
    й порядок (бажаний переклад — перший), решта лишає один, і не завжди бажаний."""
    per = [t for p in paths for t in read_external(p)]
    per.sort(key=lambda t: -len(t['ua'].split('/')))          # стабільно: порядок файлів лишається
    got = {}
    for t in per:
        g = got.setdefault(t['en'].lower(), {'en': t['en'], 'ua': [], 'примітка': []})
        g['ua'] += [v for v in t['ua'].split('/') if v and v not in g['ua']]
        if t['примітка'] and t['примітка'] not in g['примітка']:
            g['примітка'].append(t['примітка'])
    return [{'en': g['en'], 'ua': '/'.join(g['ua']), 'примітка': '; '.join(g['примітка'])}
            for g in got.values()]


def merge(own, incoming, replace=False):
    """Додати терміни до своїх. -> (новий список, додано, однакових, [(en, свій, новий)] різних).
    Різні лишаються своїми, якщо не replace (тоді беремо новий переклад, а свою примітку —
    якщо в новому її немає). Терміни бази жанру (`жанр`) не свої: новий їх перекриває."""
    terms = [dict(t) for t in own if not t.get('жанр')]
    idx = {t['en'].lower(): t for t in terms}
    added, same, diff = 0, 0, []
    for t in incoming:
        mine = idx.get(t['en'].lower())
        if mine is None:
            t = {k: v for k, v in t.items() if v}
            terms.append(t)
            idx[t['en'].lower()] = t
            added += 1
        elif not mine.get('ua'):
            mine['ua'] = t['ua']
            added += 1
        elif mine['ua'] == t['ua']:
            same += 1
        else:
            diff.append((mine['en'], mine.get('ua') or '', t['ua']))
            if replace:
                mine['ua'] = t['ua']
                if t.get('примітка'):
                    mine['примітка'] = t['примітка']
    return terms, added, same, diff


_rx = {}


def pattern(en):
    """Слово чи фраза цілком (з англійською множиною). Термін малими літерами
    шукаємо без огляду на регістр; з великими — як записано або ВЕЛИКИМИ (меню),
    інакше ім'я «IF» ловило б кожне «if»."""
    rx = _rx.get(en)
    if rx is None:
        if en == en.lower():
            body, flags = re.escape(en), re.IGNORECASE
        else:
            body, flags = f'(?:{re.escape(en)}|{re.escape(en.upper())})', 0
        rx = _rx[en] = re.compile(r'(?<![A-Za-z])' + body + r"(?:s|es|'s)?(?![A-Za-z])", flags)
    return rx


def found(text, terms):
    """Терміни, що трапляються в тексті (довші — першими: «Neptune» не з'їсть
    «Purple Heart»)."""
    out = []
    for t in sorted(terms, key=lambda t: -len(t['en'])):
        if pattern(t['en']).search(text or ''):
            out.append(t)
    return out


def hint(text, terms):
    """«Neptune → Нептун; HP → ОЗ» для колонки «Терміни»."""
    return '; '.join(f"{t['en']} → {t['ua']}" + (f" ({t['примітка']})" if t.get('примітка') else '')
                     for t in found(text, terms) if t.get('ua'))


def _stem(w):
    """Грубо відкидаємо закінчення: «Нептуна», «Нептуном» — той самий «Нептун»."""
    w = w.strip().lower()
    return w[:-2] if len(w) >= 6 else w[:-1] if len(w) >= 4 else w


def missing(src, tr, terms):
    """[(термін, як перекладати)] — є в оригіналі, а в перекладі немає."""
    low = (tr or '').lower()
    out = []
    for t in found(src, [t for t in terms if not t.get('жанр')]):
        variants = [v for v in re.split(r'[/,;]', t.get('ua') or '') if v.strip()]
        if not variants:
            continue
        if not any(all(_stem(w) in low for w in v.split()) for v in variants):
            out.append((t['en'], t['ua']))
    return out
