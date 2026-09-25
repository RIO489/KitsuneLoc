"""LZO1X-1 — розтискання і пакування (чистий Python, без залежностей).

Саме цим гра стискає секції в `.enc`-контейнерах (`data/table.enc`,
`DLCINFO/*.enc`). Встановлено читанням коду гри: процедура за зсувом
0xe6f5b0 — дослівний `lzo1x_decompress`; кінець потоку — `11 00 00`.
"""


def decompress(src, out_len=None):
    src = memoryview(bytes(src))
    out = bytearray()
    ip = 0

    def run_len(t, base):
        """Довжина, розписана нулями по 255."""
        nonlocal ip
        while src[ip] == 0:
            t += 255
            ip += 1
        t += base + src[ip]
        ip += 1
        return t

    def copy_match(pos, ln):
        for _ in range(ln):
            out.append(out[pos])
            pos += 1

    t = 0
    state = 'top'
    if src[ip] > 17:                       # перший блок літералів
        t = src[ip] - 17
        ip += 1
        if t < 4:
            state = 'match_next'
        else:
            out.extend(src[ip:ip + t]); ip += t
            state = 'first_literal_run'

    while True:
        if state == 'top':
            t = src[ip]; ip += 1
            if t >= 16:
                state = 'match'
            else:
                if t == 0:
                    t = run_len(0, 15)
                out.extend(src[ip:ip + t + 3]); ip += t + 3
                state = 'first_literal_run'

        if state == 'first_literal_run':
            t = src[ip]; ip += 1
            if t >= 16:
                state = 'match'
            else:                          # короткий збіг M2
                pos = len(out) - 0x801 - (t >> 2) - (src[ip] << 2); ip += 1
                copy_match(pos, 3)
                state = 'match_done'

        if state == 'match_next':
            out.extend(src[ip:ip + t]); ip += t
            t = src[ip]; ip += 1
            state = 'match'

        while state in ('match', 'match_done'):
            if state == 'match':
                if t >= 64:
                    pos = len(out) - 1 - ((t >> 2) & 7) - (src[ip] << 3); ip += 1
                    copy_match(pos, (t >> 5) - 1 + 2)
                elif t >= 32:
                    t &= 31
                    if t == 0:
                        t = run_len(0, 31)
                    pos = len(out) - 1 - (int.from_bytes(src[ip:ip + 2], 'little') >> 2); ip += 2
                    copy_match(pos, t + 2)
                elif t >= 16:
                    pos = len(out) - ((t & 8) << 11)
                    t &= 7
                    if t == 0:
                        t = run_len(0, 7)
                    pos -= int.from_bytes(src[ip:ip + 2], 'little') >> 2; ip += 2
                    if pos == len(out):
                        return bytes(out)              # кінець потоку
                    copy_match(pos - 0x4000, t + 2)
                else:
                    pos = len(out) - 1 - (t >> 2) - (src[ip] << 2); ip += 1
                    copy_match(pos, 2)
                state = 'match_done'
                continue
            # match_done: два молодші біти попереднього байта — доліт літералів
            t = src[ip - 2] & 3
            if t == 0:
                state = 'top'
                break
            out.extend(src[ip:ip + t]); ip += t
            t = src[ip]; ip += 1
            state = 'match'


def compress(data):
    """Найпростіший коректний потік: усе літералами, без пошуку збігів.

    Гра читає такий потік штатним розтискачем. Розмір виходить трохи більший
    за вихідні дані, але секції в контейнері й так зберігаються зі своїм
    розміром, тож це не заважає.
    """
    out = bytearray()
    n = len(data)
    pos = 0
    first = True
    while pos < n:
        chunk = min(n - pos, 238)
        if first and chunk >= 4:
            out.append(17 + chunk)
        else:
            t = chunk - 3
            if t < 16:
                out.append(t)
            else:
                out.append(0)
                t -= 15
                while t > 255:
                    out.append(0); t -= 255
                out.append(t)
        out.extend(data[pos:pos + chunk])
        pos += chunk
        first = False
        if pos < n:                      # між блоками потрібен збіг завдовжки 3
            out.extend(b'\x00\x00')      # M2: відстань 0x801 — заповнимо нижче
    out.extend(b'\x11\x00\x00')          # кінець потоку
    return bytes(out)
