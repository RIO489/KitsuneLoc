# -*- coding: utf-8 -*-
"""Прев'ю тексту «як у грі»: гліфи справжнього шрифту гри (FFU після fontfix —
той самий, що заливається в гру), межа ширини й кількість рядків — з
оригіналів (sheets.width_limit, як у перевірці).

Рамку вікна намальовано (текстури вікна діалогу ще не знайдено), але
літери, їхні відступи, перенос і те, що не влазить, — як у грі: що вилізло
за межу або зайвий рядок — червоним.
"""
import os, re

from PIL import Image, ImageDraw

import metrics

PAD = 22
NAME_H = 0.9                         # висота таблички з ім'ям відносно рядка
TEXT = (255, 255, 255)
OVER = (255, 90, 90)
MISS = (255, 200, 0)
BOX = (18, 8, 20)
EDGE = (224, 40, 140)
NAMEBOX = (70, 12, 48)
LIMIT = (120, 120, 140)


class GameFont:
    """Гліфи шрифту діалогів гри: glyph(символ) -> (xadv, маска L) або None."""

    def __init__(self, game, backup_dir, font='msg'):
        self.game = game
        if game == 'msk':
            from maryskelter.bra import Bra
            from maryskelter import fontfix, ffu, chars
            path = os.path.join(backup_dir, metrics.MSK_FONT[0])
            data, _ = fontfix.fix(Bra.read_some(path, [metrics.MSK_FONT[1]])[metrics.MSK_FONT[1]])
            self.f = ffu.Ffu(data)
            self._code = lambda ch: chars.SUBST.get(ch, ch)     # і ї є ґ — у слотах ì ò ù ã
            self._get = lambda c: self.f.glyph(c)
            self.code_re = metrics._MSK_CODE
        elif game == 'nep':
            from neptunia.pac import Pac
            from neptunia import fontfix, ffu, chars
            path = os.path.join(backup_dir, *metrics.NEP_FONT[0].split('/'))
            data, _ = fontfix.fix(Pac(path).read(metrics.NEP_FONTS[font]))
            self.f = ffu.Ffu(data)

            def code(ch):
                try:
                    raw = chars.encode(ch)
                except Exception:
                    return None
                return raw[0] if len(raw) == 1 else int.from_bytes(raw, 'big')
            self._code = code
            self._get = lambda c: None if c is None else self.f.glyph(c)
            self.code_re = metrics._NEP_CODE
        else:
            raise ValueError('шрифт цієї гри не розібрано')
        self.cell_h = self.f.cell_h
        self.cache = {}

    def glyph(self, ch):
        if ch in self.cache:
            return self.cache[ch]
        got = None
        try:
            g = self._get(self._code(ch))
        except Exception:
            g = None
        if g:
            xadv, rows = g
            h, w = len(rows), max((len(r) for r in rows), default=0)
            m = Image.new('L', (max(1, w), max(1, h)))
            if w and h:
                m.putdata([min(255, v * 17) for r in rows for v in (r + [0] * (w - len(r)))])
            got = (xadv, m)
        self.cache[ch] = got
        return got

    def clean(self, line):
        return self.code_re.sub('', line)


def render(font, text, limit, max_lines=None, name=None, dialog=False):
    """Картинка напису. limit — межа ширини в px (None — без межі).
    Повертає (Image RGB, [попередження, видимі на картинці])."""
    lines = (text or '').split('\n')
    lh = font.cell_h + 4
    width = max([limit or 0] + [sum((font.glyph(c) or (font.cell_h // 2, None))[0]
                                    for c in font.clean(l)) for l in lines])
    W = int(width + 2 * PAD + 8)
    top = int(lh * NAME_H) + 8 if (dialog and name) else 0
    box_h = (max_lines or len(lines)) * lh + 2 * PAD
    H = top + box_h + max(0, len(lines) - (max_lines or len(lines))) * lh + 6
    im = Image.new('RGB', (W, H), (40, 40, 48))
    d = ImageDraw.Draw(im)
    d.rectangle([2, top, W - 3, top + box_h - 1], fill=BOX, outline=EDGE, width=3)
    if dialog and name:
        nw = sum((font.glyph(c) or (font.cell_h // 2, None))[0] for c in font.clean(name)) + 28
        d.rectangle([10, 0, 10 + nw, top + 4], fill=NAMEBOX, outline=EDGE, width=2)
        _line(im, font, name, 24, 2, None, TEXT)
    if limit:
        x = PAD + limit
        for y in range(top + 4, top + box_h - 4, 8):
            d.line([(x, y), (x, y + 4)], fill=LIMIT, width=1)
    notes = []
    for i, l in enumerate(lines):
        extra = max_lines is not None and i >= max_lines
        y = top + box_h + (i - max_lines) * lh + 2 if extra else top + PAD + i * lh
        over, miss = _line(im, font, l, PAD, y, None if extra else limit, OVER if extra else TEXT)
        if over and not extra:
            notes.append(f'рядок {i + 1} не влазить у межу')
        if miss:
            notes.append('немає в шрифті гри: ' + ' '.join(sorted(miss)))
    if max_lines is not None and len(lines) > max_lines:
        notes.append(f'рядків {len(lines)}, місця — на {max_lines}')
    return im, notes


def _line(im, font, line, x, y, limit, color):
    """Намалювати рядок; повертає (чи вилізло за межу, {символи без гліфа})."""
    over, miss = False, set()
    x0 = x
    for ch in font.clean(line):
        g = font.glyph(ch)
        if g is None:
            if ch != ' ':
                miss.add(ch)
                ImageDraw.Draw(im).rectangle([x + 2, y + 4, x + font.cell_h // 2 - 2, y + font.cell_h - 4],
                                             outline=MISS, width=2)
            x += font.cell_h // 2
            continue
        xadv, m = g
        col = color
        if limit is not None and x + xadv - x0 > limit + 0.5:
            col, over = OVER, True
        im.paste(col, (int(x), int(y)), m)
        x += xadv
    return over, miss


def fit(im, max_w):
    """Зменшити під ширину панелі (гліфи гри великі: клітинка ~40 px)."""
    if im.width <= max_w:
        return im
    k = max_w / im.width
    return im.resize((max_w, max(1, round(im.height * k))), Image.LANCZOS)
