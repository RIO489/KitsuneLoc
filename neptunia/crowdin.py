"""Тексти stcm-editor / Crowdin (`*.gbin.txt`, `*.gstr.txt`, `main.cl3.txt`).

Так перекладали Neptunia до переходу на книги Excel. Будова файлу (neptools,
Gbnl::WriteTxt): рядок-роздільник із символів «―» (cp932 81 5C), пробіл і
номер рядка, далі сам текст (переноси \\r\\n), наприкінці роздільник і «EOF».

Номер рядка (neptools Gbnl::GetId):
  * сцени (запис з 9 полів, перше — u32): номер = перше поле запису (ID репліки);
  * GSTR із записом (рядок, u32, рядок): номер = u32 для тексту,
    u32 + 100000 — для ключа IDS_…;
  * решта: k·10000 + номер запису, де k — порядковий номер непорожнього
    рядкового поля в записі (1, 2…); поле фіксованої довжини має k = 0
    (або 10000, якщо flags і field_28 != 1).

Кирилиця в цих файлах — двобайтова cp932, а і ї є ґ записано грецькими
літерами (стара схема): тут повертаємо їх назад. Однобайтову схему
(preconvert) теж розуміємо — через chars.decode.
"""
import re
from . import chars
from .gbnl import STRING, U32

GREEK = str.maketrans({'Α': 'І', 'Β': 'Ї', 'Γ': 'Ґ', 'Δ': 'Є',
                       'α': 'і', 'β': 'ї', 'γ': 'ґ', 'δ': 'є'})
_SEP = re.compile(r'^―{8,} (\S+)\s*$')


def parse(raw):
    """Байти .txt -> {номер: текст}."""
    text = chars.decode(raw).translate(GREEK)
    out, cur, buf = {}, None, []
    # neptools пише переноси як \r\n, але після редагування трапляється й
    # «голий» \n — тоді роздільник наступного рядка прилипав до тексту
    for line in re.split(r'\r?\n', text):
        m = _SEP.match(line)
        if m:
            if cur is not None:
                out[cur] = '\n'.join(buf)
            cur, buf = m.group(1), []
            if cur == 'EOF':
                cur = None
            continue
        if cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = '\n'.join(buf)
    return out


def ids(g):
    """{номер neptools: 'запис.зсув'} для таблиці Gbnl (як у translate_nep)."""
    types = g.types
    out = {}
    is_event = (len(types) == 9 and types[0][0] == U32 and types[8][0] == STRING)
    is_gstr_pair = (g.is_gstr and len(types) == 3 and types[1][0] == U32
                    and types[0][0] == STRING and types[2][0] == STRING)
    fix_k = 10000 if (g.flags and g.f28 != 1) else 0
    for j in range(g.struct_count):
        k = 0
        for fo, kind, _size in g.fields:
            if kind == 'str':
                if g.record_u32(j, fo) == 0xffffffff:
                    continue
                k += 1
                this_k = k
            else:
                this_k = fix_k
            if is_event and fo == types[8][1]:
                nid = g.record_u32(j, types[0][1])
            elif is_gstr_pair:
                v = g.record_u32(j, types[1][1])
                nid = v + 100000 if fo == types[0][1] else v
            else:
                nid = this_k * 10000 + j
            out[str(nid)] = f'{j}.{fo}'
    return out
