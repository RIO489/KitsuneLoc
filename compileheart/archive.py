# -*- coding: utf-8 -*-
"""Архіви рушія Compile Heart — за магією файлу, не за назвою гри.

  'PDA\\0'    — .bra (Mary Skelter): maryskelter/bra.py, імена файлів — зі зворотними скісними;
  'DW_PACK\\0' — .pac (Neptunia): neptunia/pac.py.
Шляхи всередині архіву тут завжди з прямими скісними ('window/font/msgfont.ffu').
"""
import os


def kind(path):
    with open(path, 'rb') as f:
        head = f.read(8)
    if head[:4] == b'PDA\0':
        return 'pda'
    if head == b'DW_PACK\0':
        return 'dwpack'
    return None


def read(path, name):
    """Вміст одного файлу архіву."""
    k = kind(path)
    if k == 'pda':
        from maryskelter.bra import Bra
        inner = name.replace('/', '\\')
        return Bra.read_some(path, [inner])[inner]
    if k == 'dwpack':
        from neptunia.pac import Pac
        return Pac(path).read(name)
    raise ValueError(f'{os.path.basename(path)}: невідомий архів')
