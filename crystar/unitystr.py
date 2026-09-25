"""Length-prefixed UTF-8 string scanner for Unity MonoBehaviour raw data.

Crystar ships its text as ScriptableObjects whose serialised layout is just a
flat sequence of fields; strings are `u32 length + UTF-8 bytes + padding to 4`.
Nothing in the record stores absolute offsets, so a string can be replaced with
one of a different length as long as the prefix and padding are rewritten.
The scanner walks the record deterministically so export and import agree.
"""
import struct

MAX = 1 << 16


def _ok(b):
    try:
        s = b.decode('utf-8')
    except UnicodeDecodeError:
        return None
    if any(ord(c) < 0x20 and c not in '\n\r\t' for c in s):
        return None
    return s


def scan(data, start=0):
    """Yield (offset, length, text) for every string-looking field."""
    p, n = start, len(data)
    while p + 4 <= n:
        ln, = struct.unpack_from('<I', data, p)
        if 0 < ln <= MAX and p + 4 + ln <= n:
            s = _ok(data[p + 4:p + 4 + ln])
            if s is not None:
                yield p, ln, s
                p += 4 + ((ln + 3) & ~3)
                continue
        p += 4
    return


def rebuild(data, changes, start=0):
    """`changes` maps string offset -> new text. Returns new bytes."""
    out, prev = bytearray(), 0
    for off, ln, s in list(scan(data, start)):
        if off not in changes:
            continue
        out.extend(data[prev:off])
        b = changes[off].encode('utf-8')
        out.extend(struct.pack('<I', len(b)))
        out.extend(b)
        out.extend(b'\0' * ((-len(b)) % 4))
        prev = off + 4 + ((ln + 3) & ~3)
    out.extend(data[prev:])
    return bytes(out)
