# -*- coding: utf-8 -*-
"""Профіль Hyperdimension Neptunia Re;Birth1 для спільного коду рушія Compile Heart — лише дані.
(Реєстр профілів — compileheart/profiles.py.)"""
import re

from . import chars, fontfix

SCHEME = chars.SCHEME                 # однобайтова кирилиця 0xA1–0xDF, 0xFD–0xFF (Shift-JIS)
fix = fontfix.fix

FONT_ARCHIVE = 'data/SYSTEM00000.pac'
# головне вікно діалогу малює advfont (ширший), історія діалогів — msgfont
FONTS = {'msg': 'window/font/msgfont.ffu', 'adv': 'window/font/advfont.ffu'}

# службові коди: #FontColor[%u] / #FontColorB, %s %04u %llu %%, <BLANK>
# (перенос рядка в книзі — звичайний Enter, у файлах гри це #n або справжній \n)
CODES = re.compile(r'#[A-Za-z]+(?:\[[^\]]*\])?|%[-+0#]*\d*(?:\.\d+)?(?:ll|l|h)?[a-zA-Z%]|<[A-Z]+>')

# ширина вікон, виміряна в грі (2026-09-26): тестові рядки, де гра обрізає текст (п'ять рядків
# на вікно дали ту саму межу ±4 px). Гра не переносить — обрізає.
SCREENS = [
    {'id': 'adv', 'назва': 'головне вікно діалогу', 'font': 'adv', 'px': 790},   # видно 787, обрізано з 794
    {'id': 'msg', 'назва': 'вікно історії діалогів', 'font': 'msg', 'px': 726},  # видно 723, обрізано з 730
]
