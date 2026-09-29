# -*- coding: utf-8 -*-
"""FFU Mary Skelter — спільний compileheart/ffu.py (варіант 'UF \\x02': ключ = UTF-8).

Тут лише старий зручний API: index/glyph приймають і символ, і код.
"""
from compileheart.ffu import Ffu as _Ffu


def key(ch):
    return int.from_bytes(ch.encode('utf-8'), 'big')


class Ffu(_Ffu):
    def index(self, ch):
        return super().index(ch if isinstance(ch, int) else self.code(ch))

    def glyph(self, ch):
        return super().glyph(ch if isinstance(ch, int) else self.code(ch))
