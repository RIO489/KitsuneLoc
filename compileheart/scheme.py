# -*- coding: utf-8 -*-
"""Схема символів: куди в шрифті FFU кладемо кирилицю і як тоді писати текст гри.

Рушій Compile Heart має два відомі варіанти (визначаються за шрифтом, не за грою):
  UTF-8 (Mary Skelter) — кирилиця є у власних кодах, але і ї є ґ немає, а нові діапазони
    рушій ігнорує → SlotScheme: літери малюємо в гліфи символів, яких гра ніде не вживає
    (латинські ì ò ù ã…), а в тексті при записі підміняємо (apply_text).
  Shift-JIS (Neptunia) — кирилиця двобайтова (0x84xx), рушій дає їй ширину ієрогліфа, а
    поля фіксованої довжини вміщають удвічі менше → ByteScheme: кожна літера — один байт
    замість півширинної катакани (0xA1–0xDF, гра її не вживає) + 0xFD–0xFF.
auto() вибирає схему й слоти сам: за кодуванням шрифту і символами, які трапляються в
текстах гри (слот не може бути символом, який гра показує).
"""
from .fontfix import CYR, LOWER, UKR, UPPER


class SlotScheme:
    """UTF-8: кирилиця — у власних кодах; і ї є ґ — у невживаних слотах."""
    kind = 'slots'

    def __init__(self, slots):
        self.slots = dict(slots)                  # укр. літера -> символ-слот

    def placements(self, f):
        out = [(ch, f.code(ch)) for ch in CYR]
        out += [(ch, f.code(slot)) for ch, slot in self.slots.items()]
        return out

    def apply_text(self, text):
        """Текст -> те, що пишемо в гру (і -> ì …)."""
        return text.translate(str.maketrans(self.slots)) if text else text

    def reserved(self):
        return set(self.slots.values())


class ByteScheme:
    """Shift-JIS: кожна українська літера — один байт; ще двобайтові коди кирилиці
    (для тексту, збереженого грою раніше) і, за потреби, старі коди (legacy)."""
    kind = 'bytes'

    def __init__(self, codes, legacy=None, double_byte=True, quotes=None):
        self.codes = dict(codes)                  # літера -> байт
        self.legacy = dict(legacy or {})          # літера -> код (старі сейви тощо)
        self.double_byte = double_byte
        self.quotes = dict(quotes or {})          # « » -> байт (гліфи малює fontfix за профілем)

    def text_code(self, ch):
        """Однобайтовий код символу тексту за схемою (None — звичайний Shift-JIS)."""
        return self.codes.get(ch) or self.quotes.get(ch)

    def placements(self, f):
        out = list(self.codes.items())
        if self.double_byte:
            out += [(ch, f.code(ch)) for ch in UPPER + LOWER if f.code(ch) is not None]
        out += list(self.legacy.items())
        return out


# латинські літери, якими можна пожертвувати під і ї є ґ (пари мала/велика), у порядку переваги
SLOT_CANDIDATES = [('ì', 'Ì'), ('ò', 'Ò'), ('ù', 'Ù'), ('ã', 'Ã'), ('õ', 'Õ'), ('ñ', 'Ñ'), ('ý', 'Ý'),
                   ('è', 'È'), ('à', 'À'), ('ë', 'Ë'), ('î', 'Î'), ('û', 'Û'), ('ô', 'Ô'), ('â', 'Â'),
                   ('ê', 'Ê'), ('ç', 'Ç'), ('å', 'Å'), ('ø', 'Ø'), ('æ', 'Æ'), ('ð', 'Ð'), ('þ', 'Þ')]
KATAKANA_BYTES = list(range(0xA1, 0xE0)) + [0xFD, 0xFE, 0xFF]


def auto(f, used=''):
    """(схема, профіль) для шрифту f і символів `used`, що трапляються в текстах гри.
    Профіль — лише відмінності від fontfix.DEFAULT (для незнайомої гри — порожній)."""
    used = set(used)
    if f.encoding == 'utf-8':
        missing = [c for c in UKR if f.index(f.code(c)) is None]
        pairs = [(lo, up) for lo, up in SLOT_CANDIDATES
                 if lo not in used and up not in used
                 and f.index(f.code(lo)) is not None and f.index(f.code(up)) is not None]
        lows = [c for c in missing if c.islower()]
        slots = {}
        for (lo, up), ch in zip(pairs, lows):
            slots[ch] = lo
            if ch.upper() in missing:
                slots[ch.upper()] = up
        for ch in missing:                         # велика без малої пари
            if ch not in slots and len(pairs) > len(lows):
                slots[ch] = pairs[len(lows)][1]
        return SlotScheme(slots), {'tighten': 'wide'}
    # Shift-JIS: однобайтові коди, якщо гра не вживає півширинної катакани
    halfwidth = any('｡' <= c <= 'ﾟ' for c in used)
    if halfwidth:
        return ByteScheme({}, double_byte=True), {'tighten': 'wide'}
    codes = dict(zip(UPPER + LOWER, KATAKANA_BYTES))
    prof = {'tighten': 'wide', 'new_ranges': [(0xFD, 0x100)]}
    star, heart = f.code('☆'), f.code('∵')
    if f.index(star) is not None and f.index(heart) is not None and '∵' not in used:
        prof['heart'] = (heart, star)
    q = {}
    for ch, code in (('«', 0x80), ('»', 0xA0)):
        if chr(code) not in used:
            q[ch] = code
    if q:
        prof['quotes'] = q
    return ByteScheme(codes, quotes=q), prof
