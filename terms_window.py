# -*- coding: utf-8 -*-
"""Вікно «Терміни»: глосарій гри й «як це вже перекладено в книгах».

Ліворуч — глосарій (glossary.py): пошук, додати/змінити/видалити, імпорт
імен з книг. Праворуч — усі рядки книг, де трапляється шукане слово, з
наявним перекладом: зручно згадати, як перекладав раніше (як підказки
Crowdin). Колонка «Терміни» в книгах оновлюється після «1».
"""
import os, threading
import tkinter as tk
from tkinter import ttk, messagebox

import glossary

LIMIT = 400                     # скільки рядків «як перекладено» показувати


class TermsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        work, xl, _o, _b = app.dirs()
        self.work, self.xl = work, xl
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
        self.conc.bind('<Double-Button-1>', lambda e: self._to_form())
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
            self.tree.insert('', 'end', iid=str(k), values=(t['en'], t.get('ua', ''), t.get('примітка', '')))

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
        return True

    def _save(self):
        en, ua = self.en.get().strip(), self.ua.get().strip()
        if not en:
            return
        t = next((x for x in self.terms if x['en'].lower() == en.lower()), None)
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
        n = len(self.terms)
        self.terms = [x for x in self.terms if x['en'].lower() != en]
        if len(self.terms) != n and self._store():
            self._clear()

    def _clear(self):
        for v in (self.en, self.ua, self.note):
            v.set('')

    def _to_form(self):
        """Подвійний клік по рядку книги: шукане слово — в поле «Англійською»."""
        if not self.en.get().strip():
            self.en.set(self.q.get().strip())

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
        import sheets
        rows = []
        try:
            for source, entries in sheets.collect(self.work):
                where = sheets._scene(source)
                for e in entries:
                    if e.get('src'):
                        rows.append((e['src'], e.get('tr') or '', where))
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
        for src, tr, where in hits[:LIMIT]:
            self.conc.insert('', 'end', values=(src.replace('\n', ' ⏎ '), tr.replace('\n', ' ⏎ ') or '—', where))
        done = sum(1 for r in hits if r[1])
        self.head.set(f'Як перекладено в книгах: «{word}»')
        self.status.set(f'Знайдено рядків: {len(hits)}, з них перекладено {done}'
                        + (f' (показано перші {LIMIT})' if len(hits) > LIMIT else ''))


def _colors(app):
    import importlib
    mod = importlib.import_module('__main__')
    themes = getattr(mod, 'THEMES', None) or {'light': {'bg': '#fafafa'}}
    return themes.get(getattr(app, 'theme', 'light'), next(iter(themes.values())))
