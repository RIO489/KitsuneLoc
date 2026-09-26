# -*- coding: utf-8 -*-
"""Переклад гри як «проєкт» у програмі — замість книг Excel (з версії 1.7).

Джерело правди — один файл `Переклад\\<гра>\\переклад.json` (тека перекладача,
поруч із книгами):
    {"версія": 1,
     "рядки": {"<source>\\t<id>": {"tr": "...", "note": "...", "окремо": true}},
     "групи": [{"назва": "Розділ 1 — Сцена 1", "рядки": ["<source>\\t<id>", ...]}],
     "excel": "2026-09-26 12:00"}      # коли востаннє вивантажували / читали книги
Зберігаються лише рядки, де щось є. Робочі JSON у `work\\` — дзеркало: після
кожного збереження туди пишеться той самий переклад, тож «2», перевірка,
терміни й написи працюють як раніше.

Повтори: рядки з однаковим оригіналом (і того самого виду: репліка, ім'я
мовця, службовий ключ) «пов'язані» — переклад одного йде в усі. Позначка
"окремо" відв'язує рядок: у нього свій переклад.

Групи — як у Crowdin: довільні назви, рядок може бути в кількох групах,
сам текст нікуди не переноситься. Поруч — автоматичне дерево «за файлами
гри» (розкладка колишніх книг, sheets.DEFAULT_BOOKS).
"""
import collections, datetime, json, os, re

import common as locfile
import sheets

STORE = 'переклад.json'
SNAPSHOT = '_excel_знімок.json'     # у work\<гра>: що було в книгах на час вивантаження
VERSION = 1


def store_path(xl_dir):
    return os.path.join(xl_dir, STORE)


def enabled(xl_dir):
    """Чи гра вже перейшла на редактор (є файл проєкту)."""
    return os.path.exists(store_path(xl_dir))


def now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M')


class Project:
    """Усі рядки гри з work\\ + переклад із переклад.json."""

    def __init__(self, game, work_dir, xl_dir):
        self.game, self.work, self.xl = game, work_dir, xl_dir
        self.docs = {}                  # source -> (шлях JSON, doc)
        self.rows = []                  # у порядку гри
        self.by_key = {}
        self.links = collections.defaultdict(list)     # (вид, оригінал) -> [ключі]
        self.dirty = set()              # source, які треба переписати в work
        self.meta = {'версія': VERSION, 'групи': []}
        self._load_work()
        if enabled(xl_dir):
            self._load_store()

    # ------------------------------------------------------------ читання
    def _load_work(self):
        rules = [(re.compile(rx), name) for rx, name in sheets.book_rules(self.game)]
        for p in sorted(locfile.walk(self.work)):
            doc = locfile.load_json(p)
            if not doc:
                continue
            source = doc['source']
            self.docs[source] = (p, doc)
            book = next((n for rx, n in rules if rx.search(source)), sheets.OTHER_BOOK)
            for e, scene, who, kind in sheets._rows(source, doc['entries']):
                k = f'{source}\t{e["id"]}'
                r = {'k': k, 'source': source, 'e': e, 'kind': kind, 'scene': scene,
                     'who': who, 'book': sheets.NAMES_BOOK if kind == 'name' else book,
                     'n': len(self.rows)}
                self.rows.append(r)
                self.by_key[k] = r
                self.links[(kind, e['src'])].append(k)

    def _load_store(self):
        with open(store_path(self.xl), encoding='utf-8') as f:
            st = json.load(f)
        self.meta = {k: v for k, v in st.items() if k != 'рядки'}
        self.meta.setdefault('групи', [])
        saved = st.get('рядки', {})
        for k, r in self.by_key.items():
            got = saved.get(k, {})
            e = r['e']
            tr, note = got.get('tr', ''), got.get('note', '')
            if e.get('tr', '') != tr or e.get('note', '') != note:
                self.dirty.add(r['source'])
            e['tr'] = tr
            if note:
                e['note'] = note
            else:
                e.pop('note', None)
            e.pop('auto', None)             # «перевір» з книг тут не потрібне
            r['окремо'] = bool(got.get('окремо'))

    # ------------------------------------------------------------ рядки
    def tr(self, k):
        return self.by_key[k]['e'].get('tr', '')

    def lk(self, k):
        r = self.by_key[k]
        return (r['kind'], r['e']['src'])

    def linked(self, k):
        """Ключі, які отримають той самий переклад, що й k (разом із ним)."""
        r = self.by_key[k]
        if r.get('окремо'):
            return [k]
        return [x for x in self.links[self.lk(k)] if not self.by_key[x].get('окремо')]

    def twins(self, k):
        """Усі рядки з тим самим оригіналом (і пов'язані, і відв'язані)."""
        return self.links[self.lk(k)]

    def set_tr(self, k, text):
        """Записати переклад; повертає список змінених ключів."""
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        text = text.strip()                 # як із книг: краї повертає імпорт (_edges)
        changed = []
        for x in self.linked(k):
            e = self.by_key[x]['e']
            if e.get('tr', '') != text:
                e['tr'] = text
                e.pop('auto', None)
                self.dirty.add(self.by_key[x]['source'])
                changed.append(x)
        self._names_changed(changed)
        return changed

    def set_note(self, k, text):
        e = self.by_key[k]['e']
        text = text.strip()
        if e.get('note', '') == text:
            return False
        if text:
            e['note'] = text
        else:
            e.pop('note', None)
        self.dirty.add(self.by_key[k]['source'])
        return True

    def detach(self, keys):
        for k in keys:
            self.by_key[k]['окремо'] = True
        self._touched = True
        self._names_changed(keys)

    def attach(self, keys):
        """Повернути до спільного перекладу однакових рядків."""
        changed = []
        for k in keys:
            r = self.by_key[k]
            if not r.get('окремо'):
                continue
            r['окремо'] = False
            shared = next((self.tr(x) for x in self.linked(k) if x != k and self.tr(x)), None)
            if shared is not None and self.tr(k) != shared:
                r['e']['tr'] = shared
                self.dirty.add(r['source'])
                changed.append(k)
        self._touched = True
        self._names_changed(keys)
        return changed

    # ------------------------------------------------------------ групи
    def groups(self):
        return self.meta['групи']

    def group(self, name):
        return next((g for g in self.groups() if g['назва'] == name), None)

    def add_to_group(self, name, keys):
        g = self.group(name)
        if g is None:
            g = {'назва': name, 'рядки': []}
            self.groups().append(g)
        have = set(g['рядки'])
        g['рядки'] += [k for k in keys if k not in have and k in self.by_key]
        g['рядки'].sort(key=lambda k: self.by_key[k]['n'])
        self._touched = True
        return g

    def remove_from_group(self, name, keys):
        g = self.group(name)
        if g:
            drop = set(keys)
            g['рядки'] = [k for k in g['рядки'] if k not in drop]
            self._touched = True

    def rename_group(self, old, new):
        g = self.group(old)
        if g and not self.group(new):
            g['назва'] = new
            self._touched = True

    def delete_group(self, name):
        self.meta['групи'] = [g for g in self.groups() if g['назва'] != name]
        self._touched = True

    def group_rows(self, name):
        g = self.group(name)
        return [self.by_key[k] for k in (g['рядки'] if g else []) if k in self.by_key]

    # ------------------------------------------------------------ дерево «за файлами»
    def books(self):
        """{книга: {сцена/файл: [рядки]}} — як були книги Excel; імена — по одному."""
        out = collections.OrderedDict()
        seen_names = set()
        for r in sorted(self.rows, key=lambda r: (r['book'], r['n'])):
            if r['kind'] == 'name':
                if r['e']['src'] in seen_names:
                    continue
                seen_names.add(r['e']['src'])
                out.setdefault(r['book'], collections.OrderedDict()).setdefault('', []).append(r)
                continue
            out.setdefault(r['book'], collections.OrderedDict()).setdefault(r['scene'], []).append(r)
        return out

    def speaker(self, r):
        """Ім'я мовця репліки — перекладене, якщо вже є."""
        who = r['who']
        if not who:
            return ''
        # кеш: «Jack» — тисячі рядків-імен; перебирати їх на кожну репліку — десятки секунд
        cache = self.__dict__.setdefault('_spk', {})
        got = cache.get(who)
        if got is None:
            got = next((t for t in map(self.tr, self.links.get(('name', who), [])) if t), who)
            cache[who] = got
        return got

    def _names_changed(self, keys=None):
        """Переклад імені мовця змінився — скинути кеш speaker()."""
        if keys is None or any(self.by_key[k]['kind'] == 'name' for k in keys):
            self.__dict__.pop('_spk', None)

    # ------------------------------------------------------------ прогрес
    def progress(self):
        """Як sheets.progress: по «книгах» (імена — кожне один раз; службові ключі не рахуються)."""
        books = []
        done = total = 0
        for name, scenes in self.books().items():
            rows = [r for rs in scenes.values() for r in rs if r['kind'] != 'key']
            if not rows:
                continue
            d = sum(1 for r in rows if r['e'].get('tr'))
            books.append({'book': name, 'done': d, 'total': len(rows), 'auto': 0})
            done += d
            total += len(rows)
        return {'books': books, 'done': done, 'total': total, 'auto': 0, 'prev': None}

    # ------------------------------------------------------------ зберігання
    def save(self):
        """переклад.json (атомарно) + змінені документи в work\\."""
        rows = {}
        for k, r in self.by_key.items():
            e = r['e']
            d = {}
            if e.get('tr'):
                d['tr'] = e['tr']
            if e.get('note'):
                d['note'] = e['note']
            if r.get('окремо'):
                d['окремо'] = True
            if d:
                rows[k] = d
        st = dict(self.meta)
        st['версія'] = VERSION
        st['рядки'] = rows
        os.makedirs(self.xl, exist_ok=True)
        p = store_path(self.xl)
        with open(p + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(p + '.tmp', p)
        self.sync_work()
        self._touched = False

    def sync_work(self):
        """Переписати в work\\ документи, у яких змінився переклад."""
        for source in sorted(self.dirty):
            p, doc = self.docs[source]
            with open(p + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(doc, f, ensure_ascii=False, indent=1)
            os.replace(p + '.tmp', p)
        self.dirty.clear()

    def pending(self):
        return bool(self.dirty) or getattr(self, '_touched', False)

    # ------------------------------------------------------------ перехід з книг
    def relink(self):
        """Після переходу з книг: однакові рядки з різним перекладом — відв'язати,
        порожні однакові — заповнити спільним перекладом. Повертає (заповнено, відв'язано)."""
        filled = detached = 0
        for lk, keys in self.links.items():
            live = [k for k in keys if not self.by_key[k].get('окремо')]
            vals = collections.Counter(self.tr(k) for k in live if self.tr(k))
            if not vals:
                continue
            shared = vals.most_common(1)[0][0]
            for k in live:
                t = self.tr(k)
                if not t:
                    self.by_key[k]['e']['tr'] = shared
                    self.dirty.add(self.by_key[k]['source'])
                    filled += 1
                elif t != shared:
                    self.by_key[k]['окремо'] = True
                    detached += 1
        self._names_changed()
        return filled, detached

    @classmethod
    def create(cls, game, work_dir, xl_dir):
        """Перший перехід: переклад із work\\ (туди його щойно прочитали з книг)."""
        pr = cls(game, work_dir, xl_dir)
        for r in pr.rows:
            r['e'].pop('auto', None)
        stats = pr.relink()
        pr.meta['excel'] = now()
        pr.dirty.update(pr.docs)
        pr.save()
        pr.save_snapshot()
        return pr, stats

    # --------------------------------------------------------- книги Excel
    def _snap_path(self):
        return os.path.join(self.work, SNAPSHOT)

    def read_books(self, progress=None):
        """Що зараз написано в книгах Excel: {ключ: (переклад, примітка)} (work\\ не чіпаємо)."""
        out = {}
        if not os.path.isdir(self.xl):
            return out
        books = sorted(f for f in os.listdir(self.xl) if f.endswith('.xlsx') and not f.startswith('~$'))
        for i, fn in enumerate(books):
            for src, eid, tr, _au, note in sheets._read_book(os.path.join(self.xl, fn)):
                if src == sheets.NAMES:                  # книга «Імена»: одне ім'я — усі репліки
                    keys = self.links.get(('name', eid), [])
                else:
                    keys = [f'{src}\t{eid}']
                for k in keys:
                    if k in self.by_key and (k not in out or (tr and not out[k][0])):
                        out[k] = (tr, note or '')
            if progress:
                progress(i + 1, len(books), fn)
        return out

    def save_snapshot(self, books=None):
        """Знімок книг Excel (щойно вивантажили чи прочитали) — з ним порівнює load_excel."""
        books = self.read_books() if books is None else books
        p = self._snap_path()
        with open(p + '.tmp', 'w', encoding='utf-8') as f:
            json.dump({k: list(v) for k, v in books.items() if v[0] or v[1]}, f, ensure_ascii=False)
        os.replace(p + '.tmp', p)

    def load_excel(self, progress=None):
        """«Завантажити з Excel»: узяти з книг лише те, що в них змінили після
        вивантаження (порівняння зі знімком книг), — стара книга не затре новіший
        переклад із програми. Змінене в одному з пов'язаних рядків іде в усі
        пов'язані; різні нові переклади однакових рядків — ті рядки відв'язуються.
        Повертає к-сть змінених рядків."""
        try:
            with open(self._snap_path(), encoding='utf-8') as f:
                snap = json.load(f)
        except (OSError, ValueError):
            snap = {}
        fresh = self.read_books(progress)
        norm = lambda t: (t or '').replace('\r\n', '\n').strip()
        changed, n = {}, 0
        for k, (tr, note) in fresh.items():
            was = snap.get(k, ['', ''])
            if norm(note) != norm(was[1]) and self.set_note(k, note):
                n += 1
            if norm(tr) != norm(was[0]) and norm(tr) != self.tr(k):
                changed[k] = norm(tr)
        groups = collections.defaultdict(list)
        for k in changed:
            groups[self.lk(k) if not self.by_key[k].get('окремо') else ('окремо', k)].append(k)
        for g, keys in groups.items():
            vals = {changed[k] for k in keys}
            if g[0] != 'окремо' and len(vals) == 1:
                n += len(self.set_tr(keys[0], changed[keys[0]]))
            else:
                for k in keys:
                    self.by_key[k]['окремо'] = True
                    self.by_key[k]['e']['tr'] = changed[k]
                    self.dirty.add(self.by_key[k]['source'])
                    n += 1
        self.meta['excel'] = now()
        self._touched = True
        self.save()
        self._names_changed()
        self.save_snapshot(fresh)
        return n

    def export_excel(self, progress=None):
        """Вивантажити книги Excel (копія для перегляду/передачі)."""
        self.sync_work()
        made = sheets.export(self.work, self.xl, self.game, progress=progress)
        self.meta['excel'] = now()
        self._touched = True
        self.save()
        self.save_snapshot()
        return made

    def excel_newer(self):
        """Книги Excel, змінені після останнього вивантаження/читання."""
        stamp = self.meta.get('excel')
        if not stamp or not os.path.isdir(self.xl):
            return []
        t = datetime.datetime.strptime(stamp, '%Y-%m-%d %H:%M').timestamp() + 60
        return sorted(f for f in os.listdir(self.xl)
                      if f.endswith('.xlsx') and not f.startswith('~$')
                      and os.path.getmtime(os.path.join(self.xl, f)) > t)
