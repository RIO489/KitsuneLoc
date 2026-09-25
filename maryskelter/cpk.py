"""CRI CPK archive reader (+ CRILAYLA decompression).

Японська версія Mary Skelter (GAME.cpk, SYSTEM.cpk) пакує файли в CPK.
Структура: заголовок 'CPK ' + таблиця @UTF з полями TocOffset/ContentOffset,
далі 'TOC ' + @UTF (DirName, FileName, FileSize, ExtractSize, FileOffset…).
Файл стиснено, якщо FileSize < ExtractSize (починається з 'CRILAYLA').
"""
import struct

_TYPES = {0: '>B', 1: '>b', 2: '>H', 3: '>h', 4: '>I', 5: '>i', 6: '>Q', 7: '>q', 8: '>f'}


def read_utf(buf, off=0):
    """@UTF-таблиця -> список рядків (dict)."""
    if buf[off:off + 4] != b'@UTF':
        raise ValueError('not an @UTF table')
    size, rows_off, str_off, data_off, name_off = struct.unpack_from('>IIIII', buf, off + 4)
    ncols, rowlen, nrows = struct.unpack_from('>HHI', buf, off + 24)
    base = off + 8
    strings = base + str_off

    def cstr(o):
        p = strings + o
        return buf[p:buf.index(b'\0', p)].decode('cp932', 'replace')

    def read_val(t, p):
        if t in _TYPES:
            fmt = _TYPES[t]
            return struct.unpack_from(fmt, buf, p)[0], struct.calcsize(fmt)
        if t == 0xA:
            return cstr(struct.unpack_from('>I', buf, p)[0]), 4
        if t == 0xB:
            o, n = struct.unpack_from('>II', buf, p)
            return (base + data_off + o, n), 8
        raise ValueError(f'unknown @UTF type {t:#x}')

    cols, p = [], off + 32
    for _ in range(ncols):
        flags = buf[p]; p += 1
        name = cstr(struct.unpack_from('>I', buf, p)[0]); p += 4
        storage, t = flags & 0xF0, flags & 0x0F
        const = None
        if storage in (0x30, 0x70):
            const, n = read_val(t, p); p += n
        cols.append((name, storage, t, const))
    rows = []
    for r in range(nrows):
        p = base + rows_off + r * rowlen
        row = {}
        for name, storage, t, const in cols:
            if storage == 0x50:
                row[name], n = read_val(t, p); p += n
            elif storage in (0x30, 0x70):
                row[name] = const
            else:
                row[name] = None
        rows.append(row)
    return rows


def crilayla(data):
    """Розтиснути блок CRILAYLA (читає бітовий потік з кінця до початку)."""
    usize, hoff = struct.unpack_from('<II', data, 8)
    out = bytearray(usize + 0x100)
    out[:0x100] = data[hoff + 0x10:hoff + 0x10 + 0x100]
    src = data
    pos = len(data) - 0x100 - 1
    pool = left = 0
    out_end = 0x100 + usize - 1
    done = 0

    def bits(n):
        nonlocal pos, pool, left
        v = got = 0
        while got < n:
            if left == 0:
                pool = src[pos]; left = 8; pos -= 1
            take = min(left, n - got)
            v = (v << take) | ((pool >> (left - take)) & ((1 << take) - 1))
            left -= take; got += take
        return v

    vle = (2, 3, 5, 8)
    while done < usize:
        if bits(1):
            ref = out_end - done + bits(13) + 3
            ln = 3
            for lv, w in enumerate(vle):
                x = bits(w)
                ln += x
                if x != (1 << w) - 1:
                    break
            else:
                while True:
                    x = bits(8)
                    ln += x
                    if x != 255:
                        break
            for _ in range(ln):
                out[out_end - done] = out[ref]
                ref -= 1
                done += 1
        else:
            out[out_end - done] = bits(8)
            done += 1
    return bytes(out)


class Cpk:
    def __init__(self, path):
        self.path = path
        self.f = open(path, 'rb')
        head = self.f.read(0x800)
        if head[:4] != b'CPK ':
            raise ValueError('not a CPK')
        size = struct.unpack_from('<I', head, 8)[0]
        hdr = read_utf(self._read(0x10, size), 0)[0]
        self.header = hdr
        toc = hdr.get('TocOffset') or 0
        content = hdr.get('ContentOffset') or 0
        self.add = min(x for x in (toc, content) if x) if (toc or content) else 0
        self.files = []
        if toc:
            tsize = struct.unpack_from('<I', self._read(toc + 8, 4), 0)[0]
            for r in read_utf(self._read(toc + 0x10, tsize), 0):
                d = (r.get('DirName') or '').strip('/')
                name = f"{d}/{r['FileName']}" if d else r['FileName']
                self.files.append({'name': name, 'size': r['FileSize'],
                                   'extract': r.get('ExtractSize') or r['FileSize'],
                                   'offset': r['FileOffset'] + self.add})
        self.by_name = {x['name'].lower(): x for x in self.files}

    def _read(self, off, n):
        self.f.seek(off)
        return self.f.read(n)

    def read(self, entry):
        if isinstance(entry, str):
            entry = self.by_name[entry.replace('\\', '/').lower()]
        raw = self._read(entry['offset'], entry['size'])
        if raw[:8] == b'CRILAYLA':
            return crilayla(raw)
        return raw
