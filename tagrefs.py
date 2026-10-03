# -*- coding: utf-8 -*-
"""Теги-посилання в тексті гри -> слова, для читання в редакторі (з 2.11, прохання перекладача).

Crystar пише в репліках не слово, а посилання на нього: «<CHARA=PLAYER_0002>», «<WORD=ABYSS>».
Тримати в голові, що за ключем, незручно, а терміни таких рядків не знаходились (японські
знаки злипаються з тегом). Тут:
  - слово для тега — з таблиць самої гри (crystar/tags: CHARA/WORD/SYS -> рядок parameter,
    для документів формату unity-mb2), а переклад слова — з проєкту, якщо той рядок уже
    перекладено;
  - свої слова перекладача (`Переклад\\<гра>\\теги.json` {тег: слово}) — для тегів, яких
    таблиці не знають (<ITEM>, <VALUE>…), і для будь-якої гри; своє слово важливіше.
Сам текст рядка не змінюється — це лише показ.
"""
import json, os, re

TAG = re.compile(r'<(/?)(#[0-9A-Fa-f]{6,8}|[A-Za-z]+)(?:=([^<>\n]+))?>')
USER_FILE = 'теги.json'


class Refs:
    def __init__(self, pr):
        self.pr = pr
        self.path = os.path.join(pr.xl, USER_FILE)
        self.user = self._load()
        self.d = None                       # словник тегів гри (crystar/tags) або None
        if any(doc.get('format') == 'unity-mb2' for _p, doc in pr.docs.values()):
            from crystar import tags
            self.tags = tags
            self.d = tags.build_dict([(s, doc['entries']) for s, (_p, doc) in pr.docs.items()])
        self._tr_index = None

    def _load(self):
        try:
            with open(self.path, encoding='utf-8') as f:
                d = json.load(f)
            return {k: v for k, v in d.items() if isinstance(v, str) and v.strip()} if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def set_user(self, tag, word):
        word = (word or '').strip()
        if word:
            self.user[tag] = word
        else:
            self.user.pop(tag, None)
        if self.user:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self.user, f, ensure_ascii=False, indent=1)
        elif os.path.exists(self.path):
            os.remove(self.path)

    def _tr_of(self, table, en):
        """Переклад рядка таблиці гри, на який посилається тег (як його переклали в проєкті)."""
        if self._tr_index is None:
            self._tr_index = {}
            for r in self.pr.rows:
                if r['source'].startswith('parameter/') and r['e'].get('tr'):
                    self._tr_index.setdefault((r['source'].rsplit('.', 1)[-1], r['e']['src']), r['e']['tr'])
        return self._tr_index.get((table, en), '')

    def reset_tr(self):
        self._tr_index = None                # переклад змінився — індекс перебудується

    def word(self, m, lang):
        """Для збігу TAG: (слово мовою lang, переклад слова | '', звідки) або None."""
        full = m.group(0)
        if full in self.user:
            return self.user[full], '', 'своє слово'
        slash, kind, key = m.groups()
        if self.d is None or slash or kind not in self.tags.LOOKUP or not key:
            return None
        c = self.tags.lookup(self.d, kind, key)
        if not c:
            return None
        w = self.tags.resolve(c.get(lang) or c['en'], lang, self.d, wrap=False)
        tr = self._tr_of(c['table'], c['en'])
        return w, tr, c['table']

    def spans(self, text, lang):
        """[(початок, кінець, тег, слово, переклад, звідки)] — теги, які можна показати словом."""
        out = []
        for m in TAG.finditer(text or ''):
            got = self.word(m, lang)
            if got:
                out.append((m.start(), m.end(), m.group(0)) + got)
        return out

    def plain(self, text, lang='en'):
        """Текст з тегами, заміненими на слова (для пошуку термінів)."""
        sp = self.spans(text, lang)
        if not sp:
            return text
        out, i = [], 0
        for a, b, _t, w, _tr, _s in sp:
            out += [text[i:a], ' ' + w + ' ']
            i = b
        return ''.join(out + [text[i:]])
