"""Рядкові слоти в таблицях `.enc`.

Схему записів знати не потрібно: кожен рядок лежить у полі фіксованого
розміру, добитому нулями. Слот = (зсув, текст, місткість), де місткість —
це довжина тексту плюс усі нулі до наступних даних. Переклад пишеться в те
саме поле, тож нічого не зсувається і решта таблиці лишається недоторканою.
"""
import re

RX = re.compile(rb'[\x20-\x7e][\x20-\x7e]{2,}\x00')
# службові рядки (шляхи, імена файлів, чисті числа) — не для перекладу
SERVICE = re.compile(r'^(?:[\w./\\-]+\.(?:ism2|tid|dds|acb|cl3|bin|pac|enc)|/[\w./-]+|\d+|[\w-]+/[\w./-]+)$')


def slots(data, encoding='utf-8'):
    """[(зсув, текст, місткість)] — місткість разом із завершальним нулем."""
    out = []
    for m in RX.finditer(data):
        s, e = m.start(), m.end() - 1
        cap = e - s
        p = e
        while p < len(data) and data[p] == 0:
            cap += 1
            p += 1
        out.append((s, data[s:e].decode(encoding, 'replace'), cap))
    return out


def field_start(data, off):
    """Початок поля, у якому лежить слот `off`. Слот — лише ASCII до нуля, тож опис
    «Minor Water damage to\\none enemy» чи «Press the □ button…» давав тільки хвіст
    після переносу / не-ASCII символу. Ідемо назад через ASCII, переноси й цілі
    символи UTF-8 до першого байта, що текстом не є (нуль чи службовий байт)."""
    import unicodedata
    p = off
    while p > 0:
        c = data[p - 1]
        if c == 10 or 0x20 <= c < 0x7f:
            p -= 1
            continue
        for n in (2, 3, 4):
            if p - n < 0:
                continue
            try:
                ch = data[p - n:p].decode('utf-8')
            except UnicodeDecodeError:
                continue
            if len(ch) == 1 and unicodedata.category(ch)[0] != 'C':
                p -= n
                break
        else:
            break
    return p


def fields(data, encoding='utf-8'):
    """[(зсув слота, початок поля, текст поля, місткість від початку поля)].
    Зсув слота лишається id рядка (як у slots), а пишемо — з початку поля."""
    out = []
    for off, text, cap in slots(data, encoding):
        s = field_start(data, off)
        if s != off:
            text = data[s:off].decode(encoding, 'replace') + text
            cap += off - s
        out.append((off, s, text, cap))
    return out


def is_service(text):
    return bool(SERVICE.match(text.strip()))


def build(data, changes, encoding='utf-8'):
    """`changes` — {зсув: новий текст}. Повертає (байти, список проблем)."""
    buf, bad = bytearray(data), []
    for off, (text, cap) in changes.items():
        b = text.encode(encoding, 'strict')
        if len(b) + 1 > cap:
            bad.append((off, len(b) + 1, cap))
            continue
        buf[off:off + cap] = b + b'\0' * (cap - len(b))
    return bytes(buf), bad
