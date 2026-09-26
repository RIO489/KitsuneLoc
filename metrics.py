# -*- coding: utf-8 -*-
"""Ширина тексту в пікселях за шрифтами самої гри (для перевірки довжини).

Рахувати символи — грубо: «Ш» у шрифті гри вчетверо ширша за «і». Тут
беремо шрифт з оригіналу (backup), проганяємо через той самий fontfix, що й
при заливанні (кирилиця звужена, і ї є ґ домальовані), і читаємо xadv
кожного гліфа. Таблиця {символ: ширина} кешується поруч з work, бо Neptunia
розпаковує шрифт кілька секунд.

Mary Skelter і Neptunia: у msgfont і sysfont ширини однакові (перевірено),
тож таблиця одна на гру. Crystar — шрифтів не розбирали, None.
"""
import json, os, re

MSK_FONT = ('System.bra', 'window\\font\\msgfont.ffu')
NEP_FONT = ('data/SYSTEM00000.pac', 'window/font/msgfont.ffu')

# службові коди не малюються: прибираємо перед вимірюванням
_MSK_CODE = re.compile(r'%[-+ 0#]*\d*(?:\.\d+)?[a-zA-Z]|#[A-Za-mo-z]')
_NEP_CODE = re.compile(r'#[A-Za-z]+(?:\[[^\]]*\])?|%[-+0#]*\d*(?:\.\d+)?(?:ll|l|h)?[a-zA-Z%]|<[A-Z]+>')


def _font_file(game, backup_dir):
    arc = MSK_FONT[0] if game == 'msk' else NEP_FONT[0]
    p = os.path.join(backup_dir, *arc.split('/'))
    return p if os.path.exists(p) else None


def _build(game, path):
    if game == 'msk':
        from maryskelter.bra import Bra
        from maryskelter import fontfix, ffu, chars
        data, _rep = fontfix.fix(Bra.read_some(path, [MSK_FONT[1]])[MSK_FONT[1]])
        f = ffu.Ffu(data)
        tab = {}
        for key, i in f.map.items():
            try:
                ch = key.to_bytes((key.bit_length() + 7) // 8 or 1, 'big').decode('utf-8')
            except UnicodeDecodeError:
                continue
            if len(ch) == 1 and i < len(f.entries):
                tab[ch] = f.entries[i][0]
        for ua, slot in chars.SUBST.items():          # і ї є ґ лежать у слотах ì ò ù ã
            if slot in tab:
                tab[ua] = tab[slot]
        return tab
    from neptunia.pac import Pac
    from neptunia import fontfix, ffu, chars
    data, _rep = fontfix.fix(Pac(path).read(NEP_FONT[1]))
    f = ffu.Ffu(data)
    tab = {}
    for code, i in f.map.items():
        if i >= len(f.entries):
            continue
        raw = bytes([code]) if code < 0x100 else code.to_bytes(2, 'big')
        try:
            ch = chars.decode(raw)
        except Exception:
            continue
        if len(ch) == 1:
            tab.setdefault(ch, f.entries[i][0])
    for ch, code in chars.CODE.items():               # українські літери — однобайтові слоти
        i = f.index(code)
        if i is not None:
            tab[ch] = f.entries[i][0]
    return tab


def table(game, backup_dir, cache_dir):
    """{символ: ширина в px} або None (немає шрифту чи гра без метрик)."""
    if game not in ('msk', 'nep'):
        return None
    path = _font_file(game, backup_dir)
    if not path:
        return None
    cache = os.path.join(cache_dir, f'_ширини_{game}.json')
    st = os.stat(path)
    sig = f'{st.st_size}:{int(st.st_mtime)}:{_code_sig()}'
    try:
        c = json.load(open(cache, encoding='utf-8'))
        if c.get('sig') == sig:
            return c['tab']
    except (OSError, ValueError):
        pass
    try:
        tab = _build(game, path)
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
    for p in ('maryskelter/fontfix.py', 'neptunia/fontfix.py', 'neptunia/chars.py'):
        try:
            out.append(str(int(os.path.getmtime(os.path.join(here, p)))))
        except OSError:
            out.append('0')
    return '-'.join(out)


def width(line, tab, game):
    """Ширина одного рядка в пікселях (службові коди не рахуються)."""
    line = (_MSK_CODE if game == 'msk' else _NEP_CODE).sub('', line)
    avg = tab.get('n') or 12
    return sum(tab.get(ch, avg) for ch in line)
