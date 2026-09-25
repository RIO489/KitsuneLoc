"""DW_PACK (.pac) — архіви Hyperdimension Neptunia Re;Birth1 (PC).

Будова (за вихідниками nr2_unpacker):
  header 20 Б: 'DW_PACK\\0', u32 0, u32 file_count, u32 status
  file_count × 288 Б: u32 a, u16 index, u16 b, path[264] (cp932, '\\'),
                      u32 pack_size, u32 unpack_size, u32 packed(1|0), u32 offset
  дані — від 20 + 288·count, зсув файлу відносно цього місця.

Стиснений файл (packed=1):
  u32 0x1234, u32 chunk_count, u32 file_type, u32 hdr_offset,
  chunk_count × {u32 unpack_size, u32 pack_size, u32 data_offset};
  кожен шматок — Huffman: спершу дерево (біт 1 = вузол, 0 = лист + 8 біт),
  далі коди; біти читаються від старшого. Корінь-лист = шматок з одного байта.

Гра приймає й нестиснені файли (packed=0) — так ми й пишемо змінені.
"""
import os, struct

MAGIC = b'DW_PACK\0'
HDR = struct.Struct('<8sIII')
ENT = struct.Struct('<IHH264sIIII')


class Entry:
    __slots__ = ('i', 'a', 'index', 'b', 'name', 'pack_size', 'unpack_size', 'packed', 'off')

    def __repr__(self):
        return f'<Entry {self.name} {self.unpack_size}{" z" if self.packed else ""}>'


def _huff_chunk(src, pos, end, size):
    """Розпакувати один шматок Huffman."""
    acc, nbits = 0, 0
    data = src

    def bits(n):
        nonlocal acc, nbits, pos
        while nbits < n:
            acc = (acc << 8) | (data[pos] if pos < end else 0)
            pos += 1
            nbits += 8
        nbits -= n
        v = (acc >> nbits) & ((1 << n) - 1)
        acc &= (1 << nbits) - 1
        return v

    left, right = [], []

    def node():
        if bits(1):
            k = len(left)
            left.append(0); right.append(0)
            left[k] = node()
            right[k] = node()
            return 256 + k
        return bits(8)

    root = node()
    if root < 256:
        return bytes([root]) * size
    # таблиця на 12 біт: вікно -> (символ | вузол, скільки біт з'їдено)
    K = 12
    table = []
    for w in range(1 << K):
        n, used = root, 0
        while n >= 256 and used < K:
            b = (w >> (K - 1 - used)) & 1
            n = right[n - 256] if b else left[n - 256]
            used += 1
        table.append((n, used))
    out = bytearray(size)
    o = 0
    while o < size:
        while nbits < 24:
            acc = (acc << 8) | (data[pos] if pos < end else 0)
            pos += 1
            nbits += 8
        n, used = table[(acc >> (nbits - K)) & 0xfff]
        nbits -= used
        while n >= 256:
            nbits -= 1
            n = right[n - 256] if (acc >> nbits) & 1 else left[n - 256]
            if nbits < 1 and n >= 256:
                acc = (acc << 8) | (data[pos] if pos < end else 0)
                pos += 1
                nbits += 8
        acc &= (1 << nbits) - 1
        out[o] = n
        o += 1
    return bytes(out)


def decompress(blob):
    magic, cnt, _ftype, hoff = struct.unpack_from('<IIII', blob, 0)
    if magic != 0x1234:
        raise ValueError('немає сигнатури 0x1234 у стисненому файлі')
    out = []
    for k in range(cnt):
        usize, psize, doff = struct.unpack_from('<III', blob, 16 + 12 * k)
        start = hoff + doff
        out.append(_huff_chunk(blob, start, start + psize, usize))
    return b''.join(out)


class Pac:
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            magic, self.f08, count, self.status = HDR.unpack(f.read(HDR.size))
            if magic != MAGIC:
                raise ValueError(f'{path}: не DW_PACK ({magic!r})')
            raw = f.read(ENT.size * count)
        self.base = HDR.size + ENT.size * count
        self.entries = []
        for i in range(count):
            e = Entry()
            (e.a, e.index, e.b, name, e.pack_size, e.unpack_size, e.packed,
             e.off) = ENT.unpack_from(raw, i * ENT.size)
            e.i = i
            e.name = name.split(b'\0')[0].decode('cp932', 'replace')
            self.entries.append(e)
        self.by_name = {e.name.lower(): e for e in self.entries}

    def get(self, name):
        return self.by_name.get(name.replace('/', '\\').lower())

    def read_raw(self, e, f=None):
        if f is None:
            with open(self.path, 'rb') as f2:
                return self.read_raw(e, f2)
        f.seek(self.base + e.off)
        return f.read(e.pack_size)

    def read(self, e, f=None):
        if isinstance(e, str):
            name, e = e, self.get(e)
            if e is None:
                raise KeyError(name)
        if not e.pack_size or not e.unpack_size:
            return b''
        blob = self.read_raw(e, f)
        return decompress(blob) if e.packed == 1 else blob[:e.unpack_size]

    def read_some(self, names):
        """{ім'я як у запиті: байти}; відсутніх у результаті немає."""
        out = {}
        with open(self.path, 'rb') as f:
            for n in names:
                e = self.get(n)
                if e is not None:
                    out[n] = self.read(e, f)
        return out

    def repack(self, out_path, replacements, progress=None):
        """Новий архів: змінені файли пишемо нестисненими, решту копіюємо як є
        (у тому ж порядку й зі старими полями запису)."""
        repl = {k.replace('/', '\\').lower(): v for k, v in replacements.items()}
        heads, off = [], 0
        plan = []
        for e in self.entries:
            new = repl.get(e.name.lower())
            if new is not None:
                size, packed, usize = len(new), 0, len(new)
            else:
                size, packed, usize = e.pack_size, e.packed, e.unpack_size
            name = e.name.encode('cp932').ljust(264, b'\0')
            heads.append(ENT.pack(e.a, e.index, e.b, name, size, usize, packed, off))
            plan.append((e, new))
            off += size
        tmp = out_path + '.tmp'
        with open(self.path, 'rb') as src, open(tmp, 'wb') as dst:
            dst.write(HDR.pack(MAGIC, self.f08, len(self.entries), self.status))
            dst.write(b''.join(heads))
            for k, (e, new) in enumerate(plan):
                if progress:
                    progress(k, len(plan), e.name)
                if new is not None:
                    dst.write(new)
                elif e.pack_size:
                    src.seek(self.base + e.off)
                    left = e.pack_size
                    while left:
                        chunk = src.read(min(left, 1 << 22))
                        dst.write(chunk)
                        left -= len(chunk)
        os.replace(tmp, out_path)
        return out_path
