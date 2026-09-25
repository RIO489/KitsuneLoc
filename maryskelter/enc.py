"""`.enc` — контейнер секцій (data/table.enc, DLCINFO/*.enc).

  u32 кількість секцій
  далі на кожну: u32 розмір_у_файлі | 0x80000000 (прапорець стиснення),
                 u32 розмір_розпакованої, u32 зсув від початку файлу
  далі самі секції, вирівняні по 0x100

Стиснені секції — LZO1X-1. Гра перевіряє прапорець і для нестиснених
просто копіює байти, тож при перезбиранні змінені секції пишемо без
стиснення: прапорець знімаємо, розмір_у_файлі = розмір_розпакованої.
"""
import struct

from . import lzo1x

FLAG = 0x80000000
ALIGN = 0x100


class Section:
    __slots__ = ('index', 'stored', 'raw_size', 'offset', 'compressed')

    def __init__(self, index, stored, raw_size, offset, compressed):
        self.index, self.stored, self.raw_size = index, stored, raw_size
        self.offset, self.compressed = offset, compressed

    def __repr__(self):
        return (f'<Section {self.index} {"LZO" if self.compressed else "як є"} '
                f'{self.stored}->{self.raw_size} @{self.offset}>')


class Enc:
    def __init__(self, data):
        self.data = bytes(data)
        self.count, = struct.unpack_from('<I', self.data, 0)
        self.sections = []
        for i in range(self.count):
            st, raw, off = struct.unpack_from('<III', self.data, 4 + 12 * i)
            self.sections.append(Section(i, st & ~FLAG, raw, off, bool(st & FLAG)))

    def read(self, i):
        s = self.sections[i] if not isinstance(i, Section) else i
        blob = self.data[s.offset:s.offset + s.stored]
        if not s.compressed:
            return blob
        out = lzo1x.decompress(blob)
        if len(out) != s.raw_size:
            raise ValueError(f'секція {s.index}: {len(out)} байтів замість {s.raw_size}')
        return out

    def build(self, new):
        """`new` — {номер секції: байти}. Змінені пишемо без стиснення."""
        head = bytearray(4 + 12 * self.count)
        struct.pack_into('<I', head, 0, self.count)
        body, base = bytearray(), (len(head) + ALIGN - 1) // ALIGN * ALIGN
        head.extend(b'\0' * (base - len(head)))
        for s in self.sections:
            while (base + len(body)) % ALIGN:
                body.append(0)
            off = base + len(body)
            if s.index in new:
                blob, raw, comp = bytes(new[s.index]), len(new[s.index]), False
            else:
                blob, raw, comp = self.data[s.offset:s.offset + s.stored], s.raw_size, s.compressed
            struct.pack_into('<III', head, 4 + 12 * s.index,
                             len(blob) | (FLAG if comp else 0), raw, off)
            body.extend(blob)
        return bytes(head + body)
