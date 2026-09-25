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
