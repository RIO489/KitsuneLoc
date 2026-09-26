# -*- coding: utf-8 -*-
"""Глосарій термінів перекладу (Neptune → Нептун, HP → ОЗ).

Лежить у теці книг гри — `Переклад\\<гра>\\терміни.json`: це рішення перекладача,
як і самі книги (git і оновлення програми його не чіпають).
  [{"en": "Neptune", "ua": "Нептун", "примітка": "..."}, ...]
У "ua" можна кілька варіантів через «/» (Нептун/Нептуна) — перевірка
приймає будь-який.

Де вживається:
  * вікно «Терміни…» (terms_window.py) — пошук, правка, як перекладено в книгах;
  * колонка «Терміни» в книгах (sheets.write_book) — які терміни є в рядку;
  * sheets.validate — термін в оригіналі є, а його перекладу в рядку немає.
"""
import json, os, re

FILE = 'терміни.json'


def load(xlsx_dir):
    try:
        with open(os.path.join(xlsx_dir, FILE), encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return []
    return [t for t in d if isinstance(t, dict) and t.get('en')] if isinstance(d, list) else []


def save(xlsx_dir, terms):
    os.makedirs(xlsx_dir, exist_ok=True)
    terms = sorted(terms, key=lambda t: t['en'].lower())
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
    for t in found(src, terms):
        variants = [v for v in re.split(r'[/,;]', t.get('ua') or '') if v.strip()]
        if not variants:
            continue
        if not any(all(_stem(w) in low for w in v.split()) for v in variants):
            out.append((t['en'], t['ua']))
    return out
