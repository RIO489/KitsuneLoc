"""SSA (SSAD) — розкладка/анімація спрайтів інтерфейсу Re;Birth1 (лише читання).

Після заголовка 0x20 Б — ланцюжок блоків: тег[4] + u32 розмір + дані.
PART починає новий спрайт; NAME — 32 Б (cp932); AREA — 4×u32: x0, y0, x1, y1
у пікселях парної .tid (нулі — група без картинки). Координати ми лише
ЧИТАЄМО — як рамки кадрів для написів на картинках.
"""
import struct


def parts(data):
    """[(номер, ім'я, (x0, y0, x1, y1))] — лише спрайти з ненульовою AREA."""
    d = bytes(data)
    if d[:4] != b'SSAD':
        raise ValueError('не SSA')
    # блоки анімацій мають вкладені кінцівки, тож ідемо не ланцюжком, а
    # пошуком тегів: PART — новий спрайт, NAME перед AREA — його ім'я
    out, n, name = [], -1, ''
    p = 0x20
    while True:
        nxt = [x for x in (d.find(b'PART\x04\0\0\0', p), d.find(b'NAME\x20\0\0\0', p),
                           d.find(b'AREA\x10\0\0\0', p)) if x >= 0]
        if not nxt:
            break
        p = min(nxt)
        tag = d[p:p + 4]
        if tag == b'PART':
            n += 1
            name = ''
        elif tag == b'NAME':
            name = d[p + 8:p + 40].split(b'\0')[0].decode('cp932', 'replace')
        else:
            box = struct.unpack_from('<4i', d, p + 8)
            if any(box):
                out.append((n, name, box))
        p += 8
    return out


def frames(ssa_blobs):
    """Унікальні рамки з кількох .ssa однієї текстури: {номер: (x0, y0, x1, y1)}."""
    seen, out = set(), {}
    for blob in ssa_blobs:
        for _n, _nm, box in parts(blob):
            if box not in seen:
                seen.add(box)
                out[len(out)] = box
    return out
