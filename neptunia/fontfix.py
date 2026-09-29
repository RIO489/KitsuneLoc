"""Шрифти Neptunia Re;Birth1 (sysfont / msgfont / advfont) — профіль для спільного
compileheart/fontfix.py (алгоритм один на всі ігри рушія).

Особливості цієї гри:
  - шрифт у Shift-JIS; двобайтову кирилицю рушій малює шириною ієрогліфа → кожна літера
    пишеться одним байтом (neptunia/chars.py: 0xA1–0xDF замість півширинної катакани і
    новий діапазон 0xFD–0xFF) — ByteScheme;
  - відступи — медіани латиниці шрифту, нахил курсиву — автоматично (msgfont похилий);
  - ї/Ї — основа i + крапки ё, вусик ґ/Ґ — 0.28 висоти;
  - « » кутиками в нові однобайтові коди (chars.QUOTES), ♡ у слот ∵ (у рамці ☆);
  - старі сейви (стара схема Crowdin/stcm-editor) тримають кирилицю двобайтовою, а і ї є ґ —
    грецькими α β δ γ: там теж кладемо охайні літери (OLD_SUBST).
Результат побайтово той самий, що до злиття (перевірено на всіх трьох шрифтах і курсивному
sysfont).
"""
from compileheart import fontfix as _fix
from . import chars

OLD_SUBST = chars.OLD_SUBST
HEART = 0x81E6     # ∵ — перекладач пише його замість ♡ (у старій схемі тут було сердечко)
STAR = 0x8199      # ☆ — зразок розміру, товщини й положення значка в цьому шрифті

SCHEME = chars.SCHEME      # одна схема на гру: і шрифт, і текст
PROFILE = {'quotes': chars.QUOTES, 'heart': (HEART, STAR), 'new_ranges': [(0xFD, 0x100)]}

italic_sysfont = _fix.italic_sysfont


def fix(data):
    """Оригінальний .ffu -> (новий .ffu, звіт)."""
    return _fix.fix(data, SCHEME, PROFILE)
