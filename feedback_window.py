# -*- coding: utf-8 -*-
"""Вікно «Повідомити про проблему»: звернення перекладача → тема в групі Telegram.

Програма сама додає версію, кінець логу й рядок, з якого відкрито вікно
(редактор, попередження в лозі, вікно написів). Скриншоти — клацнути знімок
зі Steam / Win+PrtSc, вставити з буфера (Ctrl+V) чи вибрати файл.
Надсилання — feedback.py (черга: без інтернету звернення піде пізніше).
"""
import os, threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import feedback

THUMB = 150


class FeedbackWindow(tk.Toplevel):
    """ctx (усе опційне): game, kind, row = {source, id, src, tr, warn, where},
    image — PIL-картинка (прев'ю напису), яку додати до знімків."""

    def __init__(self, app, ctx=None):
        super().__init__(app)
        self.app, self.ctx = app, ctx or {}
        self.title('Повідомити про проблему')
        self.geometry('760x780')
        self.minsize(640, 600)
        self.transient(app)
        self.shots = []          # [(шлях|PIL, вибрано: BooleanVar, мітка, фото)]
        self.sending = False
        self._build()
        self._fill_shots()
        self._restore_draft()
        self._key_state()
        self._validate()
        self.protocol('WM_DELETE_WINDOW', self._close)
        # Ctrl+V за будь-якої розкладки (keycode 86): картинку з буфера — у знімки;
        # текст у полі вставляє tkkeys, як завжди
        self.bind('<Control-KeyPress>', lambda e: self._paste() if e.keycode == 86 else None)
        try:
            self.app.dark_titlebar(self)
        except Exception:                                   # noqa: BLE001
            pass
        self.what.focus_set()

    # ------------------------------------------------------------------ вигляд
    def _build(self):
        games = self.app.GAMES
        top = ttk.Frame(self)
        top.pack(fill='x', padx=14, pady=(12, 0))
        ttk.Label(top, text='Пиши як є — версію програми, лог і рядок програма додасть сама.',
                  style='Hint.TLabel').pack(anchor='w')

        kf = ttk.Frame(self)
        kf.pack(fill='x', padx=14, pady=(10, 0))
        ttk.Label(kf, text='Що сталося:').pack(side='left')
        self.kind = tk.StringVar(value=self.ctx.get('kind', 'текст'))
        for k, (label, _c) in feedback.KINDS.items():
            ttk.Radiobutton(kf, text=label, value=k, variable=self.kind).pack(side='left', padx=(10, 0))

        gf = ttk.Frame(self)
        gf.pack(fill='x', padx=14, pady=(8, 0))
        ttk.Label(gf, text='Гра:').pack(side='left')
        self.game_names = {g['title']: k for k, g in games.items()}
        self.game_names['Програма загалом'] = ''
        g0 = self.ctx.get('game', self.app.game.get())
        self.game = tk.StringVar(value=games[g0]['title'] if g0 in games else 'Програма загалом')
        cb = ttk.Combobox(gf, textvariable=self.game, values=list(self.game_names), state='readonly',
                          width=28)
        cb.pack(side='left', padx=8)
        cb.bind('<<ComboboxSelected>>', lambda e: self._fill_shots())

        row = self.ctx.get('row')
        self.with_row = tk.BooleanVar(value=bool(row))
        if row:
            rf = ttk.LabelFrame(self, text=' Рядок ')
            rf.pack(fill='x', padx=14, pady=(10, 0))
            txt = f'{row.get("source", "")} [{row.get("id", "")}]'
            for label, key in (('Оригінал', 'src'), ('Переклад', 'tr'), ('Попередження', 'warn')):
                if row.get(key):
                    v = ' '.join(str(row[key]).split())
                    txt += f'\n{label}: {v[:200]}{"…" if len(v) > 200 else ""}'
            ttk.Label(rf, text=txt, wraplength=700, justify='left').pack(anchor='w', padx=10, pady=(6, 0))
            ttk.Checkbutton(rf, text='Додати цей рядок до звернення', variable=self.with_row).pack(
                anchor='w', padx=10, pady=(2, 8))

        nf = ttk.Frame(self)
        nf.pack(fill='x', padx=14, pady=(8, 0))
        ttk.Label(nf, text='Твоє ім\'я або нік:').pack(side='left')
        self.nick = tk.StringVar(value=feedback.load_nick())
        ttk.Entry(nf, textvariable=self.nick, width=24).pack(side='left', padx=8)
        ttk.Label(nf, text='(необов\'язково — щоб знати, кому дякувати)',
                  style='Hint.TLabel').pack(side='left')

        self.what = self._text('Що не так? (обов\'язково)', 5)
        self.what.bind('<KeyRelease>', lambda e: self._validate(), add='+')
        self.what.bind('<<Paste>>', lambda e: self.after(50, self._validate), add='+')
        self.how = self._text('Як мало бути? (якщо знаєш)', 3)

        wf = ttk.Frame(self)
        wf.pack(fill='x', padx=14, pady=(8, 0))
        ttk.Label(wf, text='Де в грі (екран, меню, сцена):').pack(side='left')
        self.where = tk.StringVar(value=self.ctx.get('where', ''))
        ttk.Entry(wf, textvariable=self.where, width=46).pack(side='left', padx=8)

        # --- знімки ---------------------------------------------------------
        sf = ttk.LabelFrame(self, text=' Скриншоти — клацни, щоб додати ')
        sf.pack(fill='both', expand=True, padx=14, pady=(10, 0))
        bar = ttk.Frame(sf)
        bar.pack(fill='x', padx=8, pady=(6, 2))
        ttk.Button(bar, text='Вставити з буфера (Ctrl+V)', command=self._paste).pack(side='left')
        ttk.Button(bar, text='Вибрати файл…', command=self._pick).pack(side='left', padx=6)
        ttk.Button(bar, text='Оновити', command=self._fill_shots).pack(side='left')
        self.shot_info = tk.StringVar()
        ttk.Label(bar, textvariable=self.shot_info, style='Hint.TLabel').pack(side='right')
        self.canvas = tk.Canvas(sf, height=THUMB + 40, highlightthickness=0, bd=0)
        hs = ttk.Scrollbar(sf, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=hs.set)
        self.canvas.pack(fill='both', expand=True, padx=8)
        hs.pack(fill='x', padx=8, pady=(0, 6))
        self.canvas.configure(bg=self.app.colors()['bg'])
        self.strip = tk.Frame(self.canvas, bd=0, bg=self.app.colors()['bg'])
        self.canvas.create_window(0, 0, window=self.strip, anchor='nw')
        self.strip.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Button-3>', lambda e: self._shot_menu(e))
        self.canvas.bind('<MouseWheel>', lambda e: self.canvas.xview_scroll(-1 if e.delta > 0 else 1, 'units'))

        # --- низ ------------------------------------------------------------
        tf = ttk.Frame(self)
        tf.pack(fill='x', padx=14, pady=(8, 0))
        self.tech = tk.BooleanVar(value=True)
        ttk.Checkbutton(tf, text='Додати технічне (версія, Windows, кінець логу)',
                        variable=self.tech).pack(side='left')
        ttk.Button(tf, text='Що саме піде?', command=self._show_details).pack(side='left', padx=8)

        self.keybar = ttk.Frame(self)
        self.keybar.pack(fill='x', padx=14, pady=(8, 0))
        self.key_info = tk.StringVar()
        ttk.Label(self.keybar, textvariable=self.key_info, style='Hint.TLabel',
                  wraplength=520, justify='left').pack(side='left')
        self.b_key = ttk.Button(self.keybar, text='Підключити…', command=self._connect)

        bot = ttk.Frame(self)
        bot.pack(fill='x', padx=14, pady=(8, 12))
        self.status = tk.StringVar()
        ttk.Label(bot, textvariable=self.status, wraplength=440, justify='left').pack(side='left')
        ttk.Button(bot, text='Скасувати', command=self._close).pack(side='right')
        self.b_send = ttk.Button(bot, text='Надіслати', command=self._send, style='Accent.TButton')
        self.b_send.pack(side='right', padx=8)

    def _text(self, label, height):
        f = ttk.Frame(self)
        f.pack(fill='x', padx=14, pady=(8, 0))
        ttk.Label(f, text=label).pack(anchor='w')
        c = self.app.colors()
        t = tk.Text(f, height=height, wrap='word', relief='flat', borderwidth=6, undo=True,
                    font=('Segoe UI', 10), bg=c['logbg'], fg=c['logfg'], insertbackground=c['fg'])
        t.pack(fill='x', pady=(2, 0))
        # Tab — до наступного поля, а не символ табуляції
        t.bind('<Tab>', lambda e: (e.widget.tk_focusNext().focus_set(), 'break')[1])
        return t

    # ------------------------------------------------------------------ знімки
    def _fill_shots(self):
        keep = [s for s in self.shots if not isinstance(s[0], str) or s[1].get()]
        for s in self.shots:
            s[2].destroy()
        self.shots = []
        for src, on in [(s[0], s[1].get()) for s in keep]:
            self._add_shot(src, on)
        if self.ctx.get('image') is not None and not any(s[0] is self.ctx['image'] for s in self.shots):
            self._add_shot(self.ctx['image'], True)
        have = {s[0] for s in self.shots if isinstance(s[0], str)}
        g = self.game_names.get(self.game.get(), '')
        steam = self.app.GAMES[g]['steam'] if g else ''
        dirs = feedback.shot_dirs(self.app.steam_libraries(), steam, g)
        for p in feedback.recent_shots(dirs):
            if p not in have:
                self._add_shot(p, False)
        self._count()

    def _add_shot(self, src, on, first=False):
        from PIL import Image, ImageTk
        try:
            im = src if not isinstance(src, str) else Image.open(src)
            th = im.convert('RGB')
            th.thumbnail((THUMB * 16 // 9, THUMB))
        except Exception:                                   # noqa: BLE001
            return
        var = tk.BooleanVar(value=on)
        c = self.app.colors()
        box = tk.Frame(self.strip, bd=0, highlightthickness=3)
        ph = ImageTk.PhotoImage(th)
        lbl = tk.Label(box, image=ph, bd=0, cursor='hand2')
        lbl.pack()
        name = os.path.basename(src) if isinstance(src, str) else 'з буфера / прев\'ю'
        cap = tk.Label(box, text=name[:28], font=('Segoe UI', 8), fg=c['dim'], bg=c['bg'])
        cap.pack(fill='x')
        item = (src, var, box, ph)
        for w in (box, lbl, cap):
            w.bind('<Button-1>', lambda e, it=item: self._toggle(it))
            w.bind('<Button-3>', lambda e, it=item: self._shot_menu(e, it))
            w.bind('<MouseWheel>', lambda e: self.canvas.xview_scroll(-1 if e.delta > 0 else 1, 'units'))
        if first and self.shots:
            box.pack(side='left', padx=4, pady=4, before=self.shots[0][2])
            self.shots.insert(0, item)
        else:
            box.pack(side='left', padx=4, pady=4)
            self.shots.append(item)
        self._mark(item)

    def _mark(self, item):
        c = self.app.colors()
        item[2].configure(highlightbackground=c['accent'] if item[1].get() else c['bg'],
                          highlightcolor=c['accent'] if item[1].get() else c['bg'], bg=c['bg'])

    def _toggle(self, item):
        item[1].set(not item[1].get())
        self._mark(item)
        self._count()

    def _count(self):
        n = sum(1 for s in self.shots if s[1].get())
        self.shot_info.set(f'вибрано: {n}' if n else 'нічого не вибрано')

    def _shot_menu(self, ev, item=None):
        """Правий клік по знімку: прибрати зі списку / у Кошик. Якщо клацнуто по
        вибраному, а вибрано кілька, — дія над усіма вибраними."""
        chosen = [s for s in self.shots if s[1].get()]
        items = chosen if item is not None and item in chosen and len(chosen) > 1 else \
            ([item] if item is not None else [])
        files = [s for s in items if isinstance(s[0], str)]
        n = len(items)
        m = tk.Menu(self, tearoff=0)
        if items:
            what = 'знімок' if n == 1 else f'вибрані знімки ({n})'
            m.add_command(label=f'Прибрати {what} зі списку', command=lambda: self._hide(items))
            if files:
                m.add_command(label=f'Видалити з комп\'ютера (у Кошик)… ({len(files)})',
                              command=lambda: self._recycle(files))
            m.add_separator()
        rest = [s for s in self.shots if not s[1].get()]
        if rest:
            m.add_command(label=f'Прибрати всі невибрані ({len(rest)})', command=lambda: self._hide(rest))
        hidden = len(feedback.hidden_shots())
        if hidden:
            m.add_command(label=f'Повернути прибрані зі списку ({hidden})', command=self._unhide)
        if m.index('end') is None:
            return
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()

    def _drop(self, items):
        for s in items:
            s[2].destroy()
        self.shots = [s for s in self.shots if s not in items]
        if any(s[0] is self.ctx.get('image') for s in items):
            self.ctx['image'] = None            # прев'ю напису не повертати при «Оновити»
        self._count()

    def _hide(self, items):
        files = [s[0] for s in items if isinstance(s[0], str)]
        if files:
            try:
                feedback.hide_shots(files)
            except OSError as ex:
                self.status.set(f'Не вдалося запам\'ятати: {ex}')
        self._drop(items)
        self._fill_shots()                      # на місце прибраних — старші знімки
        self.status.set(f'Прибрано зі списку: {len(items)} (файли на диску не чіпали).')

    def _unhide(self):
        try:
            feedback.hide_shots(None, on=False)
        except OSError:
            pass
        self._fill_shots()
        self.status.set('Прибрані знімки знову в списку.')

    def _recycle(self, items):
        names = '\n'.join(os.path.basename(s[0]) for s in items[:10])
        more = f'\n…і ще {len(items) - 10}' if len(items) > 10 else ''
        if not messagebox.askyesno('Видалити знімки',
                                   f'Перемістити в Кошик ({len(items)}):\n\n{names}{more}\n\n'
                                   'Їх можна буде відновити з Кошика.', parent=self):
            return
        left = feedback.to_recycle_bin([s[0] for s in items])
        left = {os.path.normcase(p) for p in left}
        gone = [s for s in items if os.path.normcase(os.path.abspath(s[0])) not in left]
        self._drop(gone)
        self._fill_shots()
        self.status.set(f'У Кошику: {len(gone)}.' +
                        (f' Не вдалося: {len(left)} (файл зайнятий?).' if left else ''))

    def _paste(self):
        ims = feedback.clipboard_images()
        for im in ims:
            self._add_shot(im, True, first=True)
        if ims:
            self.canvas.xview_moveto(0)
            self._count()
            self.status.set(f'Додано з буфера: {len(ims)}.')

    def _pick(self):
        ps = filedialog.askopenfilenames(parent=self, title='Скриншоти',
                                         filetypes=[('Картинки', '*.png *.jpg *.jpeg *.bmp *.webp'),
                                                    ('Усі файли', '*.*')])
        for p in ps:
            self._add_shot(p, True, first=True)
        self.canvas.xview_moveto(0)
        self._count()

    # ------------------------------------------------------------------ звернення
    def _report(self):
        g = self.game_names.get(self.game.get(), '')
        r = {'тип': self.kind.get(), 'гра': g, 'назва гри': self.game.get() if g else '',
             'нік': ' '.join(self.nick.get().split())[:feedback.NICK_MAX],
             'що': self.what.get('1.0', 'end-1c').strip(),
             'як': self.how.get('1.0', 'end-1c').strip(), 'де': self.where.get().strip()}
        if self.with_row.get() and self.ctx.get('row'):
            r['рядок'] = self.ctx['row']
        if self.tech.get():
            r['технічне'] = feedback.technical(self.app.VERSION, self.game.get())
        return r

    def _show_details(self):
        r = dict(self._report(), створено='(час надсилання)')
        w = tk.Toplevel(self)
        w.title('Що піде в звернення')
        w.geometry('720x520')
        t = tk.Text(w, wrap='word', font=('Consolas', 9))
        sb = ttk.Scrollbar(w, command=t.yview)
        t.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        t.pack(fill='both', expand=True)
        n = sum(1 for s in self.shots if s[1].get())
        t.insert('1.0', feedback.details_text(r) + f'\n\n+ скриншотів: {n}')
        t.configure(state='disabled')

    def _send(self):
        if self.sending:
            return
        r = self._report()
        why = feedback.problem(r)
        if why:
            self.status.set(why)
            self.what.focus_set()
            return
        left = feedback.wait_left()
        if left:
            self.status.set(f'Попереднє звернення щойно пішло — зачекай ще {left} с.')
            return
        feedback.save_nick(r['нік'])
        images = [s[0] for s in self.shots if s[1].get()]
        try:
            d = feedback.enqueue(r, images)
        except Exception as ex:                             # noqa: BLE001
            messagebox.showerror('Звернення', f'Не вдалося зберегти звернення: {ex}', parent=self)
            return
        feedback.drop_draft()
        feedback.mark_sent_now()
        self._sent = True
        if not feedback.load_key():
            self.app.say('Звернення збережено; надішлеться, щойно підключиш відправку '
                         '(«Повідомити про проблему…» → «Підключити…»).', 'warn')
            self.destroy()
            return
        self.sending = True
        self.b_send.state(['disabled'])
        self.status.set('Надсилаю…')

        def job():
            key = feedback.load_key()
            try:
                link, err = feedback.send(d, key), None
            except feedback.Offline as ex:
                link, err = None, ('offline', str(ex))
            except feedback.TgError as ex:
                link, err = None, ('tg', str(ex))
            except Exception as ex:                         # noqa: BLE001
                link, err = None, ('tg', str(ex))
            self.app.q.put(('call', lambda: self._sent_done(link, err)))

        threading.Thread(target=job, daemon=True).start()

    def _sent_done(self, link, err):
        self.sending = False
        if err is None:
            self.app.say_sent(link)
            if self.winfo_exists():
                self.destroy()
            return
        kind, msg = err
        if kind == 'offline':
            self.app.say(f'Немає зв\'язку з Telegram ({msg}). Звернення збережено — '
                         'надішлю сам, щойно з\'явиться інтернет.', 'warn')
            if self.winfo_exists():
                self.destroy()
            return
        self.app.say(f'Telegram не прийняв звернення: {msg}. Воно лишилось у черзі — '
                     'надішлю ще раз при наступному запуску.', 'err')
        if self.winfo_exists():
            self.destroy()

    def _validate(self):
        """«Надіслати» доступна, лише коли в «Що не так» є що читати."""
        if self.sending:
            return
        why = feedback.problem({'що': self.what.get('1.0', 'end-1c')})
        self.b_send.state(['disabled'] if why else ['!disabled'])
        if why:
            self.status.set(why)
        elif self.status.get() in (feedback.problem({'що': ''}), feedback.problem({'що': 'а'})):
            self.status.set('')

    # ------------------------------------------------------------------ ключ
    def _key_state(self):
        key = feedback.load_key()
        if key:
            name = key.get('назва')
            self.key_info.set(f'Надсилається в групу Telegram «{name}».' if name else
                              'Надсилається в групу звернень у Telegram.')
            self.b_key.pack_forget()
            return
        found = feedback.find_key_file()
        if found:
            try:
                feedback.import_key(found)
                self.app.say(f'Підключив відправку звернень (знайшов {found}).', 'ok')
                return self._key_state()
            except ValueError:
                pass
        self.key_info.set('Відправку ще не підключено: попроси у власника файл «звернення.key» і '
                          'вибери його кнопкою праворуч. Поки що звернення збережеться й піде пізніше.')
        self.b_key.pack(side='right')

    def _connect(self):
        p = filedialog.askopenfilename(parent=self, title='Файл звернення.key від власника',
                                       filetypes=[('Ключ звернень', '*.key'), ('Усі файли', '*.*')])
        if p:
            try:
                feedback.import_key(p)
            except (ValueError, OSError) as ex:
                messagebox.showerror('Звернення', str(ex), parent=self)
                return
            self._key_state()
            return
        # власник: ключа ще немає зовсім — створити з токена бота
        if messagebox.askyesno('Звернення', 'Файлу ключа немає — створити його з токена бота?\n'
                                            '(це робить власник програми)', parent=self):
            SetupDialog(self, on_done=self._key_state)

    # ------------------------------------------------------------------ чернетка
    def _restore_draft(self):
        if self.ctx.get('row'):
            return                       # відкрито з рядка — інше звернення
        d = feedback.load_draft()
        if not d:
            return
        self.kind.set(d.get('тип', self.kind.get()))
        self.what.insert('1.0', d.get('що', ''))
        self.how.insert('1.0', d.get('як', ''))
        self.where.set(d.get('де', ''))
        self.status.set('Відновлено недописане звернення.')

    def _close(self):
        if not getattr(self, '_sent', False):
            what = self.what.get('1.0', 'end-1c').strip()
            how = self.how.get('1.0', 'end-1c').strip()
            if what or how:
                try:
                    feedback.save_draft({'тип': self.kind.get(), 'що': what, 'як': how,
                                         'де': self.where.get().strip()})
                except OSError:
                    pass
        self.destroy()


class SetupDialog(tk.Toplevel):
    """Для власника: токен бота → група → файл звернення.key (передати перекладачеві)."""

    def __init__(self, parent, on_done=None):
        super().__init__(parent)
        self.on_done = on_done
        self.title('Підключити бота')
        self.geometry('620x420')
        self.transient(parent)
        ttk.Label(self, text='1. Додай бота в групу адміністратором (право «Керувати темами»).\n'
                             '2. Напиши в групу будь-що (бот бачить лише свіжі повідомлення).\n'
                             '3. Встав токен від @BotFather і натисни «Знайти групи».',
                  justify='left').pack(anchor='w', padx=14, pady=(12, 6))
        f = ttk.Frame(self)
        f.pack(fill='x', padx=14)
        self.token = tk.StringVar()
        ttk.Entry(f, textvariable=self.token, show='•', width=52).pack(side='left')
        ttk.Button(f, text='Знайти групи', command=self._find).pack(side='left', padx=8)
        self.lb = tk.Listbox(self, height=6, activestyle='none')
        self.lb.pack(fill='both', expand=True, padx=14, pady=8)
        self.info = tk.StringVar()
        ttk.Label(self, textvariable=self.info, wraplength=580, justify='left').pack(anchor='w', padx=14)
        bot = ttk.Frame(self)
        bot.pack(fill='x', padx=14, pady=10)
        ttk.Button(bot, text='Скасувати', command=self.destroy).pack(side='right')
        self.b_ok = ttk.Button(bot, text='Зберегти ключ', command=self._save, style='Accent.TButton')
        self.b_ok.pack(side='right', padx=8)
        self.chats = []

    def _find(self):
        tok = self.token.get().strip()
        if not tok:
            return
        self.info.set('Питаю Telegram…')

        def job():
            try:
                res, err = feedback.find_chats(tok), None
            except Exception as ex:                         # noqa: BLE001
                res, err = None, str(ex)
            self.after(0, lambda: self._found(res, err))
        threading.Thread(target=job, daemon=True).start()

    def _found(self, res, err):
        if not self.winfo_exists():
            return
        if err:
            self.info.set(f'Не вдалося: {err}')
            return
        bot, chats = res
        self.chats = chats
        self.lb.delete(0, 'end')
        for cid, title, forum, topics in chats:
            notes = []
            if not forum:
                notes.append('теми вимкнено')
            if not topics:
                notes.append('бот не може створювати теми')
            self.lb.insert('end', f'{title}  ({cid})' + (f' — {", ".join(notes)}' if notes else ' — готово'))
        if chats:
            self.lb.selection_set(0)
            self.info.set(f'Бот @{bot}. Вибери групу й натисни «Зберегти ключ».')
        else:
            self.info.set(f'Бот @{bot} не бачить жодної групи. Додай його в групу, напиши там '
                          'будь-що й натисни «Знайти групи» ще раз.')

    def _save(self):
        s = self.lb.curselection()
        if not s:
            return
        cid, title, forum, topics = self.chats[s[0]]
        if (not forum or not topics) and not messagebox.askyesno(
                'Звернення', 'Без тем кожне звернення піде просто в загальний чат, а не окремою '
                             'темою. Усе одно зберегти?', parent=self):
            return
        feedback.save_key(self.token.get().strip(), cid, title)
        messagebox.showinfo('Звернення', f'Ключ збережено:\n{feedback.KEYFILE}\n\n'
                                         'Надішли цей файл перекладачеві (у git його немає).',
                            parent=self)
        if self.on_done:
            self.on_done()
        self.destroy()
