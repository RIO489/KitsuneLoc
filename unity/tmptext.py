# -*- coding: utf-8 -*-
"""Текст, вписаний прямо в префаби: m_text компонентів TextMeshPro / TextMeshProUGUI (TMP 1.x).

Type tree в IL2CPP немає, тож поле знаходимо за будовою: TMP_Text успадковує Graphic,
чиє останнє рядкове поле — m_TypeName події m_OnCullStateChanged
('UnityEngine.UI.MaskableGraphic+CullStateChangedEvent, …'); m_text — одразу за ним
(u32 довжина + UTF-8 + вирівнювання до 4). Перевірено на всіх 1041 написі Crystar
(uiscene/uistatic): збігається з тим, що знаходить unitystr.scan.
Абсолютних зсувів у даних немає — довжину тексту можна міняти.
"""
import struct

MARK = b'UnityEngine.UI.MaskableGraphic+CullStateChangedEvent'
CLASSES = ('TextMeshPro', 'TextMeshProUGUI')


def text_field(raw):
    """(зсув поля, довжина в байтах, текст) або None."""
    i = raw.find(MARK)
    if i < 4:
        return None
    n = struct.unpack_from('<I', raw, i - 4)[0]
    p = i + ((n + 3) & ~3)
    if p + 4 > len(raw):
        return None
    ln = struct.unpack_from('<I', raw, p)[0]
    if p + 4 + ln > len(raw):
        return None
    try:
        return p, ln, raw[p + 4:p + 4 + ln].decode('utf-8')
    except UnicodeDecodeError:
        return None


def with_text(raw, text):
    """Ті самі дані з іншим m_text."""
    p, ln, _old = text_field(raw)
    b = text.encode('utf-8')
    return (raw[:p] + struct.pack('<I', len(b)) + b + b'\0' * ((-len(b)) % 4)
            + raw[p + 4 + ((ln + 3) & ~3):])


def texts(env):
    """[(об'єкт UnityPy, ім'я GameObject, текст)] — усі написи TMP у файлі/бандлі."""
    out = []
    for o in env.objects:
        if o.type.name != 'MonoBehaviour':
            continue
        try:
            d = o.read()
            if d.m_Script.read().m_ClassName not in CLASSES:
                continue
        except Exception:
            continue
        f = text_field(o.get_raw_data())
        if f is None:
            continue
        try:
            go = d.m_GameObject.read().m_Name
        except Exception:
            go = ''
        out.append((o, go, f[2]))
    return out
