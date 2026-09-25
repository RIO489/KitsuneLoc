# -*- coding: utf-8 -*-
"""FFU (Idea Factory / Compile Heart) font for Mary Skelter Nightmares.

Header (magic 'UF \x02'):
  0x00 magic, 0x04 u16 num_chars, 0x09 cell_w, 0x0a cell_h,
  0x10 u32 data_size, 0x14 range_off, 0x18 char_off, 0x1c bitmap_off
Range entry  (12 b): u32 key_start, u32 key_end, u32 first_index
Char entry   ( 8 b): u8 xadv, u8 height, u16 bmp_size, u32 bmp_off
Key = UTF-8 bytes of the character read as a big-endian integer.
Bitmap = 4 bits per pixel, row width = bmp_size // height bytes (2 px/byte).
"""
import struct

def key(ch):
    return int.from_bytes(ch.encode('utf-8'), 'big')


class Ffu:
    def __init__(self, data):
        self.data = bytearray(data)
        d = self.data
        assert d[:3] == b'UF ', 'not a FFU font'
        self.num = struct.unpack_from('<H', d, 4)[0]
        self.cell_w = d[9]
        self.cell_h = d[10]
        self.data_size = struct.unpack_from('<I', d, 16)[0]
        self.range_off, self.char_off, self.bmp_off = struct.unpack_from('<III', d, 20)
        self.header = bytes(d[:self.range_off])
        self.ranges = []
        o = self.range_off
        while o < self.char_off:
            self.ranges.append(list(struct.unpack_from('<III', d, o)))
            o += 12
        self.entries = []
        for i in range(self.num):
            o = self.char_off + i * 8
            xadv, h = d[o], d[o + 1]
            size = struct.unpack_from('<H', d, o + 2)[0]
            off = struct.unpack_from('<I', d, o + 4)[0]
            self.entries.append([xadv, h, size, off])
        self.blob = bytes(d[self.bmp_off:self.data_size])
        self.footer = bytes(d[self.data_size:])
        self.map = {}
        for rs, re_, ci in self.ranges:
            for c in range(rs, re_ + 1):
                self.map[c] = ci + (c - rs)

    # ---- glyph access -------------------------------------------------
    def index(self, ch):
        return self.map.get(key(ch))

    def glyph(self, ch):
        """-> (xadv, rows) where rows is a list of lists of nibbles (0..15)."""
        i = self.index(ch)
        if i is None:
            return None
        xadv, h, size, off = self.entries[i]
        wb = size // h if h else 0
        bm = self.blob[off:off + size]
        rows = []
        for r in range(h):
            row = bm[r * wb:(r + 1) * wb]
            px = []
            for by in row:
                px.append(by >> 4)
                px.append(by & 0xF)
            rows.append(px)
        return xadv, rows

    @staticmethod
    def bbox(rows):
        L, R, T, B = 10 ** 9, -1, 10 ** 9, -1
        for r, px in enumerate(rows):
            for x, v in enumerate(px):
                if v:
                    L = min(L, x); R = max(R, x)
                    T = min(T, r); B = max(B, r)
        if R < 0:
            return None
        return L, R, T, B

    # ---- editing ------------------------------------------------------
    def set_glyph(self, ch, rows, xadv, create=False):
        """Replace (or add) a glyph. rows = list of nibble lists, all same length."""
        h = len(rows)
        w = len(rows[0])
        if w % 2:
            w += 1
            rows = [r + [0] for r in rows]
        bm = bytearray()
        for r in rows:
            for x in range(0, w, 2):
                bm.append((r[x] << 4) | r[x + 1])
        off = len(self._new_blob)
        self._new_blob += bytes(bm)
        ent = [xadv, h, len(bm), self.bmp_len + off]
        i = self.index(ch)
        if i is None:
            if not create:
                raise KeyError(ch)
            i = self.num + len(self._added)
            self._added.append((key(ch), i, ent))
        else:
            self.entries[i] = ent
        return i

    def begin(self):
        self._new_blob = b''
        self._added = []
        self.bmp_len = len(self.blob)

    def build(self):
        ranges = [list(r) for r in self.ranges]
        for k, idx, ent in self._added:
            ranges.append([k, k, idx])
        ranges.sort(key=lambda r: r[0])
        entries = [list(e) for e in self.entries]
        for k, idx, ent in sorted(self._added, key=lambda a: a[1]):
            while len(entries) <= idx:
                entries.append([0, 0, 0, 0])
            entries[idx] = ent
        num = len(entries)
        range_off = len(self.header)
        char_off = range_off + len(ranges) * 12
        bmp_off = char_off + num * 8
        blob = self.blob + self._new_blob
        out = bytearray(self.header)
        struct.pack_into('<H', out, 4, num)
        for r in ranges:
            out += struct.pack('<III', *r)
        for e in entries:
            out += struct.pack('<BBHI', e[0], e[1], e[2], e[3])
        out += blob
        struct.pack_into('<I', out, 16, bmp_off + len(blob))
        out += self.footer
        struct.pack_into('<III', out, 20, range_off, char_off, bmp_off)
        return bytes(out)
