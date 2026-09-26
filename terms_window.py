# -*- coding: utf-8 -*-
"""Вікно «Терміни»: глосарій гри й «як це вже перекладено в книгах».

Ліворуч — глосарій (glossary.py): пошук, додати/змінити/видалити, імпорт
імен з книг. Праворуч — усі рядки книг, де трапляється шукане слово, з
наявним перекладом: зручно згадати, як перекладав раніше (як підказки
Crowdin). Подвійний клік по такому рядку — перейти до нього (редактор або
книга Excel). Колонка «Терміни» в книгах оновлюється після «1».

Жанр гри (JRPG…) підключає базу загальних термінів з готовим перекладом
(glossary.genres); вони показані сірим, свій термін з тим самим словом їх перекриває.
"""
import os, threading
import tkinter as tk
from tkinter import ttk, messagebox

import glossary

LIMIT = 400                     # скільки рядків «як перекладено» показувати
NO_GENRE = '— немає —'


class TermsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        work, xl, _o, _b = app.dirs()
        self.work, self.xl = work, xl
        self.game = app.cur['game']
        self.where = {}                          # iid рядка праворуч -> (source, id, оригінал)
        import project
        self.project = app.get_project() if project.enabled(xl) else None
        self.terms = glossary.load(xl)
        self.corpus = None                       # [(оригінал, переклад, де)] — вантажиться у фоні
        self.pending = None
        c = _colors(app)
        self.title('Терміни — ' + os.path.basename(xl))
        self.geometry('1180x680')
        self.minsize(900, 480)
        self.configure(bg=c['bg'])
        self._build(c)
        self._fill_terms()
        threading.Thread(target=self._load_corpus, daemon=True).start()

    # ------------------------------------------------------------------ вигляд
    def _build(self, c):
        top = ttk.Frame(self, padding=(10, 10, 10, 4))
        top.pack(fill='x')
        ttk.Label(top, text='Пошук слова:').pack(side='left')
        self.q = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.q, width=34)
        e.pack(side='left', padx=6)
        e.focus_set()
        self.q.trace_add('write', lambda *_: self._changed())
        ttk.Label(top, text='англійською або українською: і в глосарії, і в перекладі книг',
                  style='Hint.TLabel').pack(side='left', padx=6)
        self.genre = tk.StringVar(value=glossary.genre(self.xl) or NO_GENRE)
        cb = ttk.Combobox(top, textvariable=self.genre, state='readonly', width=12,
                          values=[NO_GENRE] + glossary.genres())
        cb.pack(side='right')
        cb.bind('<<ComboboxSelected>>', lambda e: self._set_genre())
        ttk.Label(top, text='Жанр гри:').pack(side='right', padx=6)

        body = ttk.Panedwindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=10, pady=4)

        # --- глосарій
        left = ttk.Frame(body)
        body.add(left, weight=1)
        ttk.Label(left, text='Глосарій', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        tf = ttk.Frame(left)
        tf.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(tf, columns=('en', 'ua', 'note'), show='headings', selectmode='browse')
        for col, title, w in (('en', 'Англійською', 150), ('ua', 'Українською', 160), ('note', 'Примітка', 140)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=w)
        sb = ttk.Scrollbar(tf, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='left', fill='y')
        self.tree.bind('<<TreeviewSelect>>', lambda e: self._pick())
        self.tree.tag_configure('base', foreground=c.get('dim', '#6a6a6a'))

        form = ttk.Frame(left, padding=(0, 6, 0, 0))
        form.pack(fill='x')
        self.en, self.ua, self.note = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for r, (label, var) in enumerate((('Англійською:', self.en),
                                          ('Українською:', self.ua), ('Примітка:', self.note))):
            ttk.Label(form, text=label).grid(row=r, column=0, sticky='w', pady=2)
            ttk.Entry(form, textvariable=var).grid(row=r, column=1, sticky='ew', padx=6, pady=2)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text='кілька варіантів — через «/»: Нептун/Нептуна', style='Hint.TLabel').grid(
            row=3, column=1, sticky='w', padx=6)
        btns = ttk.Frame(left, padding=(0, 6, 0, 0))
        btns.pack(fill='x')
        ttk.Button(btns, text='Додати / зберегти', style='Accent.TButton', command=self._save).pack(side='left')
        ttk.Button(btns, text='Видалити', command=self._delete).pack(side='left', padx=6)
        ttk.Button(btns, text='Очистити поля', command=self._clear).pack(side='left')
        ttk.Button(btns, text='Додати імена з книг', command=self._import_names).pack(side='right')

        # --- як перекладено в книгах
        right = ttk.Frame(body, padding=(10, 0, 0, 0))
        body.add(right, weight=2)
        self.head = tk.StringVar(value='Як перекладено в книгах')
        ttk.Label(right, textvariable=self.head, font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        cf = ttk.Frame(right)
        cf.pack(fill='both', expand=True)
        self.conc = ttk.Treeview(cf, columns=('src', 'tr', 'where'), show='headings')
        for col, title, w in (('src', 'Оригінал', 300), ('tr', 'Переклад', 300), ('where', 'Де', 110)):
            self.conc.heading(col, text=title)
            self.conc.column(col, width=w)
        sb2 = ttk.Scrollbar(cf, orient='vertical', command=self.conc.yview)
        self.conc.configure(yscrollcommand=sb2.set)
        self.conc.pack(side='left', fill='both', expand=True)
        sb2.pack(side='left', fill='y')
        self.conc.bind('<Double-Button-1>', lambda e: self._go())
        self.conc.bind('<Return>', lambda e: self._go())
        self.conc.bind('<Button-3>', self._conc_menu)
        ttk.Label(right, text='Подвійний клік по рядку — перейти до нього й перекласти.',
                  style='Hint.TLabel').pack(anchor='w', pady=(4, 0))
        self.status = tk.StringVar(value='Читаю переклад з книг…')
        ttk.Label(right, textvariable=self.status, style='Hint.TLabel').pack(anchor='w', pady=(4, 0))

        bot = ttk.Frame(self, padding=(10, 4, 10, 10))
        bot.pack(fill='x')
        ttk.Label(bot, text='Глосарій зберігається одразу. Колонка «Терміни» в книгах і перевірка '
                            '(«термін … у перекладі не знайдено») оновляться після «1» / «Перевірити переклад».',
                  style='Hint.TLabel').pack(side='left')
        ttk.Button(bot, text='Закрити', command=self.destroy).pack(side='right')

    # ---------------------------------------------------------------- глосарій
    def _fill_terms(self):
        q = self.q.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        for k, t in enumerate(self.terms):
            if q and q not in t['en'].lower() and q not in (t.get('ua') or '').lower():
                continue
            note = t.get('примітка', '')
            if t.get('жанр'):
                note = f'база {t["жанр"]}' + (f': {note}' if note else '')
            self.tree.insert('', 'end', iid=str(k), values=(t['en'], t.get('ua', ''), note),
                             tags=('base',) if t.get('жанр') else ())

    def _pick(self):
        s = self.tree.selection()
        if not s:
            return
        t = self.terms[int(s[0])]
        self.en.set(t['en'])
        self.ua.set(t.get('ua', ''))
        self.note.set(t.get('примітка', ''))
        self._concordance(t['en'])

    def _store(self):
        try:
            glossary.save(self.xl, self.terms)
        except OSError as ex:
            messagebox.showerror('Не вдалося зберегти', str(ex), parent=self)
            return False
        self.terms = glossary.load(self.xl)
        self._fill_terms()
        self._refresh_editor()
        return True

    def _refresh_editor(self):
        """Відкритий редактор бере терміни при відкритті — оновити й у ньому."""
        win = getattr(self.app, 'editor_win', None)
        if win is not None and win.winfo_exists() and hasattr(win, 'reload_terms'):
            win.reload_terms()

    def _set_genre(self):
        name = '' if self.genre.get() == NO_GENRE else self.genre.get()
        try:
            glossary.set_genre(self.xl, name)
        except OSError as ex:
            messagebox.showerror('Не вдалося зберегти', str(ex), parent=self)
            return
        self.terms = glossary.load(self.xl)
        self._fill_terms()
        self._refresh_editor()
        n = sum(1 for t in self.terms if t.get('жанр'))
        self.status.set(f'Жанр: {name} — додано термінів з бази: {n}' if name else 'Базу жанру вимкнено.')

    def _save(self):
        en, ua = self.en.get().strip(), self.ua.get().strip()
        if not en:
            return
        # термін бази жанру не правимо — поруч з'являється свій, і він його перекриває
        t = next((x for x in self.terms if x['en'].lower() == en.lower() and not x.get('жанр')), None)
        if t is None:
            t = {'en': en}
            self.terms.append(t)
        t.update({'en': en, 'ua': ua})
        if self.note.get().strip():
            t['примітка'] = self.note.get().strip()
        else:
            t.pop('примітка', None)
        if self._store():
            self.status.set(f'Збережено: {en} → {ua}')

    def _delete(self):
        en = self.en.get().strip().lower()
        if not any(x['en'].lower() == en and not x.get('жанр') for x in self.terms):
            if any(x['en'].lower() == en for x in self.terms):
                messagebox.showinfo('Терміни', 'Це термін з бази жанру — його не видалити. '
                                    'Щоб перекладати інакше, впиши свій переклад і натисни '
                                    '«Додати / зберегти»: свій термін перекриє термін бази.',
                                    parent=self)
            return
        n = len(self.terms)
        self.terms = [x for x in self.terms if x['en'].lower() != en or x.get('жанр')]
        if len(self.terms) != n and self._store():
            self._clear()

    def _clear(self):
        for v in (self.en, self.ua, self.note):
            v.set('')

    def _to_form(self):
        """Шукане слово — в поле «Англійською» (щоб додати в глосарій)."""
        if not self.en.get().strip():
            self.en.set(self.q.get().strip())

    def _go(self):
        """Подвійний клік по рядку: відкрити його для перекладу — у редакторі
        або в книзі Excel (як клік по попередженню в головному вікні)."""
        s = self.conc.selection()
        if not s or s[0] not in self.where:
            return
        source, eid, src = self.where[s[0]]
        self.app._goto((self.game, source, eid, src))

    def _conc_menu(self, ev):
        iid = self.conc.identify_row(ev.y)
        if iid:
            self.conc.selection_set(iid)
        m = tk.Menu(self, tearoff=0)
        m.add_command(label='Перейти до рядка', command=self._go,
                      state='normal' if iid else 'disabled')
        m.add_command(label='Шукане слово — у глосарій', command=self._to_form)
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()

    def _import_names(self):
        """Імена мовців з книг (з уже перекладеними) — у глосарій, якщо їх там ще немає."""
        import sheets
        have = {t['en'].lower() for t in self.terms}
        added = 0
        for source, entries in sheets.collect(self.work):
            for e, _scene, who, kind in sheets._rows(source, entries):
                is_name = kind == 'name' or str(who).startswith('IDS_EVT_TITLE_NAME_SUB')
                src, tr = (e.get('src') or '').strip(), (e.get('tr') or '').strip()
                if is_name and src and tr and src.lower() not in have and len(src) < 40:
                    self.terms.append({'en': src, 'ua': tr, 'примітка': "ім'я"})
                    have.add(src.lower())
                    added += 1
        if added and self._store():
            self.status.set(f'Додано імен: {added}')
        elif not added:
            self.status.set('Нових перекладених імен немає.')

    # -------------------------------------------------------- як перекладено
    def _load_corpus(self):
        """[(оригінал, переклад, де, source, id)]. У режимі редактора переклад —
        з проєкту (там найсвіжіший, з незбереженими ще правками), інакше — з work."""
        import sheets
        rows = []
        try:
            pr = self.project
            if pr is not None:
                for r in pr.rows:
                    e = r['e']
                    if e.get('src'):
                        rows.append((e['src'], e.get('tr') or '', sheets._scene(r['source']),
                                     r['source'], e['id']))
            else:
                for source, entries in sheets.collect(self.work):
                    where = sheets._scene(source)
                    for e in entries:
                        if e.get('src'):
                            rows.append((e['src'], e.get('tr') or '', where, source, e['id']))
        except Exception as ex:                                       # noqa: BLE001
            self.after(0, lambda: self.status.set(f'Не вдалося прочитати: {ex}'))
            return
        self.corpus = rows
        self.after(0, lambda: (self.status.set(f'Рядків у книгах: {len(rows)}. Введи слово для пошуку.'),
                               self._changed(0)))

    def _changed(self, delay=250):
        self._fill_terms()
        if self.pending:
            self.after_cancel(self.pending)
        self.pending = self.after(delay, lambda: self._concordance(self.q.get().strip()))

    def _concordance(self, word):
        self.pending = None
        if not self.winfo_exists():
            return
        self.conc.delete(*self.conc.get_children())
        if self.corpus is None or len(word) < 2:
            self.head.set('Як перекладено в книгах')
            return
        rx = glossary.pattern(word)
        low = word.lower()
        hits = [r for r in self.corpus if rx.search(r[0]) or (r[1] and low in r[1].lower())]
        hits.sort(key=lambda r: (not r[1], len(r[0])))          # спершу перекладені, коротші
        self.where = {}
        for src, tr, where, source, eid in hits[:LIMIT]:
            iid = self.conc.insert('', 'end', values=(src.replace('\n', ' ⏎ '),
                                                      tr.replace('\n', ' ⏎ ') or '—', where))
            self.where[iid] = (source, eid, src)
        done = sum(1 for r in hits if r[1])
        self.head.set(f'Як перекладено в книгах: «{word}»')
        self.status.set(f'Знайдено рядків: {len(hits)}, з них перекладено {done}'
                        + (f' (показано перші {LIMIT})' if len(hits) > LIMIT else ''))


def _colors(app):
    import importlib
    mod = importlib.import_module('__main__')
    themes = getattr(mod, 'THEMES', None) or {'light': {'bg': '#fafafa'}}
    return themes.get(getattr(app, 'theme', 'light'), next(iter(themes.values())))
