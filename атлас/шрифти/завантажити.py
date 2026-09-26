#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Бібліотека шрифтів-кандидатів для «Глянути різні шрифти» (вікно написів).

Завантажує шрифти з github.com/google/fonts (ліцензія OFL) у теку
`кандидати/` поруч і лишає лише ті, де є українські літери і ї є ґ.

  python атлас/шрифти/завантажити.py            усі з переліку FAMILIES
  python атлас/шрифти/завантажити.py rubik jost  лише названі теки ofl/<назва>

Декоративні шрифти (Oi, Lobster, Stalinist One, Press Start 2P…) сюди свідомо
не входять: на написах інтерфейсу вони не доречні.
"""
import json, os, sys, urllib.request

from PIL import ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(HERE, 'кандидати')
API = 'https://api.github.com/repos/google/fonts/contents/ofl/{}'

FAMILIES = [
    'montserrat', 'rubik', 'play', 'jura', 'robotocondensed', 'firasans', 'firasansextracondensed',
    'daysone', 'unbounded', 'onest', 'geologica', 'manrope', 'raleway', 'nunito', 'sofiasans',
    'sofiasanscondensed', 'sofiasansextracondensed', 'golostext', 'commissioner', 'delagothicone',
    'scada', 'yanonekaffeesatz', 'mplus1p', 'mplusrounded1c', 'notosansdisplay', 'opensans',
    'ptsans', 'mulish', 'wixmadefordisplay',
]
STATIC = ('-Regular', '-Bold', '-Black', '-Italic', '-BoldItalic', '-BlackItalic')


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'KitsuneLoc'})
    return urllib.request.urlopen(req, timeout=60).read()


def has_ukrainian(path):
    """Чи є в шрифті і ї є ґ (а не порожній квадрат .notdef)."""
    f = ImageFont.truetype(path, 40)
    notdef = bytes(f.getmask('￿'))
    for ch in 'іїєґЖЩ':
        m = f.getmask(ch)
        if not m.getbbox() or bytes(m) == notdef:
            return False
    return True


def fetch(family):
    files = [x for x in json.loads(get(API.format(family))) if x['name'].lower().endswith('.ttf')]
    # варіативні файли (з [осями]) покривають усі товщини; інакше — основні статичні
    pick = [x for x in files if '[' in x['name']] or \
        [x for x in files if any(w in x['name'] for w in STATIC)] or files[:1]
    for x in pick:
        p = os.path.join(DST, x['name'])
        if os.path.exists(p):
            continue
        open(p, 'wb').write(get(x['download_url']))
        ok = has_ukrainian(p)
        if not ok:
            os.remove(p)
        print('+' if ok else '- (без і ї є ґ)', x['name'])


def main():
    os.makedirs(DST, exist_ok=True)
    for fam in sys.argv[1:] or FAMILIES:
        try:
            fetch(fam)
        except Exception as ex:                                       # noqa: BLE001
            print('!', fam, ex)


if __name__ == '__main__':
    main()
