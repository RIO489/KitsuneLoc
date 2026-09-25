"""Теги розмітки в тексті Crystar.

  <CHARA=KEY> <WORD=KEY> <SYS=KEY>   — змінні: гра підставляє слово з таблиці
                                       parameter (імена, терміни, пункти меню)
  <ITEM> <VALUE> <SKILL> <TIME> <TYPE> <CHARA>
                                     — значення, які гра підставляє під час гри
  <ICON=...> <BUTTON=...>            — іконки й кнопки
  <#RRGGBB> ... </color>             — колір тексту

Змінні можна розшифрувати для читання; решту перекладач мусить зберегти.
"""
import collections, re

TAG = re.compile(r'<(/?)(#[0-9A-Fa-f]{6,8}|[A-Za-z]+)(?:=([^<>\n]+))?>')
LOOKUP = ('CHARA', 'WORD', 'SYS')
# у якій таблиці шукати ключ насамперед
PREFER = {'CHARA': 'CharacterMessage', 'WORD': 'RubyMessage', 'SYS': 'SystemMessage'}


def build_dict(files):
    """files: [(source, entries)] з work/crystar -> {ключ: {'en','ja','table'}}"""
    d = {}
    for source, entries in files:
        if not source.startswith('parameter/'):
            continue
        table = source.rsplit('.', 1)[-1]
        for a, b in zip(entries, entries[1:]):
            if a.get('kind') == 'key' and b.get('kind') != 'key':
                d.setdefault(a['src'], []).append(
                    {'en': b['src'], 'ja': b.get('ja', ''), 'table': table})
    return d


def lookup(d, kind, key):
    cands = d.get(key)
    if not cands:
        return None
    pref = PREFER.get(kind, '')
    for c in cands:
        if pref and pref in c['table']:
            return c
    return cands[0]


def resolve(text, lang, d, depth=3, wrap=True):
    """Замінити змінні на слова в дужках 〔…〕 для читання."""
    if not text or depth <= 0:
        return text

    def sub(m):
        slash, kind, key = m.groups()
        if slash or kind not in LOOKUP or not key:
            return m.group(0)
        c = lookup(d, kind, key)
        if not c or not c.get(lang):
            return m.group(0)
        word = resolve(c[lang], lang, d, depth - 1, wrap=False)
        return f'〔{word}〕' if wrap else word
    return TAG.sub(sub, text)


def technical(text):
    """Теги, які мусять лишитися в перекладі (мультимножина)."""
    out = collections.Counter()
    for m in TAG.finditer(text or ''):
        slash, kind, key = m.groups()
        if kind in LOOKUP and key:
            continue                      # змінну можна замінити словом
        out[m.group(0)] += 1
    return out


def legend(en, ja, d):
    """Коротка підказка до рядка: що означає кожен тег."""
    lines, seen = [], set()
    for text in (en or '', ja or ''):
        for m in TAG.finditer(text):
            full = m.group(0)
            if full in seen:
                continue
            seen.add(full)
            slash, kind, key = m.groups()
            if kind in LOOKUP and key:
                c = lookup(d, kind, key)
                word = resolve(c['en'], 'en', d, wrap=False) if c else '?'
                lines.append(f'{full} = {word} (можна словом)')
            elif kind in ('ICON', 'BUTTON'):
                lines.append(f'{full} — іконка, лишити')
            elif kind.startswith('#') or kind == 'color':
                lines.append(f'{full} — колір, лишити')
            else:
                lines.append(f'{full} — значення з гри, лишити')
    return '\n'.join(lines)


def check(src_en, tr, d):
    """Список проблем з тегами у перекладі."""
    probs = []
    miss = technical(src_en) - technical(tr)
    for t, n in miss.items():
        probs.append(f'бракує тегу {t}' + (f' ×{n}' if n > 1 else ''))
    for m in TAG.finditer(tr):
        slash, kind, key = m.groups()
        if kind in LOOKUP and key and not lookup(d, kind, key):
            probs.append(f'невідомий ключ {m.group(0)}')
    rest = TAG.sub('', tr)
    if '<' in rest or '>' in rest:
        probs.append('зламаний тег: зайва < або >')
    return probs
