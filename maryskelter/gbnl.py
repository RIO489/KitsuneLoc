"""GBIN / GSTR string container (Compile Heart).

GBIN keeps a 0x40-byte descriptor as a *footer*, GSTR as a *header*.
The descriptor points at a type table (field type + offset inside the record),
a record array and a string pool.  Fields of type 5 hold a u32 offset into the
pool (0xffffffff = no string).

Mary Skelter mixes things up:
  * byte order — most files are little-endian ('L'), a few are big-endian ('B');
  * text encoding — dialogue (.gbin in EVENT\\DATA) is UTF-8,
    menus/databases (.gstr, DATABASE\\*.gbin) are Shift-JIS (cp932).
Both are detected per file and preserved on rebuild.
"""
import struct

U32, U8, U16, FLOAT, STRING = 0, 1, 2, 3, 5
DESC_FMT = '3sc H H I I I I I I I I I I I 12x'


def detect_encoding(blobs):
    """UTF-8, якщо всі рядки валідні UTF-8, інакше Shift-JIS."""
    for b in blobs:
        try:
            b.decode('utf-8')
        except UnicodeDecodeError:
            return 'cp932'
    return 'utf-8'


class Gbnl:
    def __init__(self, data, encoding=None):
        self.data = bytearray(data)
        if data[:3] == b'GST':
            self.is_gbin, self.desc_off = False, 0
        elif data[-0x40:-0x3d] == b'GBN':
            self.is_gbin, self.desc_off = True, len(data) - 0x40
        else:
            raise ValueError('not a GBIN/GSTR container')
        endian = bytes(self.data[self.desc_off + 3:self.desc_off + 4])
        if endian not in (b'L', b'B'):
            raise ValueError(f'unknown endian marker {endian!r}')
        self.bo = '<' if endian == b'L' else '>'
        self.DESC = struct.Struct(self.bo + DESC_FMT)
        (self.magic, self.endian, self.f04, self.f06, self.f08, self.f0c,
         self.flags, self.struct_off, self.struct_count, self.struct_size,
         self.types_count, self.types_off, self.f28, self.string_off,
         self.f30) = self.DESC.unpack_from(self.data, self.desc_off)
        self.types = [struct.unpack_from(self.bo + 'HH', self.data, self.types_off + 4 * i)
                      for i in range(self.types_count)]
        self.str_fields = [off for t, off in self.types if t == STRING]
        self.align = 16 if self.is_gbin else 1
        self.encoding = encoding or detect_encoding(self._raw_strings())

    # ---- low level -------------------------------------------------------
    def _u32(self, buf, off):
        return struct.unpack_from(self.bo + 'I', buf, off)[0]

    def _raw_at(self, rel):
        p = self.string_off + rel
        return bytes(self.data[p:self.data.index(b'\0', p)])

    def _refs(self, buf=None):
        buf = self.data if buf is None else buf
        for i in range(self.struct_count):
            base = self.struct_off + i * self.struct_size
            for fo in self.str_fields:
                rel = self._u32(buf, base + fo)
                if rel != 0xffffffff:
                    yield i, fo, base + fo, rel

    def _raw_strings(self):
        return [self._raw_at(rel) for _i, _fo, _p, rel in self._refs()]

    def decode(self, b):
        return b.decode(self.encoding, 'surrogateescape')

    def encode(self, s):
        return s.encode(self.encoding, 'surrogateescape')

    # ---- strings ---------------------------------------------------------
    def strings(self):
        """[(record_index, field_offset, text)] in file order."""
        return [(i, fo, self.decode(self._raw_at(rel))) for i, fo, _p, rel in self._refs()]

    def build(self, new):
        """`new` maps (record_index, field_offset) -> text. Returns bytes.

        Raises UnicodeEncodeError if a new string can't be represented in the
        file's encoding (e.g. 'ї' in a Shift-JIS file) — callers should check
        with `can_encode()` first.
        """
        if not self.str_fields or not any(True for _ in self._refs()):
            return bytes(self.data)          # файл без рядків — нема що міняти
        if self.struct_off + self.struct_count * self.struct_size > self.string_off:
            raise ValueError('string pool is not the last section')
        pool, index = bytearray(), {}

        def intern(b):
            if b not in index:
                index[b] = len(pool)
                pool.extend(b + b'\0')
            return index[b]

        body = bytearray(self.data[:self.string_off])
        for i, fo, pos, rel in list(self._refs(body)):
            old = self._raw_at(rel)
            b = self.encode(new[(i, fo)]) if (i, fo) in new else old
            struct.pack_into(self.bo + 'I', body, pos, intern(b))
        if not self.is_gbin:
            return bytes(body + pool)
        out = body + pool
        while len(out) % self.align:
            out.append(0)
        desc = self.DESC.pack(self.magic, self.endian, self.f04, self.f06, self.f08,
                              self.f0c, self.flags, self.struct_off, self.struct_count,
                              self.struct_size, self.types_count, self.types_off,
                              self.f28, self.string_off, self.f30)
        return bytes(out + desc)

    def can_encode(self, s):
        try:
            s.encode(self.encoding)
            return True
        except UnicodeEncodeError:
            return False
