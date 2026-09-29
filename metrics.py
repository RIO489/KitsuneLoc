# -*- coding: utf-8 -*-
"""Ширина тексту в пікселях за шрифтами самої гри (для перевірки довжини).

Рахувати символи — грубо: «Ш» у шрифті гри вчетверо ширша за «і». Тут
беремо шрифт з оригіналу (backup), проганяємо через той самий fontfix, що й
при заливанні (кирилиця звужена, і ї є ґ домальовані), і читаємо xadv
кожного гліфа. Таблиця {символ: ширина} кешується поруч з work, бо Neptunia
розпаковує шрифт кілька секунд.

Шрифт, схема символів (як текст стає гліфами) і службові коди — з профілю гри рушія
(compileheart/profiles.py); код один для всіх ігор. У MSK msgfont і sysfont однакові,
у Neptunia головне вікно — advfont. Гра без профілю (Crystar) — None.
"""
import json, os


def _profile(game):
    from compileheart import profiles
    return profiles.get(game)


def _font_file(game, backup_dir):
    prof = _profile(game)
    if prof is None:
        return None
    p = os.path.join(backup_dir, *prof.FONT_ARCHIVE.split('/'))
    return p if os.path.exists(p) else None


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
    if prof is None or font not in prof.FONTS:
        return None
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
              'compileheart/fontfix.py', 'compileheart/ffu.py', 'compileheart/scheme.py'):
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
    avg = tab.get('n') or 12
    return sum(tab.get(ch, avg) for ch in line)
