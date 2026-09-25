"""GBIN / GSTR / GBNL — таблиці з рядками (Compile Heart, Re;Birth1).

Та сама будова, що й у Mary Skelter (див. maryskelter/gbnl.py): дескриптор
0x40 Б (GSTR — на початку, GBIN/GBNL — у кінці), таблиця типів полів,
записи, пул рядків. Відмінність Re;Birth1 — рядки ФІКСОВАНОЇ довжини:
поле типу 1 (u8), після якого до наступного поля більше 4 байтів, — це
не байт, а рядок у слоті (так вирішує й neptools). Переклад пишеться в
той самий слот, решта — нулі; останній байт лишаємо під NUL.

Рядки в пулі (тип 5) переписуються будь-якої довжини.
Кодування — cp932 з нашою однобайтовою кирилицею (neptunia/chars.py).
"""
import struct
from . import chars

U32, U8, U16, FLOAT, STRING = 0, 1, 2, 3, 5
DESC_FMT = '3sc H H I I I I I I I I I I I 12x'


class Gbnl:
    def __init__(self, data):
        self.data = bytearray(data)
        if data[:3] in (b'GST',):
            self.is_gstr, self.desc_off = True, 0
        elif data[-0x40:-0x3d] == b'GBN':
            self.is_gstr, self.desc_off = False, len(data) - 0x40
        else:
            raise ValueError('не GBIN/GSTR')
        endian = bytes(self.data[self.desc_off + 3:self.desc_off + 4])
        self.bo = '<' if endian == b'L' else '>'
        self.DESC = struct.Struct(self.bo + DESC_FMT)
        (self.magic, self.endian, self.f04, self.f06, self.f08, self.f0c,
         self.flags, self.struct_off, self.struct_count, self.struct_size,
         self.types_count, self.types_off, self.f28, self.string_off,
         self.f30) = self.DESC.unpack_from(self.data, self.desc_off)
        self.types = [struct.unpack_from(self.bo + 'HH', self.data, self.types_off + 4 * i)
                      for i in range(self.types_count)]
        self.fields = []            # (зсув, 'str' | 'fix', розмір слота)
        for k, (t, off) in enumerate(self.types):
            nxt = self.types[k + 1][1] if k + 1 < len(self.types) else self.struct_size
            if t == STRING:
                self.fields.append((off, 'str', 4))
            elif t == U8 and nxt - off - 1 > 3:
                self.fields.append((off, 'fix', nxt - off))

    # ---- читання -------------------------------------------------------
    def _u32(self, buf, off):
        return struct.unpack_from(self.bo + 'I', buf, off)[0]

    def _raw_at(self, rel):
        p = self.string_off + rel
        return bytes(self.data[p:self.data.index(b'\0', p)])

    def items(self):
        """[(запис, зсув поля, 'str'|'fix', байти, місткість|None)] — у порядку файлу."""
        out = []
        for i in range(self.struct_count):
            base = self.struct_off + i * self.struct_size
            for fo, kind, size in self.fields:
                if kind == 'str':
                    rel = self._u32(self.data, base + fo)
                    if rel == 0xffffffff:
                        continue
                    out.append((i, fo, kind, self._raw_at(rel), None))
                else:
                    raw = bytes(self.data[base + fo:base + fo + size])
                    out.append((i, fo, kind, raw.split(b'\0')[0], size - 1))
        return out

    def strings(self):
        """[(запис, зсув, текст, місткість|None)]"""
        return [(i, fo, chars.decode(raw), cap) for i, fo, _k, raw, cap in self.items()]

    def record_u32(self, i, off):
        return self._u32(self.data, self.struct_off + i * self.struct_size + off)

    # ---- запис ---------------------------------------------------------
    def build(self, new):
        """new: {(запис, зсув): байти}. Повертає (байти, [(запис, зсув, треба, є)])
        — другим списком ідуть рядки, що не влізли у фіксований слот (їх лишаємо)."""
        too_long = []
        body = bytearray(self.data[:self.string_off] if self.flags else self.data)
        for (i, fo), b in new.items():
            f = next((x for x in self.fields if x[0] == fo), None)
            if f is None or f[1] != 'fix':
                continue
            if len(b) > f[2] - 1:
                too_long.append((i, fo, len(b), f[2] - 1))
                continue
            p = self.struct_off + i * self.struct_size + fo
            body[p:p + f[2]] = b.ljust(f[2], b'\0')
        has_pool = any(k == 'str' for _o, k, _s in self.fields)
        if not has_pool or not self.flags:
            if not self.is_gstr:
                return bytes(body[:self.desc_off]) + bytes(self.data[self.desc_off:]), too_long
            return bytes(body), too_long
        if self.struct_off + self.struct_count * self.struct_size > self.string_off:
            raise ValueError('пул рядків — не остання секція')
        pool, index = bytearray(), {}

        def intern(b):
            if b not in index:
                index[b] = len(pool)
                pool.extend(b + b'\0')
            return index[b]

        for i in range(self.struct_count):
            base = self.struct_off + i * self.struct_size
            for fo, kind, _s in self.fields:
                if kind != 'str':
                    continue
                rel = self._u32(self.data, base + fo)
                if rel == 0xffffffff:
                    continue
                b = new.get((i, fo))
                if b is None:
                    b = self._raw_at(rel)
                struct.pack_into(self.bo + 'I', body, base + fo, intern(b))
        if self.is_gstr:
            return bytes(body + pool), too_long
        out = body + pool
        while len(out) % 16:
            out.append(0)
        desc = self.DESC.pack(self.magic, self.endian, self.f04, self.f06, self.f08,
                              self.f0c, self.flags, self.struct_off, self.struct_count,
                              self.struct_size, self.types_count, self.types_off,
                              self.f28, self.string_off, self.f30)
        return bytes(out + desc), too_long
