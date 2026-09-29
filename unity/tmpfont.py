# -*- coding: utf-8 -*-
"""TextMeshPro 1.x: TMP_FontAsset у сирих даних MonoBehaviour (IL2CPP — type tree немає).

Будова (перевірено на Crystar, TMP 1.x, `m_glyphInfoList`):
  заголовок MonoBehaviour (m_GameObject, m_Enabled, m_Script, m_Name)
  hashCode i32, material PPtr (i32 + i64), materialHashCode i32, fontAssetType i32
  m_fontInfo: Name (рядок) + 20 полів (FACE)
  atlas PPtr<Texture2D> (i32 + i64)
  m_glyphInfoList: i32 кількість + записи по 36 Б
      {i32 id (символ), f32 x, y, width, height (рамка чорнила в пікселях атласу, y — згори),
       f32 xOffset, yOffset (верх чорнила над базовою лінією), xAdvance, scale}
  хвіст (кернінг, запасні шрифти, налаштування) — переносимо як є.
Абсолютних зсувів у даних немає, тож записи можна додавати.
"""
import struct

FACE = ('PointSize', 'Scale', 'CharacterCount', 'LineHeight', 'Baseline', 'Ascender', 'CapHeight',
        'Descender', 'CenterLine', 'SuperscriptOffset', 'SubscriptOffset', 'SubSize', 'Underline',
        'UnderlineThickness', 'strikethrough', 'strikethroughThickness', 'TabWidth', 'Padding',
        'AtlasWidth', 'AtlasHeight')
_FACE_FMT = '<ffi17f'
GLYPH = ('id', 'x', 'y', 'w', 'h', 'xo', 'yo', 'adv', 'scale')
_GLYPH_FMT = '<i8f'


def _string(raw, p):
    n = struct.unpack_from('<i', raw, p)[0]
    return raw[p + 4:p + 4 + n].decode('utf-8'), p + 4 + ((n + 3) & ~3)


class TmpFont:
    def __init__(self, raw):
        self.raw = raw
        self.name, p = _string(raw, 28)
        p += 24                                      # hashCode, material, materialHashCode, fontAssetType
        self.face_name, p = _string(raw, p)
        self.face_off = p
        self.face = dict(zip(FACE, struct.unpack_from(_FACE_FMT, raw, p)))
        p += 80
        _fid, self.atlas_id = struct.unpack_from('<iq', raw, p)
        p += 12
        self.list_off = p
        n = struct.unpack_from('<i', raw, p)[0]
        if not 0 <= n <= 200000 or p + 4 + 36 * n > len(raw):
            raise ValueError(f'{self.name}: не TMP 1.x (m_glyphInfoList)')
        self.glyphs = [dict(zip(GLYPH, struct.unpack_from(_GLYPH_FMT, raw, p + 4 + 36 * k))) for k in range(n)]
        self.tail_off = p + 4 + 36 * n

    def by_char(self):
        return {g['id']: g for g in self.glyphs}

    def build(self):
        face = dict(self.face, CharacterCount=len(self.glyphs))
        out = bytearray(self.raw[:self.face_off])
        out += struct.pack(_FACE_FMT, *[face[k] for k in FACE])
        out += self.raw[self.face_off + 80:self.list_off]
        out += struct.pack('<i', len(self.glyphs))
        for g in self.glyphs:
            out += struct.pack(_GLYPH_FMT, int(g['id']), *[float(g[k]) for k in GLYPH[1:]])
        out += self.raw[self.tail_off:]
        return bytes(out)


def is_tmp_font(obj):
    """MonoBehaviour-об'єкт UnityPy — TMP_FontAsset?"""
    try:
        return obj.read().m_Script.read().m_ClassName == 'TMP_FontAsset'
    except Exception:
        return False
