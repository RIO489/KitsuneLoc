# -*- coding: utf-8 -*-
"""Реєстр профілів ігор рушія Compile Heart: ключ гри -> модуль-профіль (лише дані).

Профіль дає: SCHEME (схема символів, scheme.py), fix (шрифт, fontfix.py з профілем),
FONT_ARCHIVE і FONTS (де лежать шрифти: архів за магією — archive.py), CODES (службові
коди тексту), SCREENS (вікна діалогу: id, назва, шрифт, px — виміряна межа).
Увесь код поверх (прев'ю, ширини, перевірка перекладу) — один для всіх ігор рушія.
Нова гра рушія = новий профіль (або, для незнайомої, — scheme.auto + fontfix.DEFAULT).
"""
import importlib

PROFILES = {'msk': 'maryskelter.profile', 'nep': 'neptunia.profile'}


def get(game):
    """Модуль-профіль гри або None (гра не на цьому рушії чи профілю ще немає)."""
    mod = PROFILES.get(game)
    return importlib.import_module(mod) if mod else None
