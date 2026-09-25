# -*- coding: utf-8 -*-
"""DDS атласу інтерфейсу (`common.tid`): читання і точкове перекодування BC7.

Оригінал: 2048x2048, заголовок DX10, BC7_UNORM, один рівень міпмапів. Після
пікселів движок тримає хвіст '\\x08common.tid\\0…' — зберігаємо його як є.

Гра читає лише BC7 (усі 4299 текстур такі; нестиснутий B8G8R8A8 дає чорний
екран). BC7 — незалежні блоки 4x4 по 16 байтів, тож перекодовуємо тільки блоки
під зміненими прямокутниками, решта файлу лишається байт-у-байт оригінальною.
Кодувальник — etcpak (приходить разом з UnityPy), декодер — texture2ddecoder.
"""
import struct

DXGI_BC7_UNORM = 98
BLOCK = 16


def _layout(d):
    if d[:4] != b'DDS ' or d[84:88] != b'DX10':
        raise ValueError('очікую DDS із заголовком DX10')
    height, width = struct.unpack_from('<2I', d, 12)
    fmt = struct.unpack_from('<I', d, 128)[0]
    if fmt != DXGI_BC7_UNORM:
        raise ValueError(f'формат DXGI {fmt}, очікую BC7 ({DXGI_BC7_UNORM})')
    return width, height, 148


def decode(d):
    """DDS BC7 -> PIL.Image RGBA."""
    import texture2ddecoder
    from PIL import Image
    w, h, off = _layout(d)
    return Image.frombytes('RGBA', (w, h), texture2ddecoder.decode_bc7(d[off:], w, h),
                           'raw', 'BGRA')


def patch(d, img, rects):
    """Перекодувати в BC7 прямокутники `rects` [(x0, y0, x1, y1)] з `img` (RGBA
    розміром атласу) і вписати їх у копію DDS `d`. Решта байтів — без змін."""
    import etcpak
    w, h, off = _layout(d)
    if img.size != (w, h):
        raise ValueError(f'розмір атласу {img.size} != {(w, h)}')
    out = bytearray(d)
    per_row = w // 4
    for x0, y0, x1, y1 in rects:
        bx0, by0 = max(0, x0) // 4, max(0, y0) // 4
        bx1, by1 = (min(w, x1) + 3) // 4, (min(h, y1) + 3) // 4
        if bx1 <= bx0 or by1 <= by0:
            continue
        region = img.crop((bx0 * 4, by0 * 4, bx1 * 4, by1 * 4)).convert('RGBA')
        enc = etcpak.compress_bc7(region.tobytes(), region.width, region.height)
        nbx = bx1 - bx0
        for r in range(by1 - by0):
            src = enc[r * nbx * BLOCK:(r + 1) * nbx * BLOCK]
            dst = off + ((by0 + r) * per_row + bx0) * BLOCK
            out[dst:dst + len(src)] = src
    return bytes(out)
