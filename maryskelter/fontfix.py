# -*- coding: utf-8 -*-
"""Fixes Cyrillic in Mary Skelter Nightmares fonts (msgfont.ffu / sysfont.ffu).

1. Cyrillic glyphs are stored in full-width (40 px) cells with a fixed
   advance of 40, so Ukrainian/Russian text renders with huge gaps.
   We crop every Cyrillic glyph to its ink, leave a 1 px left bearing and
   set xadv = 1 + ink_width + 2, which matches the Latin metrics.
2. і І ї Ї є Є ґ Ґ have no glyphs at all and the game silently drops them.
   Adding new range/char entries to the font did NOT work (the engine
   ignores them), so instead we overwrite eight Latin-1 letters that no
   Ukrainian or English line in the game ever uses — see SLOT below.
   maryskelter/chars.py does the matching substitution on import.
"""
from .ffu import Ffu

LB = 1     # left bearing, px
RB = 2     # right side bearing, px

CYR = [chr(c) for c in range(0x400, 0x460)] + ['ґ', 'Ґ']

# українська літера -> латинський слот, у який ми малюємо її гліф
SLOT = {'і': 'ì', 'І': 'Ì', 'ї': 'ò', 'Ї': 'Ò',
        'є': 'ù', 'Є': 'Ù', 'ґ': 'ã', 'Ґ': 'Ã'}


def _crop(rows, lb=LB, rb=RB):
    bb = Ffu.bbox(rows)
    if not bb:
        return None, None
    L, R, T, B = bb
    w = R - L + 1
    out = []
    for r in rows:
        out.append([0] * lb + r[L:R + 1] + [0] * rb)
    return out, lb + w + rb


def _mirror(rows):
    return [list(reversed(r)) for r in rows]


def _tick(rows, thick=5, high=9):
    """Add the upper-right stroke that turns Г/г into Ґ/ґ."""
    bb = Ffu.bbox(rows)
    L, R, T, B = bb
    rows = [list(r) for r in rows]
    x0 = max(L, R - thick + 1)
    for y in range(max(0, T - high), T):
        for x in range(x0, R + 1):
            rows[y][x] = 15
    return rows


def fix(data):
    f = Ffu(data)
    f.begin()
    src = {}
    for ch in CYR + ['i', 'I', 'ï', 'Ï']:
        g = f.glyph(ch)
        if g:
            src[ch] = g
    report = {'tightened': 0, 'added': []}

    # 1. tighten existing Cyrillic
    for ch in CYR:
        if ch not in src:
            continue
        xadv, rows = src[ch]
        new, adv = _crop(rows)
        if new is None:
            continue
        f.set_glyph(ch, new, adv)
        report['tightened'] += 1

    # 2. build the missing Ukrainian letters
    def add(slot, rows, adv, shown):
        f.set_glyph(slot, rows, adv)
        report['added'].append(shown)

    for ch, base in (('і', 'i'), ('І', 'I'), ('ї', 'ï'), ('Ї', 'Ï'),
                     ('є', 'э'), ('Є', 'Э'), ('ґ', 'г'), ('Ґ', 'Г')):
        slot = SLOT[ch]
        g = f.glyph(base) if base not in src else src[base]
        if not g:
            continue
        xadv, rows = g
        new, adv = _crop(rows)
        if ch in ('є', 'Є'):
            new = _mirror(new)
        elif ch in ('ґ', 'Ґ'):
            new = _tick(new)
        add(slot, new, adv, ch)

    return f.build(), report
