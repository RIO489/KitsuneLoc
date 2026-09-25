"""TTM1.bra Text\\TextData.bin / TextDataEx.bin — таблиці рядків інтерфейсу.

  u32 count, 12 байтів нулів
  count × { u32 offset, 12 байтів нулів }
  NUL-термінований UTF-8 пул рядків, + хвіст (1 байт)

Перенос рядка всередині тексту позначено `#n`.
"""
import struct


class TextData:
    def __init__(self, data):
        self.data = bytes(data)
        self.count, = struct.unpack_from('<I', self.data, 0)
        self.head = self.data[:16]
        self.ents = []                       # (offset, 12 байтів хвоста запису)
        for i in range(self.count):
            p = 16 + 16 * i
            self.ents.append((struct.unpack_from('<I', self.data, p)[0], self.data[p + 4:p + 16]))
        self.pool_off = 16 + 16 * self.count
        self.tail = b''
        self.tail = self.data[len(self._assemble({})):]   # що лишилось після пулу

    def raw(self, i):
        o = self.ents[i][0]
        return self.data[o:self.data.index(b'\0', o)]

    def strings(self):
        return [self.raw(i).decode('utf-8', 'surrogateescape') for i in range(self.count)]

    def build(self, new):
        """new: {індекс: текст}. Порядок і спільні зміщення зберігаються."""
        return self._assemble(new) + self.tail

    def _assemble(self, new):
        vals = [new[i].encode('utf-8', 'surrogateescape') if i in new else self.raw(i)
                for i in range(self.count)]
        groups = {}
        for i, (o, _t) in enumerate(self.ents):
            groups.setdefault(o, []).append(i)
        pool, pos, slot = bytearray(), self.pool_off, {}
        for o in sorted(groups):
            seen = {}
            for i in groups[o]:
                v = vals[i]
                if v not in seen:
                    seen[v] = pos
                    # порожній рядок в оригіналі займає 2 байти (\0\0)
                    b = v + b'\0' if v else b'\0\0'
                    pool.extend(b)
                    pos += len(b)
                slot[i] = seen[v]
        out = bytearray(self.head)
        for i, (_o, t) in enumerate(self.ents):
            out.extend(struct.pack('<I', slot[i]) + t)
        out.extend(pool)
        return bytes(out)
