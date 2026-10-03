# -*- coding: utf-8 -*-
"""Ширина тексту в пікселях за шрифтами самої гри (для перевірки довжини).

Рахувати символи — грубо: «Ш» у шрифті гри вчетверо ширша за «і». Тут
беремо шрифт з оригіналу (backup), проганяємо через той самий fontfix, що й
при заливанні (кирилиця звужена, і ї є ґ домальовані), і читаємо xadv
кожного гліфа. Таблиця {символ: ширина} кешується поруч з work, бо Neptunia
розпаковує шрифт кілька секунд.

Шрифт, схема символів (як текст стає гліфами) і службові коди — з профілю гри рушія
(compileheart/profiles.py); код один для всіх ігор. У MSK msgfont і sysfont однакові,
у Neptunia головне вікно — advfont.

Unity (з 2.9): TMP-шрифт з бандла, названого в FONT_BUNDLES модуля translate_<гра>
(Crystar: uistatic), після unity/fontfix — unity/tmpgame. Теги тексту (<CHARA=…>) не
малюються; TMP сам переносить слова (wraps, wrap).
"""
import importlib, json, os, re

_TAG = re.compile(r'<[^<>]*>')


def _profile(game):
    from compileheart import profiles
    return profiles.get(game)


def _font_file(game, backup_dir):
    prof = _profile(game)
    if prof is None:
        return None
    p = os.path.join(backup_dir, *prof.FONT_ARCHIVE.split('/'))
    return p if os.path.exists(p) else None


def unity_bundle(game, backup_dir):
    """Бандл з TMP-шрифтом гри Unity в оригіналах (backup) або None."""
    try:
        mod = importlib.import_module(f'translate_{game}')
    except ImportError:
        return None
    for name in getattr(mod, 'FONT_BUNDLES', ()):
        p = os.path.join(backup_dir, name)
        if os.path.exists(p):
            return p
    return None


def unity_boxes(game, backup_dir, cache_dir):
    """Поля префабів, у яких гра Unity показує документи (PREVIEW_BOXES модуля translate_<гра>):
    [{'re', 'px' — ширина в px шрифту програми (кегль атласу), 'назва'}]. Кеш — за бандлами."""
    try:
        mod = importlib.import_module(f'translate_{game}')
    except ImportError:
        return []
    spec = getattr(mod, 'PREVIEW_BOXES', None)
    font = unity_bundle(game, backup_dir)
    if not spec or not font:
        return []
    paths = sorted({os.path.join(backup_dir, b) for _r, b, _p, _n in spec} | {font})
    if not all(os.path.exists(p) for p in paths):
        return []
    sig = json.dumps([[os.path.getsize(p), int(os.path.getmtime(p))] for p in paths] + [spec, _code_sig()],
                     ensure_ascii=False)
    cache = os.path.join(cache_dir, f'_вікна_{game}.json')
    try:
        c = json.load(open(cache, encoding='utf-8'))
        if c.get('sig') == sig:
            return c['boxes']
    except (OSError, ValueError):
        pass
    import UnityPy
    from unity import tmptext
    from unity.tmpfont import TmpFont, is_tmp_font
    point = None                                   # кегль атласу шрифту (одиниці metrics.table)
    for o in UnityPy.load(font).objects:
        if o.type.name == 'MonoBehaviour' and is_tmp_font(o):
            try:
                f = TmpFont(o.get_raw_data())
            except ValueError:
                continue
            if any(0x410 <= g['id'] <= 0x44F for g in f.glyphs):
                point = f.face['PointSize']
                break
    boxes, envs = [], {}
    for rx, bundle, path, name in spec:
        env = envs.get(bundle) or envs.setdefault(bundle, UnityPy.load(os.path.join(backup_dir, bundle)))
        got = tmptext.field_box(env, path)
        if got and point:
            w, h, size = got
            boxes.append({'re': rx, 'px': round(w * point / size, 1), 'назва': name})
    try:
        os.makedirs(cache_dir, exist_ok=True)
        json.dump({'sig': sig, 'boxes': boxes}, open(cache, 'w', encoding='utf-8'), ensure_ascii=False)
    except OSError:
        pass
    return boxes


def wraps(game, backup_dir):
    """Чи гра сама переносить слова за шириною поля (TMP) — тоді межа рядків важить більше."""
    return _profile(game) is None and unity_bundle(game, backup_dir) is not None


def game_font(game, path, font='msg'):
    """(FFU шрифту гри після fontfix, схема, профіль) — те саме, що кладе в гру «2»."""
    from compileheart import archive
    from compileheart.ffu import Ffu
    prof = _profile(game)
    data, _rep = prof.fix(archive.read(path, prof.FONTS[font]))
    return Ffu(data), prof.SCHEME, prof


def _build(game, path, font='msg'):
    f, scheme, _prof = game_font(game, path, font)
    tab = {}
    for code, i in f.map.items():
        if i >= len(f.entries):
            continue
        raw = code.to_bytes((code.bit_length() + 7) // 8 or 1, 'big')
        try:
            ch = scheme.decode(raw)
        except Exception:
            continue
        if len(ch) == 1:
            tab.setdefault(ch, f.entries[i][0])
    for ch, code in scheme.letters(f):            # і ї є ґ у слотах / однобайтові літери, « »
        i = f.index(code)
        if i is not None:
            tab[ch] = f.entries[i][0]
    return tab


def table(game, backup_dir, cache_dir, font='msg'):
    """{символ: ширина в px} або None (немає шрифту чи гра без метрик).
    font — 'msg' (msgfont: інтерфейс, таблиці, історія діалогів) або 'adv'
    (Neptunia: advfont головного вікна діалогу)."""
    prof = _profile(game)
    if prof is None:
        path = unity_bundle(game, backup_dir) if font == 'msg' else None
    elif font not in prof.FONTS:
        return None
    else:
        path = _font_file(game, backup_dir)
    if not path:
        return None
    cache = os.path.join(cache_dir, f'_ширини_{game}.json' if font == 'msg'
                         else f'_ширини_{game}_{font}.json')
    st = os.stat(path)
    sig = f'{st.st_size}:{int(st.st_mtime)}:{_code_sig()}'
    try:
        c = json.load(open(cache, encoding='utf-8'))
        if c.get('sig') == sig:
            return c['tab']
    except (OSError, ValueError):
        pass
    try:
        if prof is None:
            from unity import tmpgame
            got = tmpgame.load(path)
            if got is None:
                return None
            tab = tmpgame.widths(got[0])
        else:
            tab = _build(game, path, font)
    except Exception:
        return None
    try:
        os.makedirs(cache_dir, exist_ok=True)
        json.dump({'sig': sig, 'tab': tab}, open(cache, 'w', encoding='utf-8'), ensure_ascii=False)
    except OSError:
        pass
    return tab


def _code_sig():
    """Кеш застаріває, коли міняється fontfix (інші відступи — інші ширини)."""
    here = os.path.dirname(os.path.abspath(__file__))
    out = []
    for p in ('maryskelter/fontfix.py', 'neptunia/fontfix.py', 'neptunia/chars.py', 'maryskelter/chars.py',
              'maryskelter/profile.py', 'neptunia/profile.py',
              'compileheart/fontfix.py', 'compileheart/ffu.py', 'compileheart/scheme.py',
              'unity/fontfix.py', 'unity/sdf.py', 'unity/tmpgame.py'):
        try:
            out.append(str(int(os.path.getmtime(os.path.join(here, p)))))
        except OSError:
            out.append('0')
    return '-'.join(out)


def width(line, tab, game):
    """Ширина одного рядка в пікселях (службові коди не рахуються; символи — як їх
    запише імпорт: ’ -> ', … -> ... у Shift-JIS-схемі)."""
    prof = _profile(game)
    if prof is not None:
        line = prof.SCHEME.plain(prof.CODES.sub('', line))
    else:
        line = _TAG.sub('', line)
    avg = tab.get('n') or 12
    return sum(tab.get(ch, avg) for ch in line)


def wrap(line, limit, tab, game):
    """Рядок, перенесений за словами в межу (як TMP): [рядки]. Слово, ширше за межу, — окремим рядком."""
    out, cur = [], ''
    for word in line.split(' '):
        cand = word if not cur else cur + ' ' + word
        if cur and width(cand, tab, game) > limit:
            out.append(cur)
            cur = word
        else:
            cur = cand
    out.append(cur)
    return out
