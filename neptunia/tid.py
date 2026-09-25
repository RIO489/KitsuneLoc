"""TID — текстура Neptunia Re;Birth1.

Заголовок 0x80 Б: 'TID' + прапорці (0x90 / 0x92 — сирі 32 біт на піксель,
0x94 — DXT), u32 розмір файлу @0x04, u32 0x80 @0x08, ім'я[32] @0x20,
u32 ширина @0x44, u32 висота @0x48, u32 біт на піксель @0x4c,
u32 розмір даних @0x58, u32 зсув даних @0x5c; для DXT — fourcc @0x64
('DXT1' / 'DXT5'). Пікселі сирих текстур — RGBA (прапорець 0x02 — BGRA?
див. FLAG_BGRA; визначено за виглядом у грі).

Запис: сирі пікселі переписуємо як є; DXT перекодовуємо ЛИШЕ блоки 4×4, що
перетинають змінені прямокутники, — решта байтів лишається оригінальною.
"""
import struct
from PIL import Image

HDR = 0x80


class Tid:
    def __init__(self, data):
        self.data = bytearray(data)
        if self.data[:3] != b'TID':
            raise ValueError('не TID')
        self.flags = self.data[3]
        self.w, self.h = struct.unpack_from('<II', self.data, 0x44)
        self.bpp = struct.unpack_from('<I', self.data, 0x4c)[0]
        self.size, self.off = struct.unpack_from('<II', self.data, 0x58)
        self.fourcc = bytes(self.data[0x64:0x68]) if self.flags & 0x04 else b''

    @property
    def kind(self):
        return self.fourcc.decode() if self.fourcc else f'raw{self.bpp}'

    def image(self):
        px = bytes(self.data[self.off:self.off + self.size])
        if self.fourcc in (b'DXT1', b'DXT5'):
            import texture2ddecoder as t2d
            fn = t2d.decode_bc1 if self.fourcc == b'DXT1' else t2d.decode_bc3
            raw = fn(px, self.w, self.h)
            return Image.frombytes('RGBA', (self.w, self.h), raw, 'raw', 'BGRA')
        if self.bpp != 32:
            raise ValueError(f'формат {self.kind} не підтримується')
        mode = 'BGRA' if self.flags & 0x02 else 'RGBA'
        return Image.frombytes('RGBA', (self.w, self.h), px, 'raw', mode)

    def patch(self, img, rects):
        """Нові байти TID: img — вся текстура (RGBA), rects — змінені місця."""
        out = bytearray(self.data)
        if not self.fourcc:
            mode = 'BGRA' if self.flags & 0x02 else 'RGBA'
            out[self.off:self.off + self.size] = img.tobytes('raw', mode)
            return bytes(out)
        import etcpak
        bs = 8 if self.fourcc == b'DXT1' else 16
        bw = (self.w + 3) // 4
        bgra = img.tobytes('raw', 'BGRA')
        for x0, y0, x1, y1 in rects:
            bx0, by0 = max(0, x0 // 4), max(0, y0 // 4)
            bx1, by1 = min(bw, (x1 + 3) // 4), min((self.h + 3) // 4, (y1 + 3) // 4)
            for by in range(by0, by1):
                # рядок блоків цієї смуги — одним викликом кодера
                cx0, cx1 = bx0 * 4, bx1 * 4
                strip = bytearray()
                for yy in range(by * 4, by * 4 + 4):
                    row = bgra[(yy * self.w + cx0) * 4:(yy * self.w + cx1) * 4]
                    strip += row
                enc = (etcpak.compress_bc1(bytes(strip), cx1 - cx0, 4) if bs == 8
                       else etcpak.compress_bc3(bytes(strip), cx1 - cx0, 4))
                p = self.off + (by * bw + bx0) * bs
                out[p:p + len(enc)] = enc
        return bytes(out)
