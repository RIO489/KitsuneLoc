"""STCM2L — байткод сценаріїв Re;Birth1 (main.DAT усередині .cl3).

Текст сцени — таблиця GBNL у блоці даних (зазвичай у GLOBAL_DATA на
початку файлу). Коли переклад довший за оригінал, блок росте і все, що
після нього, зсувається — тому треба знайти й поправити КОЖЕН вказівник
у файлі. Модель вказівників — як у neptools (stcm-editor), чиї файли гра
приймає:

  заголовок 0x3c Б: magic[0x20], u32 export_off, export_count, f28,
                    collection_link_off, f30, expansion_off, expansion_count
  експорт (0x28 Б): u32 type (0 код, 1 дані), name[0x20], u32 offset
  інструкція: u32 is_call, u32 opcode|адреса, u32 param_count, u32 size,
              param_count × (p0, p4, p8), далі дані до size
  дані: u32 type, u32 offset_unit, u32 f8, u32 length, length байтів
  collection link: заголовок 0x40 Б (u32 0, u32 offset, u32 count, ...),
                   записи 0x20 Б (u32 name_0, u32 name_1, ...)
  expansion: записи 0x50 Б (u32 index, u32 name, ...)

Параметр: p0 >> 30 = 0 — p0 це адреса блоку даних; 3 — особливий
(0xffffff40/41 — p4 адреса інструкції, 0xffffff42 — collection link,
0xffffff43 — expansion). p4/p8 з тегом 0 — теж адреси.
Інструкції обходимо від експортів: виклик -> ціль і наступна; opcode 0 і 6
не повертаються. Мертвий код, до якого не дістатись, не чіпаємо (так само
робить neptools).
"""
import struct

NO_RETURN = {0, 6}
SP_MIN, SP_STACK_MAX = 0xffffff00, 0xffffff0f
SP_4AC_MIN, SP_4AC_MAX = 0xffffff20, 0xffffff27
INSTR0, INSTR1, COLL, EXPN = 0xffffff40, 0xffffff41, 0xffffff42, 0xffffff43


class Stcm:
    def __init__(self, data):
        self.data = bytearray(data)
        if self.data[:5] != b'STCM2':
            raise ValueError('не STCM')
        (self.export_off, self.export_count, self.f28, self.coll_off, self.f30,
         self.exp_off, self.exp_count) = struct.unpack_from('<7I', self.data, 0x20)
        self.ptrs = set()        # позиції u32, де лежить адреса
        self.instrs = {}         # адреса -> (is_call, opcode, [(p0,p4,p8)], size)
        self.datas = {}          # адреса -> (type, unit, f8, length)
        self._walk()

    def u32(self, p):
        return struct.unpack_from('<I', self.data, p)[0]

    # ---- обхід ---------------------------------------------------------
    def _walk(self):
        d, n = self.data, len(self.data)
        self.ptrs.update((0x20, 0x2c))
        if self.exp_off:
            self.ptrs.add(0x34)
            for k in range(self.exp_count):
                self.ptrs.add(self.exp_off + 0x50 * k + 4)
        # collection link
        if self.coll_off + 0x40 <= n:
            self.ptrs.add(self.coll_off + 4)
            off, cnt = struct.unpack_from('<II', d, self.coll_off + 4)
            for k in range(cnt):
                self.ptrs.add(off + 0x20 * k)
                self.ptrs.add(off + 0x20 * k + 4)
        todo = []
        for k in range(self.export_count):
            p = self.export_off + 0x28 * k
            typ = self.u32(p)
            self.ptrs.add(p + 0x24)
            tgt = self.u32(p + 0x24)
            if typ == 0:
                todo.append(tgt)
            else:
                self._data(tgt)
        while todo:
            a = todo.pop()
            if a in self.instrs or a + 16 > n:
                continue
            is_call, op, pc, size = struct.unpack_from('<4I', d, a)
            if is_call not in (0, 1) or pc >= 16 or size < 16 + 12 * pc:
                raise ValueError(f'STCM: зіпсована інструкція @0x{a:x}')
            params = [struct.unpack_from('<3I', d, a + 16 + 12 * i) for i in range(pc)]
            self.instrs[a] = (is_call, op, params, size)
            if is_call:
                self.ptrs.add(a + 4)
                todo.append(op)
            if is_call or op not in NO_RETURN:
                todo.append(a + size)
            for i, (p0, p4, p8) in enumerate(params):
                base = a + 16 + 12 * i
                tag = p0 >> 30
                if tag == 0:
                    self.ptrs.add(base)
                    self._data(p0)
                    self._p48(base + 4, p4)
                    self._p48(base + 8, p8)
                elif tag == 2:
                    self._p48(base + 8, p8)
                elif tag == 3:
                    if p0 in (INSTR0, INSTR1):
                        self.ptrs.add(base + 4)
                        todo.append(p4)
                    elif p0 in (COLL, EXPN):
                        self.ptrs.add(base + 4)

    def _p48(self, pos, v):
        if v >> 30 == 0:
            self.ptrs.add(pos)

    def _data(self, a):
        if a in self.datas or a + 16 > len(self.data):
            return
        self.datas[a] = struct.unpack_from('<4I', self.data, a)

    # ---- текст ---------------------------------------------------------
    def gbnl_blocks(self):
        """[(адреса блоку даних, байти GBNL)] у порядку адрес."""
        out = []
        for a in sorted(self.datas):
            typ, unit, f8, ln = self.datas[a]
            if ln >= 0x40 and self.data[a + 16 + ln - 0x40:a + 16 + ln - 0x3c] == b'GBNL':
                out.append((a, bytes(self.data[a + 16:a + 16 + ln])))
        return out

    def replace_data(self, a, payload):
        """Нові байти блоку даних за адресою a; всі адреси за ним зсуваються.
        Повертає нові байти файлу."""
        typ, unit, f8, ln = self.datas[a]
        payload = bytes(payload)
        while len(payload) % 16 != ln % 16:          # вирівнювання як в оригіналі
            payload += b'\0'
        start, end = a + 16, a + 16 + ln
        delta = len(payload) - ln
        out = bytearray(self.data[:start]) + payload + self.data[end:]
        struct.pack_into('<I', out, a + 12, len(payload))
        for pos in self.ptrs:
            v = self.u32(pos)
            if start < v < end:
                raise ValueError(f'STCM: вказівник @0x{pos:x} усередину блоку тексту')
            npos = pos + delta if pos >= end else pos
            if v >= end:
                struct.pack_into('<I', out, npos, v + delta)
        return bytes(out)

    def shape(self):
        """Будова без абсолютних адрес — для перевірки, що після зсуву нічого
        не з'їхало: послідовність інструкцій з номерами цілей замість адрес."""
        targets = sorted(set(self.instrs) | set(self.datas))
        rank = {a: i for i, a in enumerate(targets)}
        out = []
        for a in sorted(self.instrs):
            is_call, op, params, size = self.instrs[a]
            ps = []
            for p0, p4, p8 in params:
                ps.append((rank.get(p0, p0) if p0 >> 30 == 0 else p0,
                           rank.get(p4, 'x') if p0 in (INSTR0, INSTR1) else p4 >> 30,
                           p8 >> 30))
            out.append((is_call, rank.get(op, 'x') if is_call else op, tuple(ps), size))
        return out
