# -*- coding: utf-8 -*-
"""Налаштування розкладки тексту TextMeshPro (TMP 1.x) у сирих даних компонента (з 2.12).

Type tree в IL2CPP немає. Поля TMP_Text після m_text (unity/tmptext.text_field) ідуть
у порядку серіалізації TMP 1.3 (перевірено на всіх написах Crystar: вирівнювання, кегль,
перенос мають осмислені значення):
  m_isRightToLeft b4, m_fontAsset P, m_sharedMaterial P, m_fontSharedMaterials [P],
  m_fontMaterial P, m_fontMaterials [P], m_fontColor32 4, m_fontColor 16,
  m_enableVertexGradient b4, m_fontColorGradient 64, m_fontColorGradientPreset P,
  m_spriteAsset P, m_tintAllSprites b4, m_overrideHtmlColors b4, m_faceColor 4,
  m_outlineColor 4, m_fontSize f, m_fontSizeBase f, m_fontWeight i, m_enableAutoSizing b4,
  m_fontSizeMin f, m_fontSizeMax f, m_fontStyle i, m_textAlignment i,
  m_isAlignmentEnumConverted b4, m_characterSpacing, m_wordSpacing, m_lineSpacing,
  m_lineSpacingMax, m_paragraphSpacing, m_charWidthMaxAdj (f), m_enableWordWrapping b4,
  m_wordWrappingRatios f, m_overflowMode i, …
(P — PPtr: i32 fileID + i64 pathID = 12 Б; [P] — i32 кількість + 12 Б на елемент;
b4 — bool з вирівнюванням до 4.)

Автопідбір кегля (m_enableAutoSizing) — вбудований механізм TMP: текст, що влазить, лишається
кеглем m_fontSizeMax; що не влазить — зменшується до m_fontSizeMin. Так переклад, довший за
оригінал, не переноситься і не зникає з кнопки, а англійське/японське не змінюється.
"""
import struct

from . import tmptext

ALIGN = {257, 258, 260, 264, 272, 513, 514, 516, 520, 528, 1025, 1026, 1028, 1032, 1040,
         2049, 2050, 2052, 2056, 2064, 4097, 4098, 4100, 4104, 4112, 8193, 8194, 8196, 8200, 8208}


def fields(raw):
    """{назва: (зсув, формат)} полів розкладки або None (не TMP 1.x / інша будова)."""
    f = tmptext.text_field(raw)
    if f is None:
        return None
    p, ln, _t = f
    q = p + 4 + ((ln + 3) & ~3)
    try:
        q += 4 + 12 + 12                               # rtl, fontAsset, sharedMaterial
        n = struct.unpack_from('<i', raw, q)[0]
        if not 0 <= n < 64:
            return None
        q += 4 + 12 * n + 12                           # fontSharedMaterials, fontMaterial
        n = struct.unpack_from('<i', raw, q)[0]
        if not 0 <= n < 64:
            return None
        q += 4 + 12 * n                                # fontMaterials
        q += 4 + 16 + 4 + 64 + 12 + 12 + 4 + 4 + 4 + 4  # кольори, градієнт, спрайти, faceColor, outline
        out = {}
        for name, fmt in (('fontSize', 'f'), ('fontSizeBase', 'f'), ('fontWeight', 'i'),
                          ('autoSize', 'i'), ('sizeMin', 'f'), ('sizeMax', 'f'), ('fontStyle', 'i'),
                          ('align', 'i'), ('alignConverted', 'i'), ('charSpacing', 'f'),
                          ('wordSpacing', 'f'), ('lineSpacing', 'f'), ('lineSpacingMax', 'f'),
                          ('paragraphSpacing', 'f'), ('charWidthMaxAdj', 'f'), ('wrap', 'i'),
                          ('wrapRatio', 'f'), ('overflow', 'i')):
            out[name] = (q, fmt)
            q += 4
        if q > len(raw):
            return None
        v = read(raw, out)
        if (v['align'] not in ALIGN or v['autoSize'] not in (0, 1) or v['wrap'] not in (0, 1)
                or not 0 < v['fontSize'] < 5000 or not 0 <= v['overflow'] <= 7):
            return None
        return out
    except struct.error:
        return None


def read(raw, flds):
    return {k: struct.unpack_from('<' + fmt, raw, off)[0] for k, (off, fmt) in flds.items()}


def write(raw, flds, **vals):
    """Ті самі дані з іншими значеннями полів (довжина не змінюється)."""
    b = bytearray(raw)
    for k, v in vals.items():
        off, fmt = flds[k]
        struct.pack_into('<' + fmt, b, off, int(v) if fmt == 'i' else float(v))
    return bytes(b)


DEFAULT_MIN = 0.7          # частка кегля, до якої автопідбір може зменшити текст


def is_ours(raw):
    """Автопідбір, увімкнений нашим імпортом (min = DEFAULT_MIN × max) — ознака перекладеного
    бандла; автопідбір з іншими межами може бути й в оригіналі іншої гри."""
    flds = fields(raw)
    if flds is None:
        return False
    v = read(raw, flds)
    return bool(v['autoSize']) and abs(v['sizeMin'] - round(v['sizeMax'] * DEFAULT_MIN, 1)) < 0.01


def autosize(raw, min_ratio=DEFAULT_MIN):
    """Увімкнути автопідбір кегля: max — нинішній кегль, min — min_ratio від нього (якщо в
    оригіналі вже ввімкнено — лишаємо, лише не даємо max вище нинішнього). None — не TMP 1.x."""
    flds = fields(raw)
    if flds is None:
        return None
    v = read(raw, flds)
    size = v['sizeMax'] if v['autoSize'] else v['fontSize']
    if v['autoSize']:
        return raw
    return write(raw, flds, autoSize=1, sizeMax=size, sizeMin=round(size * min_ratio, 1))
