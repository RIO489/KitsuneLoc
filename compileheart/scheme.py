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

Спільний інтерфейс обох схем (ним користуються fontfix, прев'ю, ширини, перевірка):
  placements(f)      — [(літера, код у шрифті)]: куди fontfix кладе кирилицю;
  text_code(ch, f)   — код гліфа, яким гра намалює символ тексту (None — немає);
  letters(f)         — [(символ, код)] символів схеми, яких немає в кодуванні шрифту як є;
  decode(raw)        — байти коду шрифту -> символ; plain(text) — текст, як його покаже гра;
  bad_chars(text)    — символи, яких гра не покаже; reserved() — символи, зайняті схемою;
  cap_len(text, enc) — довжина для полів фіксованої довжини (у байтах гри).
"""
from .fontfix import CYR, LOWER, UKR, UPPER


class SlotScheme:
    """UTF-8: кирилиця — у власних кодах; і ї є ґ — у невживаних слотах."""
    kind = 'slots'
    bad_note = 'потрібен гліф у шрифті'
    cap_unit = 'Б'

    def __init__(self, slots, no_glyph=''):
        self.slots = dict(slots)                  # укр. літера -> символ-слот
        self.no_glyph = set(no_glyph)             # символи без гліфа і без прийнятної заміни

    def placements(self, f):
        out = [(ch, f.code(ch)) for ch in CYR]
        out += [(ch, f.code(slot)) for ch, slot in self.slots.items()]
        return out

    def apply_text(self, text):
        """Текст -> те, що пишемо в гру (і -> ì …)."""
        return text.translate(str.maketrans(self.slots)) if text else text

    def text_code(self, ch, f):
        return f.code(self.slots.get(ch, ch))

    def letters(self, f):
        return [(ch, f.code(slot)) for ch, slot in self.slots.items()]

    @staticmethod
    def decode(raw):
        return raw.decode('utf-8')

    @staticmethod
    def plain(text):
        return text

    def bad_chars(self, text):
        return sorted({c for c in (text or '') if c in self.no_glyph})

    def reserved(self):
        return set(self.slots.values())

    @staticmethod
    def cap_len(text, enc=None):
        return len(text.encode(enc or 'utf-8', 'replace'))


class ByteScheme:
    """Shift-JIS: кожна українська літера — один байт; ще двобайтові коди кирилиці
    (для тексту, збереженого грою раніше) і, за потреби, старі коди (legacy).

    Кодування тексту: наші однобайтові літери й лапки; `prefer` — символи, які cp932 має,
    але гра малює погано (двобайтові знаки в головному вікні мають ширину ієрогліфа);
    півширинна катакана — повноширинною (вона однобайтова = слоти нашої кирилиці);
    чого cp932 не має — `fallback`, інакше «?» (errors='replace'). Байт, якого немає в
    cp932, у тексті живе як chr(RAW + байт) — туди й назад без втрат."""
    kind = 'bytes'
    bad_note = 'буде «?»'
    cap_unit = 'символів'
    RAW = 0xF700

    def __init__(self, codes, legacy=None, double_byte=True, quotes=None, fallback=None, prefer=None,
                 encoding='cp932'):
        self.codes = dict(codes)                  # літера -> байт
        self.legacy = dict(legacy or {})          # літера -> код (старі сейви тощо)
        self.double_byte = double_byte
        self.quotes = dict(quotes or {})          # « » -> байт (гліфи малює fontfix за профілем)
        self.fallback = dict(fallback or {})
        self.prefer = dict(prefer or {})
        self.encoding = encoding
        self.letter = {v: k for k, v in self.codes.items()}      # байт -> літера
        self.letter.update({v: k for k, v in self.quotes.items()})

    # ---- шрифт -----------------------------------------------------------
    def placements(self, f):
        out = list(self.codes.items())
        if self.double_byte:
            out += [(ch, f.code(ch)) for ch in UPPER + LOWER if f.code(ch) is not None]
        out += list(self.legacy.items())
        return out

    def text_code(self, ch, f=None):
        try:
            raw = self.encode(ch)
        except Exception:
            return None
        return raw[0] if len(raw) == 1 else int.from_bytes(raw, 'big')

    def letters(self, f=None):
        return list(self.codes.items()) + list(self.quotes.items())

    # ---- текст -----------------------------------------------------------
    @staticmethod
    def _halfwidth(ch):
        """Півширинна катакана (｢ ｡ ･ ﾟ…) у cp932 однобайтова — тобто потрапила б у
        слоти нашої кирилиці. Пишемо повноширинною (｢ -> 「)."""
        import unicodedata
        if not '\uff61' <= ch <= '\uff9f':
            return None
        return {'\uff9e': '\u309b', '\uff9f': '\u309c'}.get(ch) or unicodedata.normalize('NFKC', ch)

    def plain(self, text):
        """Текст таким, яким його побачить гра (’ -> ', … -> ..., ｢ -> 「)."""
        return ''.join(self.prefer.get(ch) or self._halfwidth(ch) or ch for ch in text)

    def encode(self, text, errors='strict'):
        """Рядок -> байти гри."""
        out = bytearray()
        for ch in text:
            b = self.codes.get(ch) or self.quotes.get(ch)
            if b is not None:
                out.append(b)
                continue
            if self.RAW <= ord(ch) < self.RAW + 256:
                out.append(ord(ch) - self.RAW)
                continue
            alt = self.prefer.get(ch) or self._halfwidth(ch)
            if alt is not None and alt != ch:
                out += self.encode(alt, errors)
                continue
            try:
                out += ch.encode(self.encoding)
            except UnicodeEncodeError:
                alt = self.fallback.get(ch)
                if alt is not None:
                    out += self.encode(alt, errors)
                elif errors == 'strict':
                    raise
                else:
                    out += b'?'
        return bytes(out)

    def bad_chars(self, text):
        bad = set()
        for ch in text or '':
            if (ch in self.codes or ch in self.quotes or ch in self.fallback
                    or self.RAW <= ord(ch) < self.RAW + 256):
                continue
            try:
                ch.encode(self.encoding)
            except UnicodeEncodeError:
                bad.add(ch)
        return sorted(bad)

    def decode(self, raw):
        """Байти гри -> рядок. Наші однобайтові коди — наші літери (в оригінальних файлах
        гри їх немає, тож читати так можна завжди)."""
        out, i, n = [], 0, len(raw)
        while i < n:
            b = raw[i]
            if b in self.letter:
                out.append(self.letter[b]); i += 1
            elif (0x81 <= b <= 0x9f or 0xe0 <= b <= 0xfc) and i + 1 < n:
                try:
                    out.append(raw[i:i + 2].decode(self.encoding)); i += 2
                except UnicodeDecodeError:
                    out.append(chr(self.RAW + b)); i += 1
            else:
                try:
                    out.append(raw[i:i + 1].decode(self.encoding))
                except UnicodeDecodeError:
                    out.append(chr(self.RAW + b))
                i += 1
        return ''.join(out)

    @staticmethod
    def reserved():
        return set()

    def cap_len(self, text, enc=None):
        return len(self.encode(text, 'replace'))


# латинські літери, якими можна пожертвувати під і ї є ґ (пари мала/велика), у порядку переваги
SLOT_CANDIDATES = [('ì', 'Ì'), ('ò', 'Ò'), ('ù', 'Ù'), ('ã', 'Ã'), ('õ', 'Õ'), ('ñ', 'Ñ'), ('ý', 'Ý'),
                   ('è', 'È'), ('à', 'À'), ('ë', 'Ë'), ('î', 'Î'), ('û', 'Û'), ('ô', 'Ô'), ('â', 'Â'),
                   ('ê', 'Ê'), ('ç', 'Ç'), ('å', 'Å'), ('ø', 'Ø'), ('æ', 'Æ'), ('ð', 'Ð'), ('þ', 'Þ')]
KATAKANA_BYTES = list(range(0xA1, 0xE0)) + [0xFD, 0xFE, 0xFF]
# типові заміни для Shift-JIS-рушіїв (незнайома гра): чого немає в cp932 — схожим
FALLBACK = {'Ъ': 'Ь', 'ъ': 'ь', 'Ы': 'И', 'ы': 'и', 'Э': 'Є', 'э': 'є', 'Ё': 'Е', 'ё': 'е',
            'ʼ': "'", '’': "'", '‘': "'", '“': '"', '”': '"', '„': '"', '—': '-', '–': '-',
            '…': '...', '\u00a0': ' '}
PREFER = {'’': "'", '‘': "'", '…': '...'}


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
    halfwidth = any('\uff61' <= c <= '\uff9f' for c in used)
    if halfwidth:
        return ByteScheme({}, double_byte=True, fallback=FALLBACK), {'tighten': 'wide'}
    codes = dict(zip(UPPER + LOWER, KATAKANA_BYTES))
    prof = {'tighten': 'wide', 'new_ranges': [(0xFD, 0x100)]}
    star, heart = f.code('\u2606'), f.code('\u2235')
    if f.index(star) is not None and f.index(heart) is not None and '\u2235' not in used:
        prof['heart'] = (heart, star)
    q = {}
    for ch, code in (('«', 0x80), ('»', 0xA0)):
        if chr(code) not in used:
            q[ch] = code
    if q:
        prof['quotes'] = q
    return ByteScheme(codes, quotes=q, fallback=FALLBACK, prefer=PREFER), prof
