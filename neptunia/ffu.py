"""FFU — растровий шрифт Neptunia Re;Birth1 (магія 'UF_\\0').

Заголовок: 0x04 u16 к-сть гліфів, 0x09 cell_w, 0x0a cell_h,
0x10 u32 кінець бітмапів, 0x14 зсув діапазонів, 0x18 зсув записів,
0x1c зсув бітмапів. Далі — хвіст (зберігаємо як є).
Діапазон (12 Б): u32 перший код, u32 кінець (НЕ включно), u32 перший індекс.
Запис гліфа (8 Б): u8 xadv, u8 висота, u16 розмір бітмапа, u32 зсув.
Бітмап — 4 біти на піксель (старший півбайт — лівий піксель), рядок =
розмір // висота байтів. Код символу — байти Shift-JIS як big-endian
число (однобайтові — сам байт).

Увага: старий інструмент (neptune/script/ffu.py) читав бітмап як 8 біт на
піксель, тож правки гліфів ним ішли кроком у два пікселі.
"""
import struct


class Ffu:
    def __init__(self, data):
        d = bytes(data)
        if d[:4] != b'UF_\0':
            raise ValueError('не шрифт FFU (UF_)')
        self.num = struct.unpack_from('<H', d, 4)[0]
        self.cell_w, self.cell_h = d[9], d[10]
        self.data_end, self.range_off, self.char_off, self.bmp_off = \
            struct.unpack_from('<IIII', d, 16)
        self.header = d[:self.range_off]
        self.ranges = [list(struct.unpack_from('<III', d, o))
                       for o in range(self.range_off, self.char_off, 12)]
        self.entries = [list(struct.unpack_from('<BBHI', d, self.char_off + 8 * i))
                        for i in range(self.num)]
        self.blob = bytearray(d[self.bmp_off:self.data_end])
        self.footer = d[self.data_end:]
        self.map = {}
        for rs, re_, ci in self.ranges:
            for c in range(rs, re_):          # кінець діапазону не включно
                self.map.setdefault(c, ci + (c - rs))

    # ---- читання ------------------------------------------------------
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

    # ---- запис --------------------------------------------------------
    def set_glyph_at(self, i, rows, xadv):
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
        """Новий діапазон кодів [first, end) — порожні записи в кінці таблиці."""
        start = len(self.entries)
        for _ in range(first, end):
            self.entries.append([0, 0, 0, 0])
        self.ranges.append([first, end, start])
        self.ranges.sort(key=lambda r: r[0])
        for c in range(first, end):
            self.map[c] = start + (c - first)
        return start

    def build(self):
        out = bytearray(self.header)
        for r in self.ranges:
            out += struct.pack('<III', *r)
        char_off = len(out)
        for e in self.entries:
            out += struct.pack('<BBHI', *e)
        bmp_off = len(out)
        out += self.blob
        end = len(out)
        out += self.footer
        struct.pack_into('<H', out, 4, len(self.entries))
        struct.pack_into('<IIII', out, 16, end, self.range_off, char_off, bmp_off)
        return bytes(out)
