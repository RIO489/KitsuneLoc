"""Однобайтове кодування української в Neptunia Re;Birth1.

Гра читає текст у Shift-JIS. Кирилиця там двобайтова (0x84xx), і рушій
малює її як ієрогліф — фіксованою шириною, а поля фіксованої довжини в
таблицях (імена монстрів тощо, 32 Б) вміщають лише 15 таких літер.
Тому кожна українська літера пишеться ОДНИМ байтом замість напівширинної
катакани (0xA1–0xDF, у грі не вживається) і трьох вільних кодів 0xFD–0xFF,
а у шрифті в ці слоти намальовано кирилицю (neptunia/fontfix.py).

Таблиця збігається з тією, якою користувались раніше (preconvert_crowdin),
тож старі файли перекладу читаються без змін.
"""
UPPER = 'АБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ'
LOWER = 'абвгґдеєжзиіїйклмнопрстуфхцчшщьюя'
_CODES = list(range(0xA1, 0xE0)) + [0xFD, 0xFE, 0xFF]
assert len(UPPER + LOWER) == len(_CODES) == 66

CODE = dict(zip(UPPER + LOWER, _CODES))      # літера -> байт
LETTER = {v: k for k, v in CODE.items()}     # байт -> літера

# літери, яких у схемі немає: пишемо найближчою наявною
FALLBACK = {'Ъ': 'Ь', 'ъ': 'ь', 'Ы': 'И', 'ы': 'и', 'Э': 'Є', 'э': 'є',
            'Ё': 'Е', 'ё': 'е', 'ʼ': "'", '’': "'", '‘': "'", '«': '"', '»': '"',
            '“': '"', '”': '"', '„': '"', '—': '-', '–': '-', '…': '...',
            ' ': ' '}


RAW = 0xF700          # байт, якого немає в cp932, у тексті = chr(RAW + байт)


def encode(text, errors='strict'):
    """Рядок -> байти гри (cp932 + наші однобайтові літери)."""
    out = bytearray()
    for ch in text:
        b = CODE.get(ch)
        if b is not None:
            out.append(b)
            continue
        if RAW <= ord(ch) < RAW + 256:
            out.append(ord(ch) - RAW)
            continue
        try:
            out += ch.encode('cp932')
        except UnicodeEncodeError:
            alt = FALLBACK.get(ch)
            if alt is not None:
                out += encode(alt, errors)
            elif errors == 'strict':
                raise
            else:
                out += b'?'
    return bytes(out)


def bad_chars(text):
    """Символи, яких гра не покаже (немає ні в cp932, ні в нашій схемі)."""
    bad = set()
    for ch in text or '':
        if ch in CODE or ch in FALLBACK or RAW <= ord(ch) < RAW + 256:
            continue
        try:
            ch.encode('cp932')
        except UnicodeEncodeError:
            bad.add(ch)
    return sorted(bad)


def decode(raw):
    """Байти гри -> рядок. Однобайтові 0xA1–0xDF і 0xFD–0xFF — наша кирилиця
    (в оригінальних англійських файлах їх немає, тож читати так можна завжди)."""
    out, i, n = [], 0, len(raw)
    while i < n:
        b = raw[i]
        if b in LETTER:
            out.append(LETTER[b]); i += 1
        elif (0x81 <= b <= 0x9f or 0xe0 <= b <= 0xfc) and i + 1 < n:
            try:
                out.append(raw[i:i + 2].decode('cp932')); i += 2
            except UnicodeDecodeError:
                out.append(chr(RAW + b)); i += 1
        else:
            try:
                out.append(raw[i:i + 1].decode('cp932'))
            except UnicodeDecodeError:
                out.append(chr(RAW + b))
            i += 1
    return ''.join(out)
