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


def go_path(go, depth=0):
    """Шлях GameObject у префабі: «Root/…/Object» (через Transform.m_Father). Посилання —
    через PPtr.read(): path_id повторюються між файлами одного бандла."""
    tr = None
    for c in getattr(go, 'm_Component', None) or []:
        try:
            o = getattr(c, 'component', c).read()
        except Exception:
            continue
        if type(o).__name__ in ('Transform', 'RectTransform'):
            tr = o
            break
    if tr is None or depth > 40:
        return go.m_Name, tr
    try:
        f = tr.m_Father
        if f and f.path_id:
            parent = f.read().m_GameObject.read()
            return go_path(parent, depth + 1)[0] + '/' + go.m_Name, tr
    except Exception:
        pass
    return go.m_Name, tr


def field_box(env, path):
    """(ширина, висота, кегль) TMP-поля за шляхом у префабі (поле сталого розміру: якорі
    min == max), або None. Кегль — m_fontSize (unity/tmplayout)."""
    from . import tmplayout
    name = path.rsplit('/', 1)[-1]
    for o, go, _t in texts(env):
        if go != name:
            continue
        try:
            g = o.read().m_GameObject.read()
        except Exception:
            continue
        p, tr = go_path(g)
        if p != path or tr is None or not hasattr(tr, 'm_SizeDelta'):
            continue
        if (tr.m_AnchorMin.x, tr.m_AnchorMin.y) != (tr.m_AnchorMax.x, tr.m_AnchorMax.y):
            return None                      # розтягнуте за батьком — розмір не в самому полі
        raw = o.get_raw_data()
        flds = tmplayout.fields(raw)
        if flds is None:
            return None
        return tr.m_SizeDelta.x, tr.m_SizeDelta.y, tmplayout.read(raw, flds)['fontSize']
    return None


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
