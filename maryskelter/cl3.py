"""CL3 container (Compile Heart) — uncompressed sub-files plus a link table."""
import struct

HDR = struct.Struct('<3scIIII')          # magic, endian, f04, f08, sect_count, sect_off
SECT = struct.Struct('<32sIII36x')       # name, count, data_size, data_off
FILE = struct.Struct('<512sIIIII28x')    # name, f200, off, size, link_start, link_count
assert SECT.size == 0x50 and FILE.size == 0x230


class Cl3:
    def __init__(self, data):
        self.data = bytes(data)
        magic, self.endian, self.f04, self.f08, n, so = HDR.unpack_from(self.data, 0)
        if magic != b'CL3':
            raise ValueError('not a CL3')
        self.f14, = struct.unpack_from('<I', self.data, 0x14)
        self.sections = []
        for i in range(n):
            name, cnt, size, off = SECT.unpack_from(self.data, so + i * 0x50)
            self.sections.append([name.split(b'\0')[0].decode(), cnt, size, off])
        self.files = []                  # (name, data) in order
        self.coll = next((s for s in self.sections if s[0] == 'FILE_COLLECTION'), None)
        if self.coll:
            _, cnt, _, base = self.coll
            for i in range(cnt):
                nm, f200, off, size, ls, lc = FILE.unpack_from(self.data, base + i * 0x230)
                self.files.append([nm.split(b'\0')[0].decode('cp932', 'replace'),
                                   bytearray(self.data[base + off:base + off + size]),
                                   f200, ls, lc])

    def replace(self, name, new_data):
        for f in self.files:
            if f[0].lower() == name.lower():
                f[1] = bytearray(new_data)
                return True
        return False

    def build(self, align=1):
        """Rebuild the container, keeping section order and link data."""
        base = self.coll[3]
        hdr_len = len(self.files) * 0x230
        blob, entries, pos = bytearray(), [], hdr_len
        for name, data, f200, ls, lc in self.files:
            while pos % align:
                blob.append(0); pos += 1
            entries.append((name, f200, pos, len(data), ls, lc))
            blob.extend(data); pos += len(data)
        while pos % align:
            blob.append(0); pos += 1
        table = bytearray()
        for name, f200, off, size, ls, lc in entries:
            table.extend(FILE.pack(name.encode('cp932').ljust(512, b'\0')[:512],
                                   f200, off, size, ls, lc))
        coll_payload = bytes(table + blob)
        out = bytearray(self.data[:base])
        out.extend(coll_payload)
        # sections after FILE_COLLECTION keep their relative order; shift their offsets
        delta = len(coll_payload) - self.coll[2]
        for s in self.sections:
            if s[0] == 'FILE_COLLECTION':
                s[2] = len(coll_payload)
            elif s[3] > base:
                old = self.data[s[3]:s[3] + s[2]]
                s[3] += delta
                out.extend(old)
        so = struct.unpack_from('<I', self.data, 0x10)[0]
        for i, (name, cnt, size, off) in enumerate(self.sections):
            SECT.pack_into(out, so + i * 0x50, name.encode().ljust(32, b'\0'), cnt, size, off)
        return bytes(out)
