# -*- coding: utf-8 -*-
"""Бібліотека шрифтів для написів на картинках.

Шрифти стилів лежать у атлас/шрифти/, бібліотека для порівняння — у
атлас/шрифти/кандидати/ (Google Fonts з і ї є ґ, див. завантажити.py).
Вікно «Глянути різні шрифти» малює той самий напис кожним із них; коли
шрифт кандидата записують у розмітку, він копіюється в атлас/шрифти/.
"""
import os, shutil

from PIL import ImageFont

from . import atlas as atl

CAND_DIR = os.path.join(atl.FONTS, 'кандидати')


def font_files():
    """[(тека, файл)]: спершу шрифти стилів, далі кандидати (без повторів)."""
    out, seen = [], set()
    for d in (atl.FONTS, CAND_DIR):
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d), key=str.lower):
            if f.lower().endswith(('.ttf', '.otf')) and f not in seen:
                seen.add(f)
                out.append((d, f))
    return out


def axes(path):
    """{'Weight': {...}, 'Width': {...}} — осі варіативного шрифту ({} для статичного)."""
    try:
        f = ImageFont.truetype(path, 20)
        return {a['name'].decode() if isinstance(a['name'], bytes) else a['name']: a
                for a in f.get_variation_axes()}
    except Exception:
        return {}


def variation(path, weight):
    """Варіація для товщини `weight` (у межах осі шрифту) або None для статичного."""
    ax = axes(path)
    if 'Weight' not in ax:
        return None
    a = ax['Weight']
    v = {'wght': int(min(a['maximum'], max(a['minimum'], weight)))}
    if 'Width' in ax:
        v['wdth'] = ax['Width']['default']
    return v


def register(d, fn):
    """Щоб atlas._font знайшов шрифт кандидата, не копіюючи файл."""
    if d != atl.FONTS:
        atl.EXTRA_FONT_DIRS[fn] = d


def adopt(fn):
    """Шрифт, записаний у розмітку, — у атлас/шрифти/ (щоб поїхав до перекладача)."""
    src = os.path.join(CAND_DIR, fn)
    dst = os.path.join(atl.FONTS, fn)
    if not os.path.exists(dst) and os.path.exists(src):
        shutil.copy2(src, dst)
