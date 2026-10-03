#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Старе вікно KitsuneLoc (Tk) — запасне, поки нове (Qt, Переклад.pyw) обкатується.

Робоча логіка — core.py (клас Core); тут лише вигляд Tk.
"""
import os, re, sys, json, time, shutil, threading, traceback, subprocess, queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import themes                                                           # noqa: E402
from core import *                                                     # noqa: F401,F403
from core import Core                                                  # noqa: E402

# ---------------------------------------------------------------- теми вікна
# Теми — themes.py (з 3.0): «Світла»/«Темна» — Sun Valley (sv-ttk), решта — свої кольори
# на clam і прикраси. THEMES тут — для вікон, що беруть кольори з __main__ (editor.colors).
from themes import THEMES                                               # noqa: E402



def dark_titlebar(win, dark):
    """Темний заголовок вікна на Windows 10/11 (DWMWA_USE_IMMERSIVE_DARK_MODE)."""
    if os.name != 'nt':
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        v = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass


class App(Core, tk.Tk):
    def __init__(self):
        super().__init__()
        try:
            import tkkeys                # Ctrl+V/C/X/A за української розкладки + меню правої кнопки
            tkkeys.install(self)
        except Exception:
            pass
        self.settings = load_settings()
        self.theme = self.settings.get('theme', 'light')
        self.game = tk.StringVar(value=self.settings.get('last_game', 'crystar'))
        self.busy = False
        self.closing = False
        self.cancel = threading.Event()
        self.t0 = 0
        self.q = queue.Queue()      # фоновий потік -> вікно (tkinter не потокобезпечний)
        self.logf = None

        self.title(f'KitsuneLoc {VERSION} — Crystar / Mary Skelter / Neptunia')
        geo = self.settings.get('geometry')
        self.geometry(geo if geo else '1000x720')
        self.minsize(900, 660)
        self.protocol('WM_DELETE_WINDOW', self._on_close)

        self._build()
        self._apply_theme()
        self._refresh_path()
        self._open_log()
        self.say(f'KitsuneLoc, версія {VERSION}.', 'dim')
        self.after(80, self._poll)
        self.after(200, self._check_deps)
        self.after(3000, self._feedback_flush)

    # ------------------------------------------------------------------ вигляд
    def _build(self):
        pad = dict(padx=12, pady=(6, 0))

        # --- гра ---------------------------------------------------------
        gf = ttk.LabelFrame(self, text=' Гра ')
        gf.pack(fill='x', **pad)
        row = ttk.Frame(gf); row.pack(fill='x', padx=10, pady=(8, 2))
        for k, g in GAMES.items():
            ttk.Radiobutton(row, text=g['title'], value=k, variable=self.game,
                            command=self._switch_game).pack(side='left', padx=(0, 14))
        self.badge = ttk.Label(row, text='')
        self.badge.pack(side='right')

        self.slot_frame = ttk.Frame(gf); self.slot_frame.pack(fill='x', padx=10, pady=2)
        ttk.Label(self.slot_frame, text='Мова тексту в грі (куди писати переклад):').pack(side='left')
        self.slot = tk.StringVar(value=self.settings.get('crystar_slot', 'en'))
        for val, label in (('en', 'англійська'), ('ja', 'японська')):
            ttk.Radiobutton(self.slot_frame, text=label, value=val, variable=self.slot,
                            command=self._save_slot).pack(side='left', padx=8)

        # написи на картинках (текстури) — окремо для кожної гри; вимкнено — у грі оригінальні
        tf = ttk.Frame(gf); tf.pack(fill='x', padx=10, pady=2)
        self.textures = tk.BooleanVar(value=self._textures_on())
        ttk.Checkbutton(tf, text='Заливати в гру написи на картинках (текстури) і свої картинки',
                        variable=self.textures, command=self._save_textures).pack(side='left')
        # машинний переклад (чернетка, з 3.0) — у гру лише для пробного проходу; окремо для гри
        mf = ttk.Frame(gf); mf.pack(fill='x', padx=10, pady=2)
        self.mt_game = tk.BooleanVar(value=self._mt_in_game())
        ttk.Checkbutton(mf, text='Підставляти машинний переклад, де ще немає свого (пробний прохід)',
                        variable=self.mt_game, command=self._save_mt_game).pack(side='left')

        pf = ttk.Frame(gf); pf.pack(fill='x', padx=10, pady=(2, 10))
        ttk.Label(pf, text='Тека гри:').pack(side='left')
        self.path_var = tk.StringVar()
        ttk.Entry(pf, textvariable=self.path_var).pack(side='left', fill='x',
                                                      expand=True, padx=8)
        self.b_find = ttk.Button(pf, text='Знайти', command=self._autofind)
        self.b_find.pack(side='left', padx=(0, 6))
        self.b_pick = ttk.Button(pf, text='Обрати…', command=self._pick)
        self.b_pick.pack(side='left')

        # --- робота ------------------------------------------------------
        wf = ttk.LabelFrame(self, text=' Робота ')
        wf.pack(fill='x', **pad)
        steps = ttk.Frame(wf); steps.pack(fill='x', padx=10, pady=(8, 10))
        self.b1 = self._step_button(steps, '1. Дістати текст з гри', 'зібрати книги Excel',
                                    lambda: (self._flush_editor(), self._run(self.do_export, 'neptune.wav')), accent=True)
        self.b_open = self._step_button(steps, 'Відкрити теку з перекладом',
                                        'там лежать книги .xlsx', self.open_xlsx)
        self.b2 = self._step_button(steps, '2. Залити переклад у гру', 'з резервною копією',
                                    lambda: (self._flush_editor(), self._run(self.do_import, 'neptune-shy.wav')), accent=True)
        self.b3 = self._step_button(steps, '3. Запустити гру',
                                    'подивитись результат', self.launch)

        ed = ttk.Frame(wf); ed.pack(fill='x', padx=10, pady=(0, 8))
        self.b_editor = ttk.Button(ed, text='Редактор перекладу…', command=self.open_editor,
                                   style='Accent.TButton')
        self.b_editor.pack(side='left')
        # нагадування перекладача (reminders.py): знімки з «Гра на екрані» та ін.
        self.b_remind = ttk.Button(ed, text='Нагадування…', command=self.open_reminders)
        self.b_remind.pack(side='left', padx=8)
        self.reminders_win = None
        self.editor_hint = ttk.Label(ed, text='', style='Hint.TLabel')
        self.editor_hint.pack(side='left', padx=10)
        self.editor_win = None

        bk = ttk.Frame(wf); bk.pack(fill='x', padx=10, pady=(0, 10))
        ttk.Label(bk, text='Книга:').pack(side='left')
        self.book = tk.StringVar()
        self.book_files = {}                     # підпис у списку -> ім'я файлу
        self.book_pc = {}                        # шлях -> (час зміни, перекладено, усього)
        self.book_box = ttk.Combobox(bk, textvariable=self.book, state='readonly', width=40)
        self.book_box.pack(side='left', padx=8)
        self.b_book = ttk.Button(bk, text='Відкрити книгу', command=self.open_book)
        self.b_book.pack(side='left')
        self.b_pics = ttk.Button(bk, text='Написи на картинках (з прев\'ю)…',
                                 command=self.open_pics)
        self.b_pics.pack(side='left', padx=8)
        self.b_terms = ttk.Button(bk, text='Терміни…', command=self.open_terms)
        self.b_terms.pack(side='left')
        self.b_scan = ttk.Button(bk, text='Знайти написи…', command=self.open_scan)
        self.b_scan.pack(side='left', padx=8)

        # --- додатково ---------------------------------------------------
        ef = ttk.LabelFrame(self, text=' Додатково ')
        ef.pack(fill='x', **pad)
        row = ttk.Frame(ef); row.pack(fill='x', padx=10, pady=10)
        self.b_prog = ttk.Button(row, text='Мій прогрес',
                                 command=lambda: (self._flush_editor(), self._run(self.do_progress)))
        self.b_prog.pack(side='left')
        self.b_check = ttk.Button(row, text='Перевірити переклад',
                                  command=lambda: (self._flush_editor(), self._run(self.do_check)))
        self.b_check.pack(side='left', padx=8)
        self.b_rest = ttk.Button(row, text='Повернути оригінали гри',
                                 command=lambda: self._run(self.do_restore))
        self.b_rest.pack(side='left')
        self.b_inst = ttk.Button(row, text='Створити інсталятор…', command=self.make_installer)
        self.b_inst.pack(side='left', padx=(8, 0))
        self.b_report = ttk.Button(row, text='Повідомити про проблему…',
                                   command=lambda: self.open_feedback())
        self.b_report.pack(side='left', padx=8)
        self.b_copy = ttk.Button(row, text='Скопіювати лог', command=self.copy_log)
        self.b_copy.pack(side='right')
        self.theme_var = tk.StringVar(value=themes.get(self.theme)['title'])
        self.b_theme = ttk.Combobox(row, textvariable=self.theme_var, state='readonly', width=15,
                                    values=[themes.get(n)['title'] for n in themes.names()])
        self.b_theme.bind('<<ComboboxSelected>>', lambda e: self._set_theme(themes.by_title(self.theme_var.get())))
        self.b_theme.pack(side='right', padx=8)
        ttk.Label(row, text='Тема:').pack(side='right')
        self.sound = tk.BooleanVar(value=self.settings.get('sound', True))
        ttk.Checkbutton(row, text='Звуки', variable=self.sound,
                        command=self._save_sound).pack(side='right', padx=(0, 4))

        self.buttons = [self.b1, self.b2, self.b3, self.b_open, self.b_book, self.b_pics, self.b_editor,
                        self.b_prog, self.b_check, self.b_rest, self.b_inst,
                        self.b_find, self.b_pick, self.b_scan]

        # --- стан --------------------------------------------------------
        sf = ttk.Frame(self); sf.pack(fill='x', padx=12, pady=(10, 0))
        self.status = tk.StringVar(value='Готово.')
        ttk.Label(sf, textvariable=self.status).pack(side='left')
        self.elapsed = tk.StringVar(value='')
        ttk.Label(sf, textvariable=self.elapsed).pack(side='right')
        self.b_stop = ttk.Button(sf, text='Зупинити', command=self._stop)
        self.b_stop.pack(side='right', padx=8)
        self.b_stop.state(['disabled'])

        self.pb = ttk.Progressbar(self, mode='determinate')
        self.pb.pack(fill='x', padx=12, pady=(4, 6))

        lf = ttk.Frame(self); lf.pack(fill='both', expand=True, padx=12, pady=(0, 12))
        self.log = tk.Text(lf, height=18, wrap='word', state='disabled',
                           relief='flat', borderwidth=6)
        sb = ttk.Scrollbar(lf, orient='vertical', command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        # дівчата гри праворуч у журналі (chibi.py; з'являються після першого «1»)
        self.chibi = tk.Label(lf, bd=0, anchor='s')
        self.chibi.pack(side='right', fill='y')
        self.chibi_photo = None
        self.log.pack(side='left', fill='both', expand=True)
        self.links = {}                          # тег у лозі -> (гра, source, id, оригінал, …)
        self.links_shown = {}                    # тег -> чи показано як затверджене
        self.deco_rings = [gf, wf, ef, lf]       # світні рамки навколо розділів (themes.Deco)

    def _step_button(self, parent, text, hint, cmd, accent=False):
        box = ttk.Frame(parent); box.pack(side='left', padx=(0, 10))
        b = ttk.Button(box, text=text, command=cmd)
        if accent:
            b.configure(style='Accent.TButton')
        b.pack(fill='x')
        ttk.Label(box, text=hint, style='Hint.TLabel').pack(anchor='w', pady=(2, 0))
        return b

    def _apply_theme(self):
        if self.theme not in THEMES:
            self.theme = themes.DEFAULT
        c = themes.apply(self, self.theme)
        dark_titlebar(self, themes.is_dark(self.theme))
        self.configure(bg=c['bg'])
        self.theme_var.set(c['title'])
        # зоряне тло між розділами й світні рамки навколо них (лише в темах з прикрасами)
        if getattr(self, 'deco', None) is None:
            self.deco = themes.Deco(self, self.theme)
            self.deco.frame(self)
            for w in self.deco_rings:
                self.deco.around(w)
        else:
            self.deco.retheme(self.theme)

        self.log.configure(bg=c['logbg'], fg=c['logfg'], insertbackground=c['fg'],
                           font=('Segoe UI', 10))
        self.log.tag_configure('mono', font=('Consolas', 9))
        self.log.tag_configure('err', foreground=c['err'])
        self.log.tag_configure('warn', foreground=c['warn'])
        self.log.tag_configure('ok', foreground=c['ok'])
        self.log.tag_configure('dim', foreground=c['dim'])
        self.log.tag_configure('head', foreground=c['accent'], font=('Segoe UI', 10, 'bold'))
        # попередження, що ведуть до рядка книги: підкреслені, клік відкриває книгу
        self.log.tag_configure('link', underline=True)
        self.log.tag_bind('link', '<Enter>', lambda e: self.log.configure(cursor='hand2'))
        self.log.tag_bind('link', '<Leave>', lambda e: self.log.configure(cursor=''))
        self.chibi.configure(bg=c['logbg'])

    def _set_theme(self, name):
        if name == self.theme:
            return
        # sv-ttk <-> clam на ходу лишає частині підписів старий фон — тема ляже після перезапуску
        restart = themes.get(name)['engine'] != themes.get(self.theme)['engine']
        self.theme = name
        self.settings['theme'] = self.theme
        save_settings(self.settings)
        self._apply_theme()
        if restart:
            self.say('Тему змінено — щоб вона лягла повністю, перезапусти програму.', 'dim')
        elif any(w is not None and w.winfo_exists() for w in (self.editor_win, self.reminders_win)):
            self.say('Тема відкритих вікон зміниться, коли їх перевідкрити.', 'dim')

    # --------------------------------------------------------------- шляхи
    def _pick(self):
        d = filedialog.askdirectory(title='Оберіть теку з грою')
        if not d:
            return
        g = GAMES[self.game.get()]
        if not os.path.exists(os.path.join(d, g['marker'])):
            messagebox.showwarning('Не та тека',
                                   f'У цій теці немає {g["marker"]} — це не {g["title"]}.')
            return
        self.settings[self.game.get()] = os.path.normpath(d)
        save_settings(self.settings)
        self._refresh_path()

    def _autofind(self):
        g = GAMES[self.game.get()]
        p = find_game(g)
        if p:
            self.settings[self.game.get()] = p
            save_settings(self.settings)
            self._refresh_path()
            self.say(f'Знайшов {g["title"]}: {p}', 'ok')
        else:
            self.say(f'Не знайшов {g["title"]} у бібліотеках Steam — вкажи теку вручну.', 'warn')

    def _switch_game(self):
        self.settings['last_game'] = self.game.get()
        save_settings(self.settings)
        self._refresh_path()

    def _show_chibi(self):
        """Випадкова дівчина поточної гри праворуч у журналі (якщо вже витягнуті)."""
        try:
            import chibi
            from PIL import Image, ImageTk
            p = chibi.pick(self.game.get())
            self.chibi_photo = ImageTk.PhotoImage(Image.open(p)) if p else None
        except Exception:
            self.chibi_photo = None
        self.chibi.configure(image=self.chibi_photo or '')

    def _refresh_path(self):
        self._show_chibi()
        self.path_var.set(self.settings.get(self.game.get(), ''))
        if hasattr(self, 'textures'):
            self.textures.set(self._textures_on())
        if hasattr(self, 'mt_game'):
            self.mt_game.set(self._mt_in_game())
        w = getattr(self, 'reminders_win', None)
        if w is not None and w.winfo_exists():              # нагадування — іншої гри
            w.reload(self._xl_now(), GAMES[self.game.get()]['title'])
        if hasattr(self, 'slot_frame'):
            for w in self.slot_frame.winfo_children():
                w.state(['!disabled'] if self.game.get() == 'crystar' else ['disabled'])
        self._refresh_books()
        self._refresh_badge()
        self._refresh_title()
        self._refresh_editor_hint()

    def _show_books(self, xl, names):
        """Список книг з відсотком готовності: «05 Сюжет 0003xx — 72%»."""
        cur = self.book_file()
        self.book_files = {}
        for fn in names:
            st = self.book_pc.get(os.path.join(xl, fn))
            label = fn[:-5]
            if st and st[2]:
                pc = 100 * st[1] / st[2]
                label += ' — ✓ 100%' if st[1] == st[2] else f' — {pc:.0f}%'
            self.book_files[label] = fn
        labels = list(self.book_files)
        self.book_box['values'] = labels
        keep = next((k for k, v in self.book_files.items() if v == cur), None)
        self.book.set(keep or (labels[0] if labels else ''))

    def book_file(self):
        """Ім'я файлу вибраної книги (у списку — підпис з відсотком)."""
        return self.book_files.get(self.book.get(), '')

    def _refresh_title(self):
        base = f'KitsuneLoc {VERSION} — {GAMES[self.game.get()]["title"]}'
        pc = self.settings.get('pc', {}).get(self.game.get())
        self.title(f'{base} — {pc}' if pc else base)

    def _save_mt_game(self):
        self.settings.setdefault('mt_in_game', {})[self.game.get()] = bool(self.mt_game.get())
        save_settings(self.settings)
        self.say('Машинний переклад ' + ('підставлятиметься в гру там, де ще немає свого — лише '
                 'для пробного проходу, перед справжнім «2» вимкни.' if self.mt_game.get() else
                 'в гру не йде: там, де немає свого перекладу, буде оригінал.'), 'dim')

    def _save_textures(self):
        self.settings.setdefault('textures', {})[self.game.get()] = bool(self.textures.get())
        save_settings(self.settings)
        self.say('Написи на картинках ' + ('заливатимуться в гру.' if self.textures.get() else
                 'не заливатимуться: при «2» у грі будуть оригінальні текстури '
                 '(уже перекладені повернуться до оригіналу).'), 'dim')

    def _save_slot(self):
        self.settings['crystar_slot'] = self.slot.get()
        save_settings(self.settings)

    def _save_sound(self):
        self.settings['sound'] = self.sound.get()
        save_settings(self.settings)

    def copy_log(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(self.log.get('1.0', 'end-1c'))
            self.set_status('Лог скопійовано в буфер обміну.')
        except tk.TclError:
            pass

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind in ('log', 'link', 'act'):
                    text, tag = val[:2]
                    tags = (tag,)
                    if kind == 'link':
                        name = f'goto{len(self.links)}'
                        self.links[name] = (self.game.get(),) + tuple(val[2])
                        self.links_shown[name] = val[2][-1]
                        self.log.tag_bind(name, '<Button-1>',
                                          lambda e, n=name: self._goto(self.links[n][:4]))
                        self.log.tag_bind(name, '<Button-3>',
                                          lambda e, n=name: self._link_menu(e, n))
                        tags = (tag, 'link', name)
                    elif kind == 'act':                  # посилання-дія (напр. «показати затверджені»)
                        name = f'act{len(self.links)}'
                        self.links[name] = val[2]
                        self.log.tag_bind(name, '<Button-1>', lambda e, n=name: self.links[n]())
                        tags = (tag, 'link', name)
                    self.log.configure(state='normal')
                    self.log.insert('end', text, tags)
                    self.log.insert('end', '\n', tag)
                    self.log.see('end')
                    self.log.configure(state='disabled')
                    if self.logf:
                        try:
                            self.logf.write(text + '\n'); self.logf.flush()
                        except OSError:
                            pass
                elif kind == 'chibi':
                    if val == self.game.get():
                        self._show_chibi()
                elif kind == 'books':
                    game, xl, _names = val
                    if game == self.game.get() and os.path.isdir(xl):
                        self._show_books(xl, sorted(
                            f for f in os.listdir(xl) if f.endswith('.xlsx') and not f.startswith('~$')))
                elif kind == 'step':
                    i, n, label = val
                    self.pb['maximum'] = max(n, 1)
                    self.pb['value'] = i
                    self.status.set(f'{label}  ({i}/{n})' if label else f'{i}/{n}')
                elif kind == 'status':
                    self.status.set(val)
                elif kind == 'call':                     # дія в головному потоці (з фонового)
                    val()
                elif kind == 'done':
                    self.busy = False
                    self.elapsed.set('')
                    for b in self.buttons:
                        b.state(['!disabled'])
                    self.b_stop.state(['disabled'])
                    self.pb['value'] = 0
                    self._refresh_books()
                    self._refresh_badge()
                    if self.closing:
                        self._shutdown()
                        return
        except queue.Empty:
            pass
        if self.busy and self.t0:
            s = int(time.time() - self.t0)
            self.elapsed.set(f'{s // 60}:{s % 60:02d}')
        self.after(80, self._poll)

    def _snap(self):
        """Знімок стану вікна — фоновий потік не має чіпати tk-змінні."""
        self.cur = {'game': self.game.get(), 'path': self.path_var.get().strip(),
                    'slot': self.slot.get()}

    def _run(self, fn, sound=None):
        if self.busy:
            return
        if sound and self.sound.get():
            play(sound)
        self._snap()
        try:
            self._fresh()
        except Exception:
            self.say('Не вдалося перечитати модулі:\n' + traceback.format_exc(), 'err')
        self.busy = True
        self.cancel.clear()
        self.t0 = time.time()
        for b in self.buttons:
            b.state(['disabled'])
        self.b_stop.state(['!disabled'])

        def work():
            try:
                fn()
            except Cancelled:
                self.say('\nЗупинено. У теці гри нічого не змінено.', 'warn')
                self.set_status('Зупинено.')
            except RuntimeError as ex:
                self.say('\n' + str(ex), 'err')
                self.set_status('Не вдалося — дивись повідомлення нижче.')
            except Exception:
                self.say('\nПОМИЛКА:\n' + traceback.format_exc(), 'err')
                self.set_status('Не вдалося — дивись повідомлення нижче.')
            finally:
                self.q.put(('done', None))
        threading.Thread(target=work, daemon=True).start()

    def _stop(self):
        if self.busy:
            self.cancel.set()
            self.set_status('Зупиняю…')
            self.b_stop.state(['disabled'])

    def _on_close(self):
        if self.busy:
            if not messagebox.askyesno(
                    'Триває робота',
                    'Зараз програма пише файли. Якщо закрити просто зараз, '
                    'файл у теці гри може лишитися недописаним.\n\n'
                    'Зупинити роботу й закрити вікно?'):
                return
            self.closing = True
            self.cancel.set()
            self.set_status('Зупиняю…')
            return
        self._shutdown()

    def _shutdown(self):
        try:
            self._flush_editor()
        except Exception:
            pass
        try:
            self.settings['geometry'] = self.geometry()
            self.settings['last_game'] = self.game.get()
            save_settings(self.settings)
        except Exception:
            pass
        if self.logf:
            try:
                self.logf.close()
            except OSError:
                pass
        self.destroy()

    def _check_deps(self):
        import importlib.util
        missing = [pip for mod, pip in self.DEPS if importlib.util.find_spec(mod) is None]
        if not missing:
            return
        if messagebox.askyesno(
                'Не вистачає бібліотек',
                'Для роботи потрібні бібліотеки, яких зараз немає:\n'
                + ', '.join(missing) + '\n\nВстановити автоматично?'):
            self.say('Встановлюю ' + ', '.join(missing) + '…', 'dim')

            def job():
                try:
                    subprocess.run([sys.executable, '-m', 'pip', 'install'] + missing,
                                   check=True, capture_output=True)
                    self.say('Готово. Перезапусти вікно.', 'ok')
                except Exception as ex:
                    self.say(f'Не вдалося встановити: {ex}\n'
                             'Запусти «Встановити.bat» вручну.', 'err')
            threading.Thread(target=job, daemon=True).start()
        else:
            self.say('Без цих бібліотек програма працюватиме не повністю. '
                     'Запусти «Встановити.bat».', 'warn')

    def make_installer(self):
        """«Створити інсталятор…»: шлях до zip питаємо тут (діалог — лише з головного потоку)."""
        if self.busy:
            return
        g = self.game.get()
        desk = os.path.join(os.path.expanduser('~'), 'Desktop')
        path = filedialog.asksaveasfilename(
            parent=self, title='Куди зберегти інсталятор перекладу',
            initialdir=desk if os.path.isdir(desk) else HERE,
            initialfile=f'{GAMES[g]["folder"]} — українська ({time.strftime("%Y-%m-%d")}).zip',
            defaultextension='.zip', filetypes=[('Архів zip', '*.zip')])
        if path:
            self._run(lambda: self.do_installer(os.path.normpath(path)))

    def _link_menu(self, event, name):
        """Правий клік по попередженню: відкрити рядок / затвердити / скасувати."""
        game, s, i, src, msg, tr, approved = self.links[name]
        m = tk.Menu(self, tearoff=0)
        m.add_command(label='Відкрити рядок', command=lambda: self._goto((game, s, i, src)))
        if approved:
            m.add_command(label='Скасувати затвердження',
                          command=lambda: self._approve(name, False))
        else:
            m.add_command(label='Затвердити: це не помилка',
                          command=lambda: self._approve(name, True))
        m.add_separator()
        m.add_command(label='Повідомити про це попередження…', command=lambda: self.open_feedback({
            'game': game, 'kind': 'вигляд',
            'row': {'source': s, 'id': i, 'src': src or '', 'tr': tr or '', 'warn': msg}}))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _approve(self, name, on):
        import sheets
        game, s, i, src, msg, tr, approved = self.links[name]
        xl = os.path.join(XLSX, GAMES[game]['folder'])
        ok = sheets.load_approved(xl)
        key = sheets.approval_key(s, i, msg)
        if on:
            ok[key] = tr
        else:
            ok.pop(key, None)
        try:
            sheets.save_approved(xl, ok)
        except OSError as ex:
            self.set_status(f'Не вдалося зберегти: {ex}')
            return
        self.links[name] = (game, s, i, src, msg, tr, on)
        # закреслене — рядок уже не в тому списку, у якому його показано
        self.log.tag_configure(name, overstrike=on != self.links_shown.get(name, approved))
        self.set_status('Затверджено: це попередження більше не показуватиметься, доки не зміниш переклад.'
                        if on else 'Затвердження скасовано.')

    def open_xlsx(self):
        self._snap()
        _w, xl, _o, _b = self.dirs()
        if not os.path.isdir(xl):
            messagebox.showinfo('Немає файлів',
                                'Спочатку натисни «1. Дістати текст з гри».')
            return
        self._open_path(xl)

    def open_book(self):
        self._snap()
        _w, xl, _o, _b = self.dirs()
        name = self.book_file()
        if not name:
            messagebox.showinfo('Немає книг',
                                'Спочатку натисни «1. Дістати текст з гри».')
            return
        self._open_path(os.path.join(xl, name))

    def open_reminders(self):
        """Вікно «Нагадування» (reminders_window.py) — для гри, вибраної вгорі."""
        w = self.reminders_win
        if w is not None and w.winfo_exists():
            w.reload(self._xl_now(), GAMES[self.game.get()]['title'])
            w.lift()
            return
        try:
            import importlib, reminders, reminders_window
            importlib.reload(reminders)
            importlib.reload(reminders_window)
            self.reminders_win = reminders_window.RemindersWindow(self, self._xl_now(),
                                                                  GAMES[self.game.get()]['title'])
        except Exception as e:
            messagebox.showerror('Нагадування', str(e))

    def reminders_changed(self):
        """Додано нагадування з іншого вікна — оновити відкрите вікно нагадувань."""
        w = self.reminders_win
        if w is not None and w.winfo_exists():
            w.reload()

    def open_terms(self):
        """Глосарій гри й «як уже перекладено в книгах» (terms_window.py)."""
        self._snap()
        try:
            import importlib, terms_window, glossary
            importlib.reload(glossary)
            importlib.reload(terms_window)
            terms_window.TermsWindow(self)
        except Exception as e:
            messagebox.showerror('Терміни', str(e))

    # ------------------------------------------------------ звернення (feedback.py)
    def colors(self):
        return THEMES[self.theme]

    def dark_titlebar(self, win):
        dark_titlebar(win, themes.is_dark(self.theme))

    def open_feedback(self, ctx=None):
        """Вікно «Повідомити про проблему» (feedback_window.py); ctx — рядок, звідки відкрито."""
        try:
            import importlib, feedback, feedback_window
            importlib.reload(feedback)
            importlib.reload(feedback_window)
            feedback_window.FeedbackWindow(self, ctx)
        except Exception as e:
            messagebox.showerror('Звернення', f'{e}\n\n{traceback.format_exc()}')

    def open_editor(self, goto=None):
        """Редактор перекладу: увесь текст гри в програмі (editor.py)."""
        self._snap()
        import importlib, project
        work, xl, _o, bk = self.dirs()
        win = getattr(self, 'editor_win', None)
        if win is not None and win.winfo_exists():
            if goto:
                win.goto(goto)
            win.lift()
            return
        if not os.path.isdir(work) or not any(True for _ in __import__('common').walk(work)):
            messagebox.showinfo('Редактор перекладу', 'Спочатку натисни «1. Дістати текст з гри».')
            return
        if not project.enabled(xl):
            busy = self.open_books(xl)
            if busy:
                messagebox.showwarning('Редактор перекладу',
                                       'Спершу закрий в Excel: ' + ', '.join(busy) +
                                       '\n(збережи зміни — програма їх перенесе).')
                return
            if not messagebox.askyesno(
                    'Перейти на редактор',
                    'Переклад переїде з книг Excel у програму:\n\n'
                    '• усе, що вже є в книгах, перенесеться;\n'
                    '• однакові рядки стануть пов\'язаними (переклав один — перекладено всі); '
                    'де однакові рядки вже перекладено по-різному — вони лишаться окремими;\n'
                    '• далі програма бере переклад із редактора, а не з книг. Книги можна '
                    'вивантажити будь-коли як копію, а зміни з них — завантажити назад.\n\n'
                    'Перейти?'):
                return
            if self.busy:
                return
            self._run(lambda: self._migrate(), None)
            return
        try:
            for name in ('project', 'preview', 'editor'):
                if name in sys.modules:
                    importlib.reload(sys.modules[name])
            import editor
            editor.Editor(self, self.get_project(), bk, goto=goto)
        except Exception as e:
            messagebox.showerror('Редактор перекладу', f'{e}\n\n{traceback.format_exc()}')

    def open_pics(self, goto=None):
        """Вікно перекладу написів на картинках з живим прев'ю (goto — ключ напису, з редактора)."""
        self._snap()
        if self.cur['game'] not in ('msk', 'nep'):
            messagebox.showinfo('Немає написів',
                                'Написи на картинках є в Mary Skelter і Neptunia Re;Birth1.')
            return
        try:
            bad = self.check_originals(fix=False)
        except RuntimeError:
            bad = []
        if bad:
            messagebox.showwarning(
                'Немає чистих оригіналів',
                'Резервні копії гри (' + ', '.join(bad) + ') — не оригінали, тож колонка '
                '«Оригінал» показала б уже перекладені картинки.\n\n'
                'Закрий гру, у Steam зроби «Перевірити цілісність файлів гри» і натисни '
                '«1. Дістати текст з гри» — програма оновить копії. Потім відкрий це вікно знову.')
            return
        try:
            self._fresh()
            import atlas_editor
            import importlib
            importlib.reload(atlas_editor)
            win = atlas_editor.Editor(self)
            if goto:
                win.goto(goto)
        except Exception as e:
            messagebox.showerror('Написи на картинках', str(e))

    def open_scan(self):
        """Пошук і розмітка написів на картинках самим перекладачем (textscan_window.py)."""
        self._snap()
        if self.cur['game'] not in ('msk', 'nep'):
            messagebox.showinfo('Немає написів',
                                'Написи на картинках є в Mary Skelter і Neptunia Re;Birth1.')
            return
        try:
            bad = self.check_originals(fix=False)
        except RuntimeError:
            bad = []
        if bad:
            messagebox.showwarning(
                'Немає чистих оригіналів',
                'Резервні копії гри (' + ', '.join(bad) + ') — не оригінали: на картинках уже '
                'намальований переклад. Закрий гру, у Steam зроби «Перевірити цілісність файлів '
                'гри» і натисни «1. Дістати текст з гри». Потім відкрий це вікно знову.')
            return
        try:
            self._fresh()
            import importlib, textscan, textscan_window
            importlib.reload(textscan)
            importlib.reload(textscan_window)
            textscan_window.TextScan(self)
        except Exception as e:
            messagebox.showerror('Знайти написи', f'{e}\n\n{traceback.format_exc()}')

    def launch(self):
        self._snap()
        try:
            exe = os.path.join(self.root_dir(), GAMES[self.cur['game']]['exe'])
        except RuntimeError as e:
            messagebox.showerror('Помилка', str(e)); return
        if not os.path.exists(exe):
            messagebox.showerror('Помилка', f'Не знайшов {exe}'); return
        # робоча тека — тека гри: Neptunia шукає data\ відносно неї, і без
        # цього одразу падає («Application has crashed»)
        try:
            subprocess.Popen([exe], cwd=os.path.dirname(exe))
        except OSError as e:
            messagebox.showerror('Не вдалося запустити гру', str(e))

    def _after(self, ms, fn):
        """Відкладена дія (Core._feedback_flush) — у Tk через after."""
        self.after(ms, fn)


if __name__ == '__main__':
    App().mainloop()
