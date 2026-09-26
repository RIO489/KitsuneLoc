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
