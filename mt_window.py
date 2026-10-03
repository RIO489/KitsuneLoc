# -*- coding: utf-8 -*-
"""Вікно «Машинний переклад…» редактора (з 3.0).

Перекладач вибирає, що перекласти (вибрані рядки / розділ дерева / уся гра), сервіс
(Claude, DeepL, Google) і пише вказівки до стилю; програма показує обсяг і орієнтовну
ціну, а після «Почати» перекладає у фоні (mt/job.py). Результат — у поле «Машинний»
рядків (Project.set_mt), не в переклад: у гру сам не йде, перекладач бере його кнопкою
«Взяти в переклад» і вичитує. Ключі — mt.key (mt/keys.py), вказівки —
`Переклад\\<гра>\\машинний.json`.
"""
import json, os, queue, threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from mt import engines, job, keys

SETTINGS = 'машинний.json'          # у теці перекладу гри: вказівки до стилю, сервіс, модель
SERVICES = (('claude', 'Claude (Anthropic)'), ('deepl', 'DeepL'), ('google', 'Google Translate'))
KEY_HELP = {'claude': 'ключ API — console.anthropic.com → API Keys (рахунок платний)',
            'deepl': 'ключ — deepl.com/your-account/keys (безкоштовний план: 500 тис. знаків на місяць)',
            'google': 'ключ API Google Cloud з увімкненим Cloud Translation API (платно за знаки)'}


class MTWindow(tk.Toplevel):
    def __init__(self, ed, keys_sel=None):
        super().__init__(ed)
        self.ed, self.pr = ed, ed.pr
        self.sel = list(keys_sel or ed.selected_keys())
        self.q, self.cancel, self.thread, self.eng = queue.Queue(), threading.Event(), None, None
        self.cfg = self._load_cfg()
        self.title('Машинний переклад')
        self.geometry('860x700')
        self.configure(bg=ed.c['bg'])
        self.transient(ed)
        pad = dict(padx=12, pady=(8, 0))

        f = ttk.LabelFrame(self, text='Що перекласти', padding=8)
        f.pack(fill='x', **pad)
        self.scope = tk.StringVar(value='sel' if len(self.sel) > 1 else 'node')
        node = ed.tree.item(ed.node, 'text') if ed.node in ed.nodes else 'Усі рядки'
        self.r_sel = ttk.Radiobutton(f, text='', value='sel', variable=self.scope, command=self._recount)
        self.r_sel.pack(anchor='w')
        self.r_node = ttk.Radiobutton(f, text='', value='node', variable=self.scope, command=self._recount)
        self.r_node.pack(anchor='w')
        self.r_all = ttk.Radiobutton(f, text='', value='all', variable=self.scope, command=self._recount)
        self.r_all.pack(anchor='w')
        self.node_name = node
        self.redo = tk.BooleanVar(value=False)
        self.with_tr = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text='і там, де машинний уже є (перекласти заново)', variable=self.redo,
                        command=self._recount).pack(anchor='w', pady=(6, 0))
        ttk.Checkbutton(f, text='і вже перекладені мною (машинний — поруч, для порівняння)',
                        variable=self.with_tr, command=self._recount).pack(anchor='w')

        f = ttk.LabelFrame(self, text='Сервіс', padding=8)
        f.pack(fill='x', **pad)
        row = ttk.Frame(f)
        row.pack(fill='x')
        self.svc = tk.StringVar(value=self.cfg.get('сервіс', 'claude'))
        for val, lab in SERVICES:
            ttk.Radiobutton(row, text=lab, value=val, variable=self.svc,
                            command=self._svc_changed).pack(side='left', padx=(0, 14))
        self.model_row = ttk.Frame(f)
        ttk.Label(self.model_row, text='Модель:').pack(side='left')
        labels = [lab for _m, lab, _a, _b in engines.CLAUDE_MODELS]
        cur = next((lab for m, lab, _a, _b in engines.CLAUDE_MODELS if m == self.cfg.get('модель')), labels[0])
        self.model = tk.StringVar(value=cur)
        cb = ttk.Combobox(self.model_row, textvariable=self.model, values=labels, state='readonly', width=34)
        cb.pack(side='left', padx=6)
        cb.bind('<<ComboboxSelected>>', lambda e: self._recount())
        kr = ttk.Frame(f)
        kr.pack(fill='x', pady=(6, 0))
        self.key_info = tk.StringVar()
        ttk.Label(kr, textvariable=self.key_info, style='Hint.TLabel').pack(side='left')
        ttk.Button(kr, text='Ключ…', command=self._set_key).pack(side='right')
        self.key_row = kr

        f = ttk.LabelFrame(self, text='Вказівки для перекладу (стиль, звертання, що лишати англійською)',
                           padding=8)
        f.pack(fill='x', **pad)
        self.style_t = tk.Text(f, height=4, wrap='word', relief='flat', font=('Segoe UI', 10),
                               bg=ed.c.get('panel', '#ffffff'), fg=ed.c.get('fg', '#1a1a1a'),
                               insertbackground=ed.c.get('fg', '#1a1a1a'))
        self.style_t.pack(fill='x')
        self.style_t.insert('1.0', self.cfg.get('вказівки', job.DEFAULT_STYLE))
        ttk.Label(f, text='Глосарій («Терміни…»), уже перекладені імена, мовець і попередні рядки сцени '
                         'додаються самі.', style='Hint.TLabel').pack(anchor='w', pady=(4, 0))

        self.info = tk.StringVar()
        ttk.Label(self, textvariable=self.info, wraplength=820, justify='left').pack(anchor='w', **pad)
        bf = ttk.Frame(self)
        bf.pack(fill='x', **pad)
        self.b_start = ttk.Button(bf, text='Почати', command=self._start, style='Accent.TButton')
        self.b_start.pack(side='left')
        self.b_stop = ttk.Button(bf, text='Зупинити', command=self._stop, state='disabled')
        self.b_stop.pack(side='left', padx=6)
        ttk.Button(bf, text='Закрити', command=self._close).pack(side='right')
        self.bar = ttk.Progressbar(self, mode='determinate')
        self.bar.pack(fill='x', **pad)
        self.log = tk.Text(self, height=10, wrap='word', relief='flat', font=('Segoe UI', 9), state='disabled',
                           bg=ed.c['bg'], fg=ed.c.get('fg', '#1a1a1a'))
        self.log.pack(fill='both', expand=True, padx=12, pady=8)
        self.protocol('WM_DELETE_WINDOW', self._close)
        self._svc_changed()
        self.after(100, self._poll)

    # ------------------------------------------------------------ налаштування гри
    def _cfg_path(self):
        return os.path.join(self.pr.xl, SETTINGS)

    def _load_cfg(self):
        try:
            with open(self._cfg_path(), encoding='utf-8') as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_cfg(self):
        self.cfg.update({'сервіс': self.svc.get(), 'модель': self._model_id(),
                         'вказівки': self.style_t.get('1.0', 'end-1c').strip()})
        try:
            with open(self._cfg_path(), 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def _model_id(self):
        return next((m for m, lab, _a, _b in engines.CLAUDE_MODELS if lab == self.model.get()),
                    engines.CLAUDE_MODELS[0][0])

    # ------------------------------------------------------------ обсяг
    def _rows(self):
        s = self.scope.get()
        if s == 'sel':
            src = [self.pr.by_key[k] for k in self.sel if k in self.pr.by_key]
        elif s == 'node':
            src = self.ed._node_rows(self.ed.node)
        else:
            src = self.pr.rows
        return job.pick(self.pr, src, redo=self.redo.get(), with_tr=self.with_tr.get())

    def _recount(self):
        n = lambda v: f'{v:,}'.replace(',', ' ')
        cnt = lambda rows: len(job.pick(self.pr, rows, self.redo.get(), self.with_tr.get()))
        sel = [self.pr.by_key[k] for k in self.sel if k in self.pr.by_key]
        self.r_sel.configure(text=f'вибрані рядки ({n(cnt(sel))})',
                             state='normal' if sel else 'disabled')
        self.r_node.configure(text=f'розділ дерева «{self.node_name}» ({n(cnt(self.ed._node_rows(self.ed.node)))})')
        self.r_all.configure(text=f'уся гра ({n(cnt(self.pr.rows))})')
        rows = self._rows()
        chars = sum(len(r['e']['src']) for r in rows)
        words = sum(self.pr.words(r) for r in rows)
        est = ''
        try:
            eng = self._engine(quiet=True)
            if eng is not None and rows:
                usd = eng.estimate(len(rows), chars)
                est = (' · орієнтовно ' + (f'${usd:.2f}' if usd >= 0.01 else 'менше $0.01')
                       if usd else ' · безкоштовно (у межах ліміту плану)')
        except engines.MTError as ex:
            est = f' · {ex}'
        self.info.set(f'До перекладу: {n(len(rows))} рядків (однакові — один раз), {n(words)} слів, '
                      f'{n(chars)} знаків{est}.\nМашинний іде в поле «Машинний», не в переклад — у гру '
                      'сам не потрапить.')

    def _svc_changed(self):
        s = self.svc.get()
        if s == 'claude':
            self.model_row.pack(fill='x', pady=(6, 0), before=self.key_row)
        else:
            self.model_row.pack_forget()
        k = keys.get(s)
        self.key_info.set((f'Ключ: {keys.shown(k)}' if k else 'Ключа ще немає — «Ключ…». ') + ' · ' + KEY_HELP[s])
        self._recount()

    def _set_key(self):
        s = self.svc.get()
        k = simpledialog.askstring('Ключ', f'Ключ для {dict(SERVICES)[s]}\n({KEY_HELP[s]}).\n'
                                   'Зберігається лише на цьому ПК (mt.key), порожньо — прибрати.',
                                   parent=self, show='•')
        if k is None:
            return
        keys.put(s, k)
        self._svc_changed()

    # ------------------------------------------------------------ сервіс
    def _engine(self, quiet=False):
        s = self.svc.get()
        if s == 'claude':
            if engines.anthropic_state() != 'ok':
                raise engines.MTError('для Claude потрібна бібліотека anthropic — «Почати» запропонує її встановити')
            games = getattr(self.ed.app, 'GAMES', None) or {}
            title = games.get(self.pr.game, {}).get('title', self.pr.game)
            return engines.Claude(self._model_id(), title, self.style_t.get('1.0', 'end-1c'),
                                  self.ed.terms, job.names(self.pr))
        if s == 'deepl':
            return engines.DeepL()
        return engines.Google()

    def _start(self):
        if self.thread is not None:
            return
        if self.ed.ctx is None:              # межі ширини редактор рахує у фоні
            self._say('Чекаю, поки редактор порахує межі ширини…')
            self.after(700, self._start)
            return
        rows = self._rows()
        if not rows:
            self._say('Нема чого перекладати: усе вибране вже має машинний чи свій переклад.')
            return
        if self.svc.get() == 'claude' and engines.anthropic_state() != 'ok':
            # бібліотека для Claude не обов'язкова (з 3.1) — ставимо, коли справді знадобилась
            if messagebox.askyesno('Машинний переклад', 'Для Claude потрібна бібліотека anthropic (~20 МБ, раз; '
                                   'потрібен інтернет). DeepL і Google — без неї.\n\nВстановити зараз?', parent=self):
                self._say('Встановлюю бібліотеку anthropic… (до хвилини)')

                def install():                  # (не «job»: так звати модуль mt.job)
                    ok, msg = engines.install_anthropic()
                    self.q.put(('log', f'Бібліотека anthropic: {msg}.' + (' Тепер «Почати».' if ok else '')))
                threading.Thread(target=install, daemon=True).start()
            return
        settings = getattr(self.ed.app, 'settings', {})
        if not settings.get('mt_consent'):
            if not messagebox.askyesno(
                    'Машинний переклад',
                    'Текст гри (оригінал, японська, ваші примітки, глосарій і вже перекладені імена) '
                    'буде надіслано вибраному сервісу перекладу в інтернеті.\n\nСервіси платні за обсяг '
                    '(крім безкоштовного ліміту DeepL) — гроші знімає сервіс з вашого рахунку.\n\nПогодитися?',
                    parent=self):
                return
            self.ed._save_setting('mt_consent', True)
        try:
            self.eng = self._engine()
        except engines.MTError as ex:
            self._say(f'⚠ {ex}')
            return
        self._save_cfg()
        self.ed._commit()
        self.cancel.clear()
        self.bar.configure(maximum=len(rows), value=0)
        self.b_start.configure(state='disabled')
        self.b_stop.configure(state='normal')
        self._say(f'Перекладаю {len(rows)} рядків через {self.eng.label}…')
        refs = self.ed.refs()
        j = job.Job(self.pr, self.eng, rows, self.ed.ctx, self.ed.terms, refs,
                    lambda kind, data: self.q.put((kind, data)), self.cancel)
        self.thread = threading.Thread(target=self._run, args=(j,), daemon=True)
        self.thread.start()

    def _run(self, j):
        try:
            j.run()
        except Exception as ex:                                 # noqa: BLE001
            self.q.put(('log', f'⚠ Помилка: {ex}'))
            self.q.put(('done', (j.ok, j.failed, j.warned)))

    def _stop(self):
        self.cancel.set()
        self._say('Зупиняю після поточного пакета…')

    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == 'result':
                    changed = []
                    for k, text in data:
                        changed += self.pr.set_mt(k, text, self.eng.label)
                    self.ed._update_rows(changed)
                    self.ed._later('save', 1500, self.ed._save)
                elif kind == 'progress':
                    self.bar.configure(value=data[0])
                    self._say(f'{data[0]} / {data[1]} · {self.eng.spent()}', replace=True)
                elif kind == 'log':
                    self._say(data)
                elif kind == 'fatal':
                    messagebox.showwarning('Машинний переклад', data, parent=self)
                elif kind == 'done':
                    self._finish(*data)
        except queue.Empty:
            pass
        self.after(150, self._poll)

    def _finish(self, ok, failed, warned):
        self.thread = None
        self.b_start.configure(state='normal')
        self.b_stop.configure(state='disabled')
        self.ed._save()
        self.ed._levels_state()
        self.ed._count(self.ed.view)
        self._say(f'Готово: перекладено {ok}' + (f', з попередженнями {warned}' if warned else '')
                  + (f', не вдалося {failed}' if failed else '') + f'. {self.eng.spent()}.\n'
                  'У редакторі: фільтр «Лише машинний», кнопка «Взяти в переклад» (Ctrl+M).')
        self._recount()

    def _say(self, msg, replace=False):
        self.log.configure(state='normal')
        if replace and getattr(self, '_last_progress', False):
            self.log.delete('end-2l', 'end-1c')
        self.log.insert('end', msg + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')
        self._last_progress = replace

    def _close(self):
        if self.thread is not None:
            if not messagebox.askyesno('Машинний переклад', 'Переклад ще йде. Зупинити й закрити?', parent=self):
                return
            self.cancel.set()
        self._save_cfg()
        self.destroy()
