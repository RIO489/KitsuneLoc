# -*- coding: utf-8 -*-
"""Українські літери в TMP-шрифтах Unity (SDF-атлас) — автоматично, з гліфів самого шрифту.

Японські шрифти (JIS) мають «російську» кирилицю, але:
  1. без і ї є ґ І Ї Є Ґ;
  2. кирилиця й лапки ’ “ ” — повноширинні (крок = кегль, літера посередині клітинки):
     слова в грі «розсипаються» на окремі літери.
Тож: (1) складаємо відсутні літери з наявних (ті самі рецепти, що в neptunia/fontfix:
і/І ← i/I, ї/Ї ← основа без крапки + крапки з ё/Ё, є/Є ← дзеркальні э/Э, ґ/Ґ ← г/Г з
«вусиком»), рахуємо для них SDF-поле з крутизною, підібраною за гліфами цього ж атласу,
і кладемо у вільне місце атласу; (2) повноширинним некитайсько-японським знакам ставимо
відступи, як у латиниці цього шрифту (медіани лівого/правого відступу a–z, A–Z, .,'";:).

Працює на рівні формату (TMP 1.x + Alpha8-атлас): гра дає лише бандл зі шрифтами.
Завжди з чистого оригіналу — повторний запуск дає той самий результат.
"""
import statistics
import numpy as np

from . import sdf
from .tmpfont import TmpFont

UKR = 'іІїЇєЄґҐ'
K = 4                                  # пікселів маски на піксель атласу


def is_cjk(cp):
    return (0x2E80 <= cp <= 0x9FFF or 0xAC00 <= cp <= 0xD7AF or 0xF900 <= cp <= 0xFAFF
            or 0xFF00 <= cp <= 0xFFEF or 0x3000 <= cp <= 0x303F or 0x20000 <= cp)


# ---------------------------------------------------------------- рецепти (маски)
def _rows(mask):
    return np.nonzero(mask.any(1))[0]


def _dotless(mask):
    """Основа i/I без крапки: нижній суцільний блок рядків з чорнилом."""
    ys = _rows(mask)
    y = ys[-1]
    while y > 0 and mask[y - 1].any():
        y -= 1
    out = mask.copy()
    out[:y] = False
    return out


def _dots(mask):
    """Діакритика над тілом літери (ё/Ё): рядки над першим розривом згори."""
    ys = _rows(mask)
    for a, b in zip(ys, ys[1:]):
        if b - a > 1:
            out = mask.copy()
            out[b:] = False
            return out
    return None


def _shift(mask, dx):
    out = np.zeros_like(mask)
    if dx >= 0:
        out[:, dx:] = mask[:, :mask.shape[1] - dx]
    else:
        out[:, :dx] = mask[:, -dx:]
    return out


def _stem_center(mask):
    """Середина вертикального штриха (за рядком посередині висоти)."""
    ys = _rows(mask)
    xs = np.nonzero(mask[(ys[0] + ys[-1]) // 2])[0]
    return (xs.min() + xs.max()) / 2


def _yi(base, yo):
    body = _dotless(base)
    dots = _dots(yo)
    if dots is None:
        return None
    xs = np.nonzero(dots.any(0))[0]
    dx = int(round(_stem_center(body) - (xs.min() + xs.max()) / 2))
    return body | _shift(dots, dx)


def _mirror(mask):
    xs = np.nonzero(mask.any(0))[0]
    out = np.zeros_like(mask)
    out[:, xs.min():xs.max() + 1] = mask[:, xs.min():xs.max() + 1][:, ::-1]
    return out


def _ghe(mask):
    """ґ/Ґ: вертикальний «вусик» угору на правому кінці верхньої планки."""
    ys, xs = _rows(mask), np.nonzero(mask.any(0))[0]
    T, B, R = ys[0], ys[-1], xs.max()
    mid = np.nonzero(mask[(T + B) // 2])[0]
    stem = max(2 * K, mid.max() - mid.min() + 1)
    w = max(2 * K, int(round(stem * 0.8)))             # вусик трохи тонший за основний штрих
    h = max(3 * K, int(round((B - T + 1) * 0.28)))
    out = mask.copy()
    out[max(0, T - h):T + 1, R - w + 1:R + 1] = True
    return out


RECIPES = {
    'і': lambda m: m['i'],
    'І': lambda m: m['I'],
    'ї': lambda m: _yi(m['i'], m['ё']),
    'Ї': lambda m: _yi(m['I'], m['Ё']),
    'є': lambda m: _mirror(m['э']),
    'Є': lambda m: _mirror(m['Э']),
    'ґ': lambda m: _ghe(m['г']),
    'Ґ': lambda m: _ghe(m['Г']),
}
NEEDS = 'iIёЁэЭгГ'


# ---------------------------------------------------------------- відступи
def _bearings(glyphs, chars):
    lb = [glyphs[ord(c)]['xo'] for c in chars if ord(c) in glyphs]
    rb = [glyphs[ord(c)]['adv'] - glyphs[ord(c)]['xo'] - glyphs[ord(c)]['w'] for c in chars if ord(c) in glyphs]
    return (statistics.median(lb), statistics.median(rb)) if lb else None


def _spacing(by):
    low = _bearings(by, 'abcdefghijklmnopqrstuvwxyz')
    up = _bearings(by, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    punct = _bearings(by, ".,'\";:")
    return low, up, punct


def _set_spacing(g, sp):
    ch = chr(g['id'])
    low, up, punct = sp
    b = (up if ch.isupper() else low) if ch.isalpha() else punct
    if b:
        g['xo'] = b[0]
        g['adv'] = b[0] + g['w'] + b[1]


# ---------------------------------------------------------------- головне
def fix(raw, atlas):
    """raw — дані TMP_FontAsset, atlas — поле атласу (float 0..1, H×W, y згори).
    Повертає (нові дані шрифту, новий атлас | None, [повідомлення]) або None, якщо
    шрифт кирилиці не має (тоді не чіпаємо)."""
    f = TmpFont(raw)
    by = f.by_char()
    if not any(0x410 <= c <= 0x44F for c in by):
        return None
    notes = []
    P, pad = f.face['PointSize'], int(f.face['Padding'])
    sp = _spacing(by)

    # 1. повноширинні некитайсько-японські знаки — відступи як у латиниці
    narrowed = 0
    for g in f.glyphs:
        cp = g['id']
        if cp > 0x7F and not is_cjk(cp) and g['adv'] >= 0.95 * P and g['w'] < 0.8 * g['adv']:
            _set_spacing(g, sp)
            narrowed += 1
    notes.append(f'відступи як у латиниці: {narrowed} знаків (кирилиця, лапки)')

    # 2. відсутні українські літери — з наявних
    missing = [c for c in UKR if ord(c) not in by]
    new_atlas = None
    if missing:
        if any(ord(c) not in by for c in NEEDS):
            notes.append('нема з чого скласти ' + ' '.join(missing) + ' (бракує ' +
                         ' '.join(c for c in NEEDS if ord(c) not in by) + ')')
            return f.build(), None, notes
        cv = sdf.Canvas(-P, 2 * P, -P, 2 * P, k=K)
        slope, err = sdf.calibrate(atlas, [by[ord(c)] for c in 'AoгЭe'], cv, pad)
        masks = {c: sdf.mask_of(atlas, by[ord(c)], cv, pad) for c in NEEDS}
        used = atlas > 0.02
        AH, AW = atlas.shape
        for g in f.glyphs:                               # рамки гліфів разом із полем навколо
            x0, y0 = max(0, int(g['x']) - pad - 1), max(0, int(g['y']) - pad - 1)
            used[y0:int(g['y'] + g['h']) + pad + 2, x0:int(g['x'] + g['w']) + pad + 2] = True
        new_atlas = atlas.copy()
        added = []
        for c in missing:
            m = RECIPES[c](masks)
            if m is None:
                notes.append(f'«{c}»: рецепт не спрацював')
                continue
            met, field = sdf.render(m, cv, pad, slope)
            H, W = field.shape
            spot = sdf.free_spot(used, W, H)
            if spot is None:
                notes.append(f'«{c}»: в атласі немає місця')
                continue
            X, Y = spot
            new_atlas[Y:Y + H, X:X + W] = field
            used[Y - 1:Y + H + 1, X - 1:X + W + 1] = True
            g = {'id': ord(c), 'x': X + pad, 'y': Y + pad, 'w': met['w'], 'h': met['h'],
                 'xo': met['xo'], 'yo': met['yo'], 'adv': 0.0, 'scale': 1.0}
            _set_spacing(g, sp)
            f.glyphs.append(g)
            added.append(c)
        notes.append(f'додано літер: {" ".join(added) or "—"} (поле: розмах {0.5 / slope:.1f} px, '
                     f'похибка відтворення {err * 255:.1f}/255)')
    return f.build(), new_atlas, notes


def fix_bundle(env):
    """Виправити всі TMP-шрифти з кирилицею в завантаженому бандлі UnityPy (атлас — у тому ж
    бандлі). Повертає [(назва шрифту, [повідомлення])]; порожньо — нічого не змінено."""
    from PIL import Image
    from UnityPy.enums import TextureFormat
    from .tmpfont import is_tmp_font
    byid = {o.path_id: o for o in env.objects}
    done = []
    for o in env.objects:
        if o.type.name != 'MonoBehaviour' or not is_tmp_font(o):
            continue
        raw = o.get_raw_data()
        try:
            f = TmpFont(raw)
        except ValueError:
            continue
        tex_obj = byid.get(f.atlas_id)
        if tex_obj is None or not any(0x410 <= g['id'] <= 0x44F for g in f.glyphs):
            continue
        tex = tex_obj.read()
        if tex.m_TextureFormat != TextureFormat.Alpha8:
            done.append((f.name, [f'атлас не Alpha8 ({tex.m_TextureFormat}) — такий SDF ще не підтримано']))
            continue
        atlas = np.asarray(tex.image.getchannel('A'), np.float32) / 255
        res = fix(raw, atlas)
        if res is None:
            continue
        new_raw, new_atlas, notes = res
        o.set_raw_data(new_raw)
        if new_atlas is not None:
            a = Image.fromarray(np.round(new_atlas * 255).astype(np.uint8), 'L')
            white = Image.new('L', a.size, 255)
            tex.set_image(Image.merge('RGBA', (white, white, white, a)), target_format=TextureFormat.Alpha8)
            tex.save()
        done.append((f.name, notes))
    return done
