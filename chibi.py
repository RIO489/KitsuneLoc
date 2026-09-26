# -*- coding: utf-8 -*-
"""Дівчата гри в правій частині журналу вікна.

Neptunia — чібі з карти (`GAME00000.pac/symbol/texture/NNN.tid`, лише дівчата),
Mary Skelter — фігури з галереї бонусів (`TTM3.bra/TEXTURE/bonus/Character`,
без Джека: справжніх чібі в MSK немає). Витягуються з оригіналів при кроці «1»
у кеш\\чібі\\<гра>\\ (картинки обрізані по прозорості й зменшені), далі вікно
показує випадкову при запуску й при виборі гри.
"""
import os, random

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, 'кеш', 'чібі')
MAX = (200, 250)                     # найбільший розмір картинки в журналі

# symbol/texture: дівчата (решта — хлопці, звірі, скрині, значки)
NEP_GIRLS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 18, 20, 24, 25, 31, 35, 44, 46, 50, 54, 55, 58, 60]


def _save(im, path):
    im = im.convert('RGBA')
    bb = im.getchannel('A').point(lambda v: 255 if v > 8 else 0).getbbox()
    if bb:
        im = im.crop(bb)
    im.thumbnail(MAX)
    im.save(path)


def _sources(game, orig_dir, game_dir):
    """[(ім'я файлу, функція -> PIL.Image)] з оригіналів гри."""
    def path(*rel):
        p = os.path.join(orig_dir, *rel)
        return p if os.path.exists(p) else os.path.join(game_dir, *rel)
    if game == 'nep':
        from neptunia.pac import Pac
        from neptunia.tid import Tid
        pac = Pac(path('data', 'GAME00000.pac'))
        out = []
        for n in NEP_GIRLS:
            e = pac.get(f'symbol/texture/{n:03d}.tid')
            if e is not None:
                out.append((f'{n:03d}.png', lambda e=e: Tid(pac.read(e)).image()))
        return out
    if game == 'msk':
        from maryskelter.bra import Bra
        from maryskelter import dds
        b = Bra(path('TTM3.bra'))
        names = [e.name for e in b.entries
                 if e.name.lower().startswith('texture\\bonus\\character\\')
                 and not os.path.basename(e.name).startswith('00')]      # 00xx — Джек
        return [(os.path.basename(n).rsplit('.', 1)[0] + '.png', lambda n=n: dds.decode(b.read(n)))
                for n in names]
    return []


def ensure(game, orig_dir, game_dir, progress=None):
    """Витягти картинки, яких ще немає. Повертає к-сть нових."""
    if game not in ('nep', 'msk'):
        return 0
    dest = os.path.join(DIR, game)
    os.makedirs(dest, exist_ok=True)
    have = set(os.listdir(dest))
    todo = [(fn, get) for fn, get in _sources(game, orig_dir, game_dir) if fn not in have]
    for k, (fn, get) in enumerate(todo):
        if progress:
            progress(k + 1, len(todo), 'Дівчата для журналу')
        try:
            _save(get(), os.path.join(dest, fn))
        except Exception:
            continue
    return len(todo)


def pick(game):
    """Шлях до випадкової картинки гри або None."""
    d = os.path.join(DIR, game)
    try:
        files = [f for f in os.listdir(d) if f.endswith('.png')]
    except OSError:
        return None
    return os.path.join(d, random.choice(files)) if files else None
