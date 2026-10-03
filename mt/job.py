# -*- coding: utf-8 -*-
"""Машинний переклад набору рядків проєкту — у фоні, пакетами.

  pick()  — що перекладати: по одному рядку на групу однакових (машинний іде в усі з тим
            самим оригіналом — Project.set_mt), без «не перекладати» і службових ключів;
  Job     — пакети в порядку гри, контекст сцени (попередні рядки того самого файлу),
            межі ширини шрифтом гри (sheets.width_limit -> «max_chars»), коди -> заглушки
            (codes.py), після перекладу — та сама перевірка, що в редакторі
            (sheets.check_entry): ширина, рядки, терміни. Не влізло — один повторний запит
            «виправ: …» (лише мовна модель; DeepL/Google порад не приймають).
Результати віддає через post(вид, дані) — вікно застосовує їх у головному потоці.
"""
import re

import sheets
from mt import codes
from mt.engines import MTError

DEFAULT_STYLE = ('Звертання між персонажами — за сюжетом і контекстом (ти/ви). Лапки « », тире —. '
                 'Вигуки, звуки й заїкання адаптуй українською (Ах, Ой, Гм, «Щ-що?»). '
                 'Числа й одиниці — як в оригіналі.')
# попередження перевірки, з якими варто просити переклад іще раз
_RETRY = re.compile(r'ширший|не влазить|рядків \d|переносів|довший|задовго|термін')
_SAMPLE = 'Широка електрифікація південних губерній дасть змогу'
_CTX_ROWS = 4                       # скільки попередніх рядків сцени дати для контексту


def pick(pr, rows, redo=False, with_tr=False):
    """Рядки для перекладу: по одному на (вид, оригінал). redo — і ті, де машинний уже є;
    with_tr — і перекладені (порівняти)."""
    out, seen = [], set()
    for r in rows:
        if not pr.counted(r) or (r['e'].get('tr') and not with_tr) or (pr.mt(r) and not redo):
            continue
        u = (r['kind'], r['e']['src'])
        if u in seen:
            continue
        seen.add(u)
        out.append(r)
    return out


def names(pr):
    """[(ім'я англійською, переклад)] — уже перекладені імена мовців."""
    out = {}
    for r in pr.rows:
        if r['kind'] == 'name' and r['e'].get('tr'):
            out.setdefault(r['e']['src'], r['e']['tr'])
    return sorted(out.items())


class Job:
    def __init__(self, pr, engine, rows, ctx, terms, refs, post, cancel):
        self.pr, self.eng, self.rows, self.ctx = pr, engine, rows, ctx
        self.terms, self.refs, self.post, self.cancel = terms, refs, post, cancel
        self.ok = self.failed = self.warned = 0
        self.ppc = None                 # px на знак (українською) — для «max_chars»
        if ctx and ctx.get('wtab') and not ctx.get('wrap'):
            import metrics
            self.ppc = metrics.width(_SAMPLE, ctx['wtab'], ctx['game']) / len(_SAMPLE) or None

    # ------------------------------------------------------------ рядок -> завдання
    def _word(self, code):
        """Тег-змінна, яку гра підставляє словом (Crystar <CHARA=…>) -> слово для моделі."""
        if self.refs is None:
            return None
        import tagrefs
        m = tagrefs.TAG.fullmatch(code)
        got = self.refs.word(m, 'en') if m else None
        if not got:
            return None
        w, tr, _src = got
        return f'{w} (укр. {tr})' if tr else w

    def _item(self, n, r):
        e, doc = r['e'], self.pr.docs[r['source']][1]
        m = codes.mask(e['src'], codes.doc_rules(doc), self._word, self.eng.style)
        it = {'id': str(n), 'r': r, 'doc': doc, 'm': m, 'text': m.text, 'src': e['src']}
        if e.get('ja'):
            it['ja'] = e['ja']
        if e.get('note'):
            it['note'] = e['note']
        if r['who']:
            sp = self.pr.speaker(r)
            it['who'] = r['who'] + (f' ({sp})' if sp and sp != r['who'] else '')
        if m.legend:
            it['words'] = m.legend
        lines = e['src'].count('\n') + 1
        if self.ctx and self.ctx.get('wtab') and doc.get('format') != 'atlas':
            try:
                lim, glines, _w = sheets.width_limit(doc, e, self.ctx)
                lines = glines or lines
                if self.ppc:
                    it['chars'] = max(4, int(lim / self.ppc))
            except Exception:                                   # noqa: BLE001
                pass
        it['lines'] = lines
        return it

    def _scene(self, r):
        """Попередні рядки того самого файлу (з перекладом, якщо є) — контекст для моделі."""
        out, n = [], r['n'] - 1
        while n >= 0 and len(out) < _CTX_ROWS:
            x = self.pr.rows[n]
            n -= 1
            if x['source'] != r['source']:
                break
            if x['kind'] == 'key' or not x['e']['src']:
                continue
            d = {'src': x['e']['src']}
            if x['who']:
                d['who'] = x['who']
            if x['e'].get('tr'):
                d['tr'] = x['e']['tr']
            out.append(d)
        return out[::-1]

    def _batches(self):
        out, cur, chars = [], [], 0
        for r in self.rows:
            n = len(r['e']['src'])
            if cur and (len(cur) >= self.eng.batch_rows or chars + n > self.eng.batch_chars):
                out.append(cur)
                cur, chars = [], 0
            cur.append(r)
            chars += n
        if cur:
            out.append(cur)
        return out

    # ------------------------------------------------------------ перевірка
    def _check(self, it, text):
        e = dict(it['r']['e'], tr=text)
        try:
            return sheets.check_entry(it['doc'], e, self.ctx, self.terms) if self.ctx else []
        except Exception:                                       # noqa: BLE001
            return []

    def _ask(self, items, scene):
        """Запит із поділом пакета навпіл, якщо відповідь не влізла."""
        try:
            return self.eng.translate(items, scene)
        except MTError as ex:
            if not ex.split or len(items) < 2:
                raise
        h = len(items) // 2
        return {**self._ask(items[:h], scene), **self._ask(items[h:], scene)}

    def _take(self, items, got):
        """Відповідь -> ({id: (текст, попередження)}, [завдання на повтор])."""
        done, again = {}, []
        for it in items:
            raw = got.get(it['id'])
            if not raw:
                again.append(dict(it, problem='немає перекладу цього рядка'))
                continue
            text, bad = it['m'].unmask(raw, self.eng.style)
            if bad:
                again.append(dict(it, retry=raw, problem=bad + ' — заглушки ⟦N⟧ мають лишитися як в оригіналі'))
                continue
            warn = self._check(it, text)
            hard = [w for w in warn if _RETRY.search(w)]
            if hard:
                again.append(dict(it, retry=raw, problem='; '.join(hard), _text=text, _warn=warn))
            done[it['id']] = (text, warn)
        return done, again

    # ------------------------------------------------------------ робота
    def run(self):
        batches = self._batches()
        total, n = len(self.rows), 0
        for b in batches:
            if self.cancel.is_set():
                self.post('log', 'Зупинено.')
                break
            items = [self._item(i, r) for i, r in enumerate(b)]
            scene = self._scene(b[0])
            try:
                got = self._ask(items, scene)
                done, again = self._take(items, got)
                if again and self.eng.style == 'br' and not self.cancel.is_set():
                    # мовна модель — ще раз лише проблемні, з поясненням, що не так
                    retry = [{k: v for k, v in it.items() if not k.startswith('_')} for it in again]
                    got2 = self._ask(retry, scene)
                    done2, again2 = self._take(retry, got2)
                    done.update(done2)
                    first = {it['id']: it for it in again}
                    for it in again2:           # і вдруге не вийшло: ширина — лишаємо з попередженням
                        it = it if '_text' in it else first.get(it['id'], {})
                        if '_text' in it:
                            done[it['id']] = (it['_text'], it['_warn'])
                else:
                    for it in again:
                        if '_text' in it:
                            done[it['id']] = (it['_text'], it['_warn'])
            except MTError as ex:
                self.failed += len(items)
                self.post('log', f'⚠ {ex}')
                if ex.fatal:
                    self.post('fatal', str(ex))
                    break
                n += len(b)
                self.post('progress', (n, total))
                continue
            res = []
            for it in items:
                if it['id'] in done:
                    text, warn = done[it['id']]
                    res.append((it['r']['k'], text))
                    self.ok += 1
                    if warn:
                        self.warned += 1
                else:
                    self.failed += 1
            self.post('result', res)
            n += len(b)
            self.post('progress', (n, total))
        self.post('done', (self.ok, self.failed, self.warned))
