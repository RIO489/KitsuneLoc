# -*- coding: utf-8 -*-
"""FFU — растровий шрифт рушія Compile Heart / Idea Factory (обидва відомі варіанти).

Заголовок: 0x00 магія 'UF' + 2 байти варіанта, 0x04 u16 к-сть гліфів, 0x09 cell_w,
0x0a cell_h, 0x10 u32 кінець бітмапів, 0x14 зсув діапазонів, 0x18 зсув записів,
0x1c зсув бітмапів. Далі — хвіст (зберігаємо як є).
Діапазон (12 Б): u32 перший код, u32 кінець, u32 перший індекс.
Запис гліфа (8 Б): u8 xadv, u8 висота, u16 розмір бітмапа, u32 зсув.
Бітмап — 4 біти на піксель (старший півбайт — лівий піксель), рядок = розмір // висота.

Варіанти (визначаються за файлом, не за грою):
  'UF \\x02' (Mary Skelter) — код = байти UTF-8 символу як big-endian;
  'UF_\\0'   (Neptunia)     — код = байти Shift-JIS (однобайтові — сам байт).
В обох кінець діапазону НЕ включно: сума (кінець − початок) = к-сть гліфів (MSK 29 352,
Nep 7 110). Старий maryskelter/ffu читав «включно» — зайвий код на діапазон був
неправильним UTF-8 (0xC2C0…), тож ні на що не впливав.
Невідома магія — кодування й межі вгадуються з таблиці діапазонів (`_guess`).
"""
import struct

VARIANTS = {b'UF \x02': ('utf-8', False), b'UF_\x00': ('cp932', False)}


def _guess(ranges, num):
    """(кодування, кінець включно) для невідомої магії."""
    incl = sum(e - s + 1 for s, e, _i in ranges) <= num or sum(e - s for s, e, _i in ranges) > num
    # UTF-8: латиниця й кирилиця — 0xC2xx..0xDFxx / 0xE0xxxx; Shift-JIS: 0x81xx..0x9Fxx, 0xE0xx..0xFCxx
    utf = sum(1 for s, _e, _i in ranges if 0xC280 <= s <= 0xDFBF or 0xE08080 <= s <= 0xEFBFBF)
    sjis = sum(1 for s, _e, _i in ranges if 0x8140 <= s <= 0x9FFC or 0xE040 <= s <= 0xFCFC)
    return ('utf-8' if utf >= sjis else 'cp932'), incl


class Ffu:
    def __init__(self, data):
        d = bytes(data)
        if d[:2] != b'UF':
            raise ValueError('не шрифт FFU')
        self.magic = d[:4]
        self.num = struct.unpack_from('<H', d, 4)[0]
        self.cell_w, self.cell_h = d[9], d[10]
        self.data_end, self.range_off, self.char_off, self.bmp_off = struct.unpack_from('<IIII', d, 16)
        self.header = d[:self.range_off]
        self.ranges = [list(struct.unpack_from('<III', d, o)) for o in range(self.range_off, self.char_off, 12)]
        self.entries = [list(struct.unpack_from('<BBHI', d, self.char_off + 8 * i)) for i in range(self.num)]
        self.blob = bytearray(d[self.bmp_off:self.data_end])
        self.footer = d[self.data_end:]
        self.encoding, self.end_incl = VARIANTS.get(self.magic) or _guess(self.ranges, self.num)
        self._resort = False                         # (UTF-8-варіант) діапазони сортуються при збиранні
        self._remap()

    def _remap(self):
        self.map = {}
        for rs, re_, ci in self.ranges:
            for c in range(rs, re_ + 1 if self.end_incl else re_):
                if self.end_incl:
                    self.map[c] = ci + (c - rs)          # так читав maryskelter/ffu (останній перемагає)
                else:
                    self.map.setdefault(c, ci + (c - rs))

    # ---- коди ------------------------------------------------------------
    def code(self, ch):
        """Код символу в цьому шрифті (None — кодування шрифту його не має)."""
        try:
            b = ch.encode(self.encoding)
        except UnicodeEncodeError:
            return None
        return int.from_bytes(b, 'big')

    # ---- читання ---------------------------------------------------------
    def index(self, code):
        i = self.map.get(code)
        return i if i is not None and i < len(self.entries) else None

    def glyph(self, code):
        """-> (xadv, [рядки пікселів 0..15]) або None."""
        i = self.index(code)
        return None if i is None else self.glyph_at(i)

    def glyph_at(self, i):
        xadv, h, size, off = self.entries[i]
        wb = size // h if h else 0
        rows = []
        for r in range(h):
            row = []
            for b in self.blob[off + r * wb:off + (r + 1) * wb]:
                row += (b >> 4, b & 15)
            rows.append(row)
        return xadv, rows

    @staticmethod
    def bbox(rows, thr=1):
        """(L, R, T, B) пікселів з яскравістю ≥ thr або None."""
        xs = [x for r in rows for x, v in enumerate(r) if v >= thr]
        ys = [y for y, r in enumerate(rows) if any(v >= thr for v in r)]
        if not xs:
            return None
        return min(xs), max(xs), min(ys), max(ys)

    # ---- запис -----------------------------------------------------------
    def set_glyph_at(self, i, rows, xadv):
        """Новий бітмап дописується в кінець (старий лишається, як у грі)."""
        h = len(rows)
        w = len(rows[0]) if h else 0
        if w % 2:
            rows = [r + [0] for r in rows]
            w += 1
        bm = bytearray()
        for r in rows:
            for x in range(0, w, 2):
                bm.append((r[x] << 4) | r[x + 1])
        off = len(self.blob)
        self.blob += bm
        self.entries[i] = [xadv, h, len(bm), off]

    def add_range(self, first, end):
        """Нові коди [first, end) — порожні записи в кінці таблиці. Повертає перший індекс."""
        start = len(self.entries)
        self.entries += [[0, 0, 0, 0] for _ in range(first, end)]
        self.ranges.append([first, end - 1 if self.end_incl else end, start])
        if self.end_incl:
            self._resort = True
        else:
            self.ranges.sort(key=lambda r: r[0])
        for c in range(first, end):
            self.map[c] = start + (c - first)
        return start

    def build(self):
        ranges = sorted(self.ranges, key=lambda r: r[0]) if (self.end_incl and self._resort) else self.ranges
        out = bytearray(self.header)
        for r in ranges:
            out += struct.pack('<III', *r)
        char_off = len(out)
        for e in self.entries:
            out += struct.pack('<BBHI', *e)
        bmp_off = len(out)
        out += self.blob
        end = len(out)
        out += self.footer
        struct.pack_into('<H', out, 4, len(self.entries))
        struct.pack_into('<IIII', out, 16, end, len(self.header), char_off, bmp_off)
        return bytes(out)
