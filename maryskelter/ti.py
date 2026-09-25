# -*- coding: utf-8 -*-
"""TI (TIL0001) — нарізка атласу інтерфейсу Mary Skelter: Nightmares.

Лежить у `TTM1.bra\\common\\common.CL3` разом із `common.tid` (звичайна DDS).
Формат розібраний і перевірений: усі 311 кадрів лягають рівно на спрайти.

Структура
---------
0x00  'TIL0001\\0'
0x10  u32 off_texnames   (0x128)
0x14  u32 n_textures     (1)
0x18  u32 off_frames     (0x2C04)
0x1C  u32 n_frames       (311)
0x20  u32 off_groups     (0x434)
0x24  u32 n_groups       (49)
0x28  f32 0.5

Група (0xD0 = 208 байтів): name[64] у cp932, далі поля (не чіпаємо).
Кадр  (0x94 = 148 байтів):
  +0x08  4 x (i32 x, i32 y)  — чотирикутник у пікселях (подвоєних)
  +0x28  4 x (f32 u, f32 v)  — ті самі кути в UV; саме їх бере движок
  далі масштаб/зсув, не чіпаємо.

Ми координати не міняємо — тільки читаємо, щоб знати, куди малювати текст.
"""
import struct

HDR = struct.Struct('<I I I I I I f')      # від 0x10
GROUP = 0xD0
FRAME = 0x94


class Ti:
    def __init__(self, data):
        self.data = bytes(data)
        if self.data[:7] != b'TIL0001':
            raise ValueError('не TI (TIL0001)')
        (self.off_names, self.n_tex, self.off_frames, self.n_frames,
         self.off_groups, self.n_groups, self.half) = HDR.unpack_from(self.data, 0x10)

    def groups(self):
        """[(індекс, назва)] — назви в cp932, часто японські."""
        out = []
        for i in range(self.n_groups):
            o = self.off_groups + i * GROUP
            nm = self.data[o:o + 64].split(b'\0')[0].decode('cp932', 'replace')
            out.append((i, nm))
        return out

    def frames(self, width=2048, height=2048):
        """[(індекс, x0, y0, x1, y1)] у пікселях атласу, з UV."""
        out = []
        for i in range(self.n_frames):
            o = self.off_frames + i * FRAME
            uv = struct.unpack_from('<8f', self.data, o + 0x28)
            us, vs = uv[0::2], uv[1::2]
            if not all(0.0 <= v <= 1.0 for v in uv):
                continue
            x0, x1 = min(us) * width, max(us) * width
            y0, y1 = min(vs) * height, max(vs) * height
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            out.append((i, round(x0), round(y0), round(x1), round(y1)))
        return out
