# -*- coding: utf-8 -*-
"""TMP-шрифт гри для прев'ю й ширин тексту (рівень рушія Unity, з 2.9).

Беремо TMP_FontAsset з оригінального бандла (backup), проганяємо через той самий
unity/fontfix, що й «2» (кирилиця з відступами латиниці, і ї є ґ), і читаємо:
  - ширини символів (xAdvance, px атласу при PointSize шрифту) — для перевірки довжини;
  - гліфи: поле відстані з атласу -> маска літери (край — 0.5 поля).
Інтерфейс гліфів — як у preview.GameFont: glyph(символ) -> (xadv, маска L) | None,
clean(рядок), cell_h. TMP переносить слова сам (wraps = True).
Шрифт — з кирилицею й найбільшою кількістю гліфів (Crystar: Stella-FOT_ja).
"""
import math, re

import numpy as np
from PIL import Image

TAG = re.compile(r'<[^<>]*>')          # теги тексту гри (<CHARA=…>, <#ffffff>, </color>) — не малюються


def load(bundle_path):
    """(TmpFont після fontfix, атлас float H×W, y згори) — шрифт з кирилицею з найбільшою
    кількістю гліфів у бандлі, або None."""
    import UnityPy
    from UnityPy.enums import TextureFormat
    from .tmpfont import TmpFont, is_tmp_font
    from . import fontfix
    env = UnityPy.load(bundle_path)
    byid = {o.path_id: o for o in env.objects}
    best = None
    for o in env.objects:
        if o.type.name != 'MonoBehaviour' or not is_tmp_font(o):
            continue
        try:
            f = TmpFont(o.get_raw_data())
        except ValueError:
            continue
        if not any(0x410 <= g['id'] <= 0x44F for g in f.glyphs) or f.atlas_id not in byid:
            continue
        if best is None or len(f.glyphs) > len(best[0].glyphs):
            best = (f, byid[f.atlas_id])
    if best is None:
        return None
    f, tex_obj = best
    tex = tex_obj.read()
    if tex.m_TextureFormat != TextureFormat.Alpha8:
        return None
    atlas = np.asarray(tex.image.getchannel('A'), np.float32) / 255
    res = fontfix.fix(f.raw, atlas)
    if res is None:
        return f, atlas
    raw, new_atlas, _notes = res
    return TmpFont(raw), (atlas if new_atlas is None else new_atlas)


def widths(font):
    """{символ: xAdvance} (px атласу при PointSize)."""
    return {chr(g['id']): round(g['adv'], 2) for g in font.glyphs if 0 < g['id'] < 0x110000}


class TmpGameFont:
    wraps = True                                     # TMP сам переносить слова за шириною поля

    def __init__(self, font, atlas):
        self.f, self.atlas = font, atlas
        self.by = font.by_char()
        self.pad = int(font.face['Padding'])
        self.asc = font.face['Ascender']
        self.cell_h = int(math.ceil(self.asc - font.face['Descender'])) + 6
        self.cache = {}

    def clean(self, line):
        return TAG.sub('', line)

    def glyph(self, ch):
        if ch in self.cache:
            return self.cache[ch]
        g = self.by.get(ord(ch))
        got = None
        if g is not None:
            adv = g['adv']
            pad = self.pad
            x0, y0 = int(g['x']) - pad, int(g['y']) - pad
            x1, y1 = int(math.ceil(g['x'] + g['w'])) + pad, int(math.ceil(g['y'] + g['h'])) + pad
            H, W = self.atlas.shape
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
            field = self.atlas[y0:y1, x0:x1]
            # край — 0.5 поля; поле змінюється на 0.5 за Padding px -> рампа ~1 px
            alpha = np.clip((field - 0.5) * pad + 0.5, 0, 1)
            a8 = np.round(alpha * 255).astype(np.uint8)
            ys, xs = np.nonzero(a8)
            if not len(xs):                          # пробіл
                self.cache[ch] = got = (adv, Image.new('L', (max(1, int(adv)), self.cell_h)))
                return got
            # лише чорнило (поле навколо — порожнє): тоді лівий край майже не виходить за перо
            a8 = a8[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
            m = Image.fromarray(a8, 'L')
            # у клітинці рядка: базова лінія на висоті ascender + 3 від верху
            left = g['xo'] - (g['x'] - x0) + xs.min()
            top = (self.asc + 3) - g['yo'] - (g['y'] - y0) + ys.min()
            cw = int(math.ceil(max(adv, left + m.width))) + 1
            cell = Image.new('L', (max(1, cw), self.cell_h))
            ox, oy = int(round(left)), int(round(top))
            cell.paste(m, (ox, oy))
            got = (adv, cell)
        self.cache[ch] = got
        return got
