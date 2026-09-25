"""PDA (.bra) archive reader/writer — Compile Heart / Mary Skelter.

Layout
------
header (16 B): magic 'PDA\0', u32 version(2), u32 index_offset, u32 file_count
blobs (contiguous from 16 to index_offset), each:
    u32 raw_size, u32 comp_size, u32 crc32(raw), u16 method(6=deflate), u16 flags
    raw-deflate data  (comp bytes = entry.stored - 16)
index (from index_offset), one variable-length record per file:
    u32 mtime(unix), u32 crc32(raw), u32 stored(=16+comp), u32 raw_size,
    u16 name_len(padded to 4), u16 0x20, u32 blob_offset, name bytes
"""
import struct, zlib, time, os

HDR = b'PDA\x00'
ENT = struct.Struct('<IIIIHHI')


class Entry:
    __slots__ = ('name', 'mtime', 'crc', 'stored', 'raw_size', 'off', 'flags', 'method',
                 'name_field', 'nlen')

    def __init__(self, name, mtime=0, crc=0, stored=0, raw_size=0, off=0,
                 flags=0x0018, method=6):
        self.name, self.mtime, self.crc = name, mtime, crc
        self.stored, self.raw_size, self.off = stored, raw_size, off
        self.flags, self.method = flags, method
        self.name_field = None   # original padded name bytes, preserved on repack
        self.nlen = 0

    def __repr__(self):
        return f'<Entry {self.name} raw={self.raw_size} stored={self.stored}>'


class Bra:
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.data = f.read()
        magic, self.version, self.index_off, self.count = struct.unpack_from('<4sIII', self.data, 0)
        if magic != HDR:
            raise ValueError(f'{path}: not a PDA archive ({magic!r})')
        self.entries = []
        p = self.index_off
        for _ in range(self.count):
            mtime, crc, stored, raw_size, nlen, _pad, off = ENT.unpack_from(self.data, p)
            field = self.data[p + 24:p + 24 + nlen]
            name = field.split(b'\0')[0].decode('cp932', 'replace')
            _rs, _cs, _crc, method, flags = struct.unpack_from('<IIIHH', self.data, off)
            e = Entry(name, mtime, crc, stored, raw_size, off, flags, method)
            e.name_field = field
            e.nlen = nlen
            self.entries.append(e)
            p += 24 + nlen
        self.by_name = {e.name.replace('/', '\\').lower(): e for e in self.entries}
        last = self.entries[-1]
        # some archives keep a few filler bytes between the last blob and the index
        self.index_pad = self.data[last.off + last.stored:self.index_off]

    # ---- reading -------------------------------------------------------
    def read(self, entry):
        if isinstance(entry, str):
            entry = self.by_name[entry.replace('/', '\\').lower()]
        blob = self.data[entry.off + 16:entry.off + entry.stored]
        if entry.raw_size == 0:
            return b''                               # порожній файл (у TTM1 такий є)
        if entry.method == 0:
            raw = bytes(blob[:entry.raw_size])       # збережено без стиснення
        else:
            raw = zlib.decompress(blob, -15)
        if len(raw) != entry.raw_size:
            raise ValueError(f'{entry.name}: size mismatch {len(raw)} != {entry.raw_size}')
        return raw

    def extract_all(self, outdir, progress=None):
        for i, e in enumerate(self.entries):
            dst = os.path.join(outdir, *e.name.split('\\'))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, 'wb') as f:
                f.write(self.read(e))
            if progress:
                progress(i, len(self.entries), e.name)

    @staticmethod
    def read_some(path, names):
        """Прочитати кілька файлів, не вантажачи весь архів у пам'ять.
        Повертає {ім'я як у запиті: байти}; відсутніх імен у результаті немає."""
        want = {n.replace('/', '\\').lower(): n for n in names}
        out = {}
        with open(path, 'rb') as f:
            magic, _v, index_off, count = struct.unpack('<4sIII', f.read(16))
            if magic != HDR:
                raise ValueError(f'{path}: not a PDA archive ({magic!r})')
            f.seek(index_off)
            idx = f.read()
            p = 0
            for _ in range(count):
                _mt, _crc, stored, raw_size, nlen, _pad, off = ENT.unpack_from(idx, p)
                name = idx[p + 24:p + 24 + nlen].split(b'\0')[0].decode('cp932', 'replace')
                p += 24 + nlen
                key = name.lower()
                if key not in want:
                    continue
                f.seek(off)
                blob = f.read(stored)
                method = struct.unpack_from('<H', blob, 12)[0]
                raw = (b'' if raw_size == 0 else bytes(blob[16:16 + raw_size]) if method == 0
                       else zlib.decompress(blob[16:], -15))
                out[want[key]] = raw
        return out

    # ---- writing -------------------------------------------------------
    def repack(self, out_path, replacements=None, level=9, progress=None):
        """Write a new archive; `replacements` maps name -> new raw bytes."""
        repl = {k.replace('/', '\\').lower(): v for k, v in (replacements or {}).items()}
        blobs, index, off = [], [], 16
        now = int(time.time())
        for i, e in enumerate(self.entries):
            key = e.name.replace('/', '\\').lower()
            if key in repl:
                raw = repl[key]
                comp = zlib.compress(raw, level)[2:-4]          # raw deflate
                crc, mtime = zlib.crc32(raw) & 0xffffffff, now
                blob = struct.pack('<IIIHH', len(raw), len(comp), crc, 6, e.flags) + comp
                raw_size, stored = len(raw), 16 + len(comp)
            else:                                                # copy blob verbatim
                blob = self.data[e.off:e.off + e.stored]
                raw_size, stored, crc, mtime = e.raw_size, e.stored, e.crc, e.mtime
            blobs.append(blob)
            field, nlen = e.name_field, e.nlen
            if field is None:
                name = e.name.encode('cp932')
                nlen = (len(name) + 3) & ~3
                field = name.ljust(nlen, b'\0')
            index.append(ENT.pack(mtime, crc, stored, raw_size, nlen, 0x20, off) + field)
            off += stored
            if progress:
                progress(i, len(self.entries), e.name)
        with open(out_path, 'wb') as f:
            f.write(struct.pack('<4sIII', HDR, self.version, off + len(self.index_pad),
                                len(self.entries)))
            for b in blobs:
                f.write(b)
            f.write(self.index_pad)
            for b in index:
                f.write(b)
        return out_path
