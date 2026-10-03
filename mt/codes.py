# -*- coding: utf-8 -*-
"""Службові коди гри в тексті -> заглушки (перед машинним перекладом) і назад.

Сервіс перекладу не знає, що «%s», «#Font[2]», «<CHARA=PLAYER_0002>» чи «{Amount}» —
не слова: перекладе, розірве чи загубить, і гра покаже сміття або впаде. Тому кожен код
замінюємо заглушкою, а після перекладу повертаємо на місце. Переставляти заглушки можна
(інший порядок слів), губити чи вигадувати — ні: такий рядок не приймається.

Які коди — ті самі правила, що в перевірці (sheets.check_entry): профіль рушія Compile
Heart (`prof.CODES`), коди формату (`sheets.FORMAT_CODES`, напр. теги FText Unreal), плюс
загальні: теги <…>, printf %s/%d, {Name}. Тег-змінна, яку гра підставляє словом
(Crystar <CHARA=…>), — необов'язкова: її можна замінити словом у потрібному відмінку.
"""
import html, re

# заглушки: для мовних моделей — ⟦N⟧ (не трапляються в текстах ігор, токенізуються коротко);
# для DeepL/Google (режим розмітки) — порожній тег <x i="N"/>, який сервіс лишає як є
_BR = re.compile(r'⟦(\d+)⟧')
_XML = re.compile(r'<x\s+i="(\d+)"\s*/?>(?:\s*</x>)?')
_XML_BR = re.compile(r'<br\s*/?>(?:\s*</br>)?')

GENERIC = [re.compile(r'<[^<>\n]*>'),                                   # теги <i>, <CHARA=…>, </>
           re.compile(r'%[-+0#]*\d*(?:\.\d+)?(?:ll|l|h)?[diouxXeEfgGcsp%]'),
           re.compile(r'\{[^{}\n]*\}')]


def doc_rules(doc):
    """Регулярні вирази кодів для документа work\\ (у порядку важливості)."""
    import sheets
    out = []
    prof = sheets._profile(doc.get('game'))
    if prof is not None and getattr(prof, 'CODES', None) is not None:
        out.append(prof.CODES)
    fmt = getattr(sheets, 'FORMAT_CODES', {})        # коди формату (теги FText Unreal) — якщо є
    if doc.get('format') in fmt:
        out.append(fmt[doc['format']]())
    return out + GENERIC


class Masked:
    """Текст із заглушками. codes[i] — код, optional — номери, які можна замінити словом
    (legend[i] — що це за слово)."""

    def __init__(self, text, codes, optional, legend):
        self.text, self.codes, self.optional, self.legend = text, codes, optional, legend

    def unmask(self, out, style='br'):
        """Переклад із заглушками -> (текст, проблема | None). Проблема — рядок не приймати."""
        rx = _XML if style == 'xml' else _BR
        if style == 'xml':
            out = _XML_BR.sub('\n', out)
        seen = {}
        for m in rx.finditer(out):
            i = int(m.group(1))
            seen[i] = seen.get(i, 0) + 1
        for i, n in seen.items():
            if i >= len(self.codes):
                return None, f'вигадана заглушка {i}'
            if n > 1:
                return None, f'код {self.codes[i]} повторено {n} рази'
        lost = [self.codes[i] for i in range(len(self.codes)) if i not in seen and i not in self.optional]
        if lost:
            return None, 'загублено код ' + ' '.join(lost)
        text = rx.sub(lambda m: self.codes[int(m.group(1))], out)
        if style == 'xml':
            text = html.unescape(text)
        if '⟦' in text or '⟧' in text:
            return None, 'зламана заглушка'
        return text.strip(), None


def mask(text, rules, words=None, style='br'):
    """text -> Masked. rules — doc_rules(); words(code) -> слово | None — для тегів-змінних,
    які гра підставляє словом (тоді код необов'язковий). style 'xml' — для DeepL/Google:
    решту тексту екрануємо, переноси — <br/>."""
    spans = []
    for rx in rules:
        for m in rx.finditer(text or ''):
            if m.end() > m.start():
                spans.append((m.start(), m.end()))
    spans.sort(key=lambda s: (s[0], -s[1]))
    picked, end = [], -1
    for a, b in spans:                       # перекриття — перемагає раніший і довший
        if a >= end:
            picked.append((a, b))
            end = b
    out, codes, optional, legend, i = [], [], set(), {}, 0
    esc = (lambda s: html.escape(s, quote=False).replace('\n', '<br/>')) if style == 'xml' else (lambda s: s)
    for a, b in picked:
        out.append(esc(text[i:a]))
        code = text[a:b]
        n = len(codes)
        codes.append(code)
        w = words(code) if words else None
        if w:
            optional.add(n)
            legend[n] = w
        out.append(f'<x i="{n}"/>' if style == 'xml' else f'⟦{n}⟧')
        i = b
    out.append(esc(text[i:]))
    return Masked(''.join(out), codes, optional, legend)
