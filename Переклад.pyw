#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вікно для роботи з перекладом Crystar, Mary Skelter: Nightmares
та Hyperdimension Neptunia Re;Birth1.

Три кроки: дістати текст з гри -> перекласти в Excel -> залити назад.
Нічого вводити руками не треба.
"""
import os, re, sys, json, time, shutil, threading, traceback, subprocess, queue
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')   # numpy (через openpyxl) інакше резервує ~30 МБ на кожне ядро
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

VERSION = '1.5'

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SETTINGS = os.path.join(HERE, 'settings.json')
XLSX = os.path.join(HERE, 'Переклад')
WORK = os.path.join(HERE, 'work')
OUT = os.path.join(HERE, 'out')
BACKUP = os.path.join(HERE, 'backup')
LOGFILE = os.path.join(HERE, 'лог.txt')

GAMES = {
    'crystar': {
        'title': 'Crystar',
        'folder': 'Crystar',          # назва теки — без двокрапок, Windows їх не любить
        'steam': 'Crystar',           # назва теки в steamapps\common
        'exe': 'CRYSTAR.exe',
        'marker': 'CRYSTAR_Data',
    },
    'msk': {
        'title': 'Mary Skelter: Nightmares',
        'folder': 'Mary Skelter',
        'steam': 'Mary Skelter Nightmares',
        'exe': 'MarySkelter.exe',
        'marker': 'Script.bra',
    },
    'nep': {
        'title': 'Neptunia Re;Birth1',
        'folder': 'Neptunia',
        'steam': 'Neptunia Rebirth1',
        'exe': 'NeptuniaReBirth1.exe',
        'marker': os.path.join('data', 'SYSTEM00000.pac'),
    },
}

# ---------------------------------------------------------------- теми вікна
# Кольори підігнані під тему Sun Valley (sv-ttk, вигляд Windows 11); без неї
# вікно малюється старою ручною темою з тими самими кольорами.
THEMES = {
    'light': dict(bg='#fafafa', fg='#1c1c1c', panel='#ffffff', logbg='#ffffff',
                  logfg='#1c1c1c', dim='#6a6a6a', err='#b00020', warn='#8a6100',
                  ok='#0a6b2e', accent='#005fb8', link='#005fb8'),
    'dark':  dict(bg='#1c1c1c', fg='#e6e6e6', panel='#2b2b2b', logbg='#202020',
                  logfg='#dfe3e6', dim='#8b949e', err='#ff7b72', warn='#e3b341',
                  ok='#56d364', accent='#57c8ff', link='#57c8ff'),
}

SOUNDS = os.path.join(HERE, 'звуки')


def play(name):
    """Звук кнопки (асинхронно; нема файлу чи звукової карти — мовчки)."""
    p = os.path.join(SOUNDS, name)
    if os.name != 'nt' or not os.path.exists(p):
        return
    try:
        import winsound
        winsound.PlaySound(p, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass


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


class Cancelled(Exception):
    """Користувач натиснув «Зупинити»."""


# ------------------------------------------------------------- пошук ігор
def steam_libraries():
    """Усі бібліотеки Steam: з реєстру, зі стандартних місць і з libraryfolders.vdf."""
    roots = []
    try:
        import winreg
        for hk, key, val in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
                             (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath'),
                             (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Valve\Steam', 'InstallPath')):
            try:
                with winreg.OpenKey(hk, key) as k:
                    roots.append(winreg.QueryValueEx(k, val)[0])
            except OSError:
                pass
    except ImportError:
        pass
    roots += [r'C:\Program Files (x86)\Steam', r'C:\Program Files\Steam',
              os.path.expanduser(r'~\Games\Steam'),
              os.path.expanduser(r'~\Steam')]

    libs = []
    def add(p):
        try:
            p = os.path.normpath(p)
        except Exception:
            return
        if os.path.isdir(p) and p not in libs:
            libs.append(p)

    for r in roots:
        add(r)
    for r in list(libs):
        vdf = os.path.join(r, 'steamapps', 'libraryfolders.vdf')
        if not os.path.exists(vdf):
            continue
        try:
            txt = open(vdf, encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        # і новий формат ("path" "D:\\SteamLibrary"), і старий ("1" "D:\\...")
        for m in re.finditer(r'"(?:path|\d+)"\s*"([^"]+)"', txt):
            add(m.group(1).replace('\\\\', '\\'))
    return libs


def find_game(g):
    """Знайти теку гри автоматично. Повертає шлях або ''."""
    for lib in steam_libraries():
        p = os.path.join(lib, 'steamapps', 'common', g['steam'])
        if os.path.exists(os.path.join(p, g['marker'])):
            return p
    return ''


def load_settings():
    s = {}
    if os.path.exists(SETTINGS):
        try:
            s = json.load(open(SETTINGS, encoding='utf-8'))
        except Exception:
            s = {}
    for k, g in GAMES.items():
        if not s.get(k) or not os.path.exists(os.path.join(s[k], g['marker'])):
            found = find_game(g)
            if found:
                s[k] = found
    return s


def save_settings(s):
    try:
        json.dump(s, open(SETTINGS, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    except OSError:
        pass


def _same_drive(a, b):
    """Чи на одному томі (тоді os.replace — перейменування, а не копія)."""
    try:
        return os.stat(a).st_dev == os.stat(os.path.dirname(b)).st_dev
    except OSError:
        return False


def running(exe):
    """Чи запущено зараз програму `exe` (Windows, через tasklist)."""
    if os.name != 'nt':
        return False
    try:
        r = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {exe}', '/FO', 'CSV', '/NH'],
                           capture_output=True, text=True, timeout=10,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return exe.lower() in r.stdout.lower()
    except Exception:
        return False


def human(n):
    for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
        if n < 1024 or unit == 'ГБ':
            return f'{n:.0f} {unit}' if unit == 'Б' else f'{n:.1f} {unit}'
        n /= 1024.0


class App(tk.Tk):
    def __init__(self):
        super().__init__()
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
                                    lambda: self._run(self.do_export, 'neptune.wav'), accent=True)
        self.b_open = self._step_button(steps, 'Відкрити теку з перекладом',
                                        'там лежать книги .xlsx', self.open_xlsx)
        self.b2 = self._step_button(steps, '2. Залити переклад у гру', 'з резервною копією',
                                    lambda: self._run(self.do_import, 'neptune-shy.wav'), accent=True)
        self.b3 = self._step_button(steps, '3. Запустити гру',
                                    'подивитись результат', self.launch)

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

        # --- додатково ---------------------------------------------------
        ef = ttk.LabelFrame(self, text=' Додатково ')
        ef.pack(fill='x', **pad)
        row = ttk.Frame(ef); row.pack(fill='x', padx=10, pady=10)
        self.b_prog = ttk.Button(row, text='Мій прогрес',
                                 command=lambda: self._run(self.do_progress))
        self.b_prog.pack(side='left')
        self.b_check = ttk.Button(row, text='Перевірити переклад',
                                  command=lambda: self._run(self.do_check))
        self.b_check.pack(side='left', padx=8)
        self.b_rest = ttk.Button(row, text='Повернути оригінали гри',
                                 command=lambda: self._run(self.do_restore))
        self.b_rest.pack(side='left')
        self.b_copy = ttk.Button(row, text='Скопіювати лог', command=self.copy_log)
        self.b_copy.pack(side='right')
        self.b_theme = ttk.Button(row, text='Темна тема', command=self._toggle_theme)
        self.b_theme.pack(side='right', padx=8)
        self.sound = tk.BooleanVar(value=self.settings.get('sound', True))
        ttk.Checkbutton(row, text='Звуки', variable=self.sound,
                        command=self._save_sound).pack(side='right', padx=(0, 4))

        self.buttons = [self.b1, self.b2, self.b3, self.b_open, self.b_book, self.b_pics,
                        self.b_prog, self.b_check, self.b_rest,
                        self.b_find, self.b_pick]

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
        self.log.pack(side='left', fill='both', expand=True)
        self.links = {}                          # тег у лозі -> (source, id, оригінал)

    def _step_button(self, parent, text, hint, cmd, accent=False):
        box = ttk.Frame(parent); box.pack(side='left', padx=(0, 10))
        b = ttk.Button(box, text=text, command=cmd)
        if accent:
            b.configure(style='Accent.TButton')
        b.pack(fill='x')
        ttk.Label(box, text=hint, style='Hint.TLabel').pack(anchor='w', pady=(2, 0))
        return b

    def _apply_theme(self):
        c = THEMES[self.theme]
        st = ttk.Style()
        try:
            import sv_ttk
        except ImportError:
            sv_ttk = None
        if sv_ttk is not None:
            sv_ttk.set_theme(self.theme)
        else:
            try:
                st.theme_use('clam' if self.theme == 'dark' else
                             ('vista' if 'vista' in st.theme_names() else 'clam'))
            except tk.TclError:
                pass
        dark_titlebar(self, self.theme == 'dark')
        self.configure(bg=c['bg'])
        if sv_ttk is None and self.theme == 'dark':
            st.configure('.', background=c['bg'], foreground=c['fg'],
                         fieldbackground=c['panel'], bordercolor='#3a4048')
            st.configure('TLabelframe', background=c['bg'], bordercolor='#3a4048')
            st.configure('TLabelframe.Label', background=c['bg'], foreground=c['dim'])
            st.configure('TButton', background=c['panel'], foreground=c['fg'])
            st.map('TButton', background=[('active', '#39414a'), ('disabled', c['bg'])],
                   foreground=[('disabled', c['dim'])])
            st.configure('TEntry', fieldbackground=c['panel'], foreground=c['fg'])
            st.configure('TCombobox', fieldbackground=c['panel'], foreground=c['fg'])
            st.configure('TProgressbar', background=c['accent'], troughcolor=c['panel'])
        st.configure('Hint.TLabel', foreground=c['dim'], font=('Segoe UI', 8))
        self.b_theme.configure(text='Світла тема' if self.theme == 'dark' else 'Темна тема')

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

    def _toggle_theme(self):
        self.theme = 'dark' if self.theme == 'light' else 'light'
        self.settings['theme'] = self.theme
        save_settings(self.settings)
        self._apply_theme()

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

    def _refresh_path(self):
        self.path_var.set(self.settings.get(self.game.get(), ''))
        if hasattr(self, 'slot_frame'):
            for w in self.slot_frame.winfo_children():
                w.state(['!disabled'] if self.game.get() == 'crystar' else ['disabled'])
        self._refresh_books()
        self._refresh_badge()
        self._refresh_title()

    def _refresh_books(self):
        xl = os.path.join(XLSX, GAMES[self.game.get()]['folder'])
        names = []
        if os.path.isdir(xl):
            names = sorted(f for f in os.listdir(xl)
                           if f.endswith('.xlsx') and not f.startswith('~$'))
        self._show_books(xl, names)
        # відсотки рахуються у фоні: відкрити всі книги — кілька секунд
        game = self.game.get()

        def job():
            changed = False
            for fn in names:
                p = os.path.join(xl, fn)
                try:
                    mt = os.path.getmtime(p)
                    if self.book_pc.get(p, (None,))[0] == mt:
                        continue
                    import sheets
                    done, total, _a = sheets.book_stats(p)
                    self.book_pc[p] = (mt, done, total)
                    changed = True
                except Exception:
                    continue
            if changed:
                self.q.put(('books', (game, xl, names)))
        threading.Thread(target=job, daemon=True).start()

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

    def _refresh_badge(self):
        """Що зараз лежить у грі — переклад чи оригінал (порівнюємо з backup)."""
        c = THEMES[self.theme]
        try:
            bk = os.path.join(BACKUP, self.game.get())
            dest = self.data_dir_quiet()
            if not dest or not os.path.isdir(bk):
                self.badge.configure(text='у грі: оригінал', foreground=c['dim'])
                return
            same = total = 0
            for dp, _d, fs in os.walk(bk):
                for fn in fs:
                    src = os.path.join(dp, fn)
                    dst = os.path.join(dest, os.path.relpath(src, bk))
                    if not os.path.exists(dst):
                        continue
                    total += 1
                    a, b = os.stat(src), os.stat(dst)
                    if a.st_size == b.st_size and abs(a.st_mtime - b.st_mtime) < 2:
                        same += 1
            if not total:
                self.badge.configure(text='у грі: оригінал', foreground=c['dim'])
            elif same == total:
                self.badge.configure(text='у грі: оригінал', foreground=c['dim'])
            else:
                self.badge.configure(text='● у грі: переклад', foreground=c['ok'])
        except Exception:
            self.badge.configure(text='')

    def _refresh_title(self):
        base = f'KitsuneLoc {VERSION} — {GAMES[self.game.get()]["title"]}'
        pc = self.settings.get('pc', {}).get(self.game.get())
        self.title(f'{base} — {pc}' if pc else base)

    def _remember_pc(self, done, total):
        pc = f'{100 * done / total:.1f}%' if total else None
        d = self.settings.setdefault('pc', {})
        if pc:
            d[self.game.get()] = pc
            save_settings(self.settings)
        self._refresh_title()

    def _save_slot(self):
        self.settings['crystar_slot'] = self.slot.get()
        save_settings(self.settings)

    def _save_sound(self):
        self.settings['sound'] = self.sound.get()
        save_settings(self.settings)

    # ------------------------------------------------------------------ лог
    def _open_log(self):
        try:
            if os.path.exists(LOGFILE) and os.path.getsize(LOGFILE) > 2 * 1024 * 1024:
                shutil.copy2(LOGFILE, LOGFILE + '.old')
                open(LOGFILE, 'w', encoding='utf-8').close()
            self.logf = open(LOGFILE, 'a', encoding='utf-8')
            self.logf.write(f'\n===== {time.strftime("%Y-%m-%d %H:%M:%S")} '
                            f'KitsuneLoc {VERSION} =====\n')
            self.logf.flush()
        except OSError:
            self.logf = None

    def copy_log(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(self.log.get('1.0', 'end-1c'))
            self.set_status('Лог скопійовано в буфер обміну.')
        except tk.TclError:
            pass

    # ------------------------------------------------------- потік -> вікно
    def say(self, text, tag=''):
        self.q.put(('log', (text, tag)))

    def say_link(self, text, target, tag='warn'):
        """Рядок логу, клік по якому відкриває книгу на потрібному рядку;
        target = (source, id, оригінал)."""
        self.q.put(('link', (text, tag, target)))

    def step(self, i, n, label=''):
        if self.cancel.is_set():
            raise Cancelled()
        self.q.put(('step', (i, n, label)))

    def step_nc(self, i, n, label=''):
        """Те саме, але без скасування: почате копіювання в теку гри треба
        довести до кінця, інакше там лишиться половина перекладу."""
        self.q.put(('step', (i, n, label)))

    def set_status(self, text):
        self.q.put(('status', text))

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind in ('log', 'link'):
                    text, tag = val[:2]
                    tags = (tag,)
                    if kind == 'link':
                        name = f'goto{len(self.links)}'
                        self.links[name] = (self.game.get(),) + tuple(val[2])
                        self.log.tag_bind(name, '<Button-1>',
                                          lambda e, n=name: self._goto(self.links[n]))
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

    @staticmethod
    def _fresh():
        """Перечитати модулі з диска перед кожною дією: якщо скрипти оновили,
        поки вікно відкрите, воно одразу працює з новою версією."""
        import importlib
        for name in ('common', 'crystar.unitystr', 'crystar.tags', 'crystar',
                     'maryskelter.bra', 'maryskelter.gbnl', 'maryskelter.cl3',
                     'maryskelter.textdata', 'maryskelter.chars',
                     'maryskelter.enc', 'maryskelter.table', 'maryskelter.lzo1x',
                     'maryskelter.cpk', 'maryskelter.ffu', 'maryskelter.fontfix',
                     'maryskelter',
                     'neptunia.pac', 'neptunia.chars', 'neptunia.gbnl', 'neptunia.stcm',
                     'neptunia.ffu', 'neptunia.fontfix', 'neptunia.tid', 'neptunia.ssa',
                     'maryskelter.atlas', 'neptunia.atlas', 'neptunia',
                     'sheets', 'translate_msk', 'translate_crystar', 'translate_nep'):
            mod = sys.modules.get(name)
            if mod is not None:
                importlib.reload(mod)

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

    DEPS = (('openpyxl', 'openpyxl'), ('UnityPy', 'UnityPy'), ('PIL', 'Pillow'),
            ('texture2ddecoder', 'texture2ddecoder'), ('etcpak', 'etcpak'),
            ('sv_ttk', 'sv-ttk'))

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

    # ---------------------------------------------------------------- шляхи
    def root_dir(self):
        p = self.cur['path']
        if not p or not os.path.exists(os.path.join(p, GAMES[self.cur['game']]['marker'])):
            raise RuntimeError('Не бачу теку гри. Натисни «Знайти» або «Обрати…».')
        return p

    def data_dir(self):
        p = self.root_dir()
        return (os.path.join(p, 'CRYSTAR_Data', 'StreamingAssets')
                if self.cur['game'] == 'crystar' else p)

    def data_dir_quiet(self):
        """Те саме, але без винятку — для бейджа стану."""
        p = self.settings.get(self.game.get(), '')
        if not p or not os.path.exists(os.path.join(p, GAMES[self.game.get()]['marker'])):
            return ''
        return (os.path.join(p, 'CRYSTAR_Data', 'StreamingAssets')
                if self.game.get() == 'crystar' else p)

    def dirs(self):
        g = self.cur['game']
        return (os.path.join(WORK, g), os.path.join(XLSX, GAMES[g]['folder']),
                os.path.join(OUT, g), os.path.join(BACKUP, g))

    def check_originals(self, fix=True):
        """Чи справді в backup лежать оригінали (відбитки — оригінали.json).

        Резервна копія, зроблена з уже перекладеної гри (напр. після перенесення
        теки програми), ламає все, що читає «оригінал»: написи на картинках
        малюються поверх перекладу й з кожним разом гіршають. Якщо копія не
        оригінальна, а в теці гри чистий файл (після перевірки в Steam), —
        оновлюємо копію (fix=True). Повертає список проблемних файлів."""
        p = os.path.join(HERE, 'оригінали.json')
        g = self.cur['game']
        try:
            known = json.load(open(p, encoding='utf-8')).get(g) or {}
        except (OSError, ValueError):
            return []
        if not known:
            return []
        import common
        bk_root, dest = self.dirs()[3], self.data_dir()
        bad = []
        for rel, want in known.items():
            bk = os.path.join(bk_root, *rel.split('/'))
            gm = os.path.join(dest, *rel.split('/'))
            b_ok = os.path.exists(bk) and common.is_original(bk, want)
            if b_ok:
                continue
            g_ok = os.path.exists(gm) and common.is_original(gm, want)
            if os.path.exists(bk):
                if g_ok and fix:
                    self.say(f'  резервна копія {rel} не була оригіналом — '
                             f'замінюю чистим файлом з теки гри', 'warn')
                    os.makedirs(os.path.dirname(bk), exist_ok=True)
                    shutil.copy2(gm, bk + '.new')
                    os.replace(bk + '.new', bk)
                elif not g_ok:
                    bad.append(rel)
            elif os.path.exists(gm) and not g_ok:
                bad.append(rel)          # копії немає, а в грі вже змінений файл
        return bad

    def _require_originals(self):
        bad = self.check_originals()
        if bad:
            raise RuntimeError(
                'Немає чистих оригіналів гри для: ' + ', '.join(bad) + '.\n'
                'І резервні копії в backup, і файли в теці гри вже перекладені (перепаковані '
                'програмою), тож програма малювала б переклад поверх перекладу.\n'
                'Що зробити: закрий гру → Steam → гра → Властивості → Встановлені файли → '
                '«Перевірити цілісність файлів гри». Потім натисни кнопку ще раз — програма '
                'сама оновить резервні копії. Переклад у книгах Excel не постраждає.')

    # ------------------------------------------------------------------- дії
    @staticmethod
    def open_books(xl):
        """Книги, відкриті зараз в Excel (Excel кладе поруч файл ~$назва)."""
        if not os.path.isdir(xl):
            return []
        return sorted(f[2:] for f in os.listdir(xl) if f.startswith('~$'))

    def do_export(self):
        import sheets
        g = self.cur['game']
        work, xl, _out, _bk = self.dirs()
        busy = self.open_books(xl)
        if busy:
            raise RuntimeError('Спершу закрий в Excel: ' + ', '.join(busy) +
                               '\n(збережи зміни — вони нікуди не дінуться, програма їх підхопить)')
        self._require_originals()
        if os.path.isdir(xl):
            self.say('Читаю те, що вже перекладено…')
            st = sheets.read_into_work(xl, work, self.step)
            self.say(f'  підхоплено перекладів: {st["translated"]}', 'dim')
        self.say(f'Дістаю текст з гри ({GAMES[g]["title"]})… це може зайняти кілька хвилин.')
        self.set_status('Читаю файли гри…')
        ns = type('a', (), {})()
        ns.work_dir, ns.orig_dir = work, self.dirs()[3]
        if g == 'msk':
            import translate_msk as t
            ns.game_dir = self.root_dir()
        elif g == 'nep':
            import translate_nep as t
            ns.game_dir = self.root_dir()
        else:
            import translate_crystar as t
            ns.root = self.data_dir()
        self._capture(lambda: t.cmd_export(ns, self.step))
        if os.path.isdir(xl):
            # щойно з'явились нові розділи (напр. «Написи на картинках») — підхопити
            # переклад, який уже лежить у їхній книзі, доки її не перезібрано
            sheets.read_into_work(xl, work)
        self.say('Збираю книги Excel…')
        made = sheets.export(work, xl, g, progress=self.step)
        same = set(getattr(sheets.export, 'unchanged', []))
        for p, n in made:
            if os.path.basename(p) in same:
                continue
            extra = f' (автоматично підставлено {n})' if n else ''
            self.say(f'  {os.path.basename(p)}{extra}', 'dim')
        if same:
            self.say(f'  без змін, не переписано: {len(same)} книг', 'dim')
        for f in getattr(sheets.export, 'skipped', []):
            self.say(f'  ! {f} не оновлено — відкрита в Excel. Закрий і натисни «1» ще раз.', 'warn')
        done, total = __import__('common').stats(work)
        self._remember_pc(done, total)
        self.set_status(f'Готово. Перекладено {done} з {total} рядків.')
        self.say(f'\nФайли для перекладу тут:\n{xl}', 'ok')

    def do_import(self):
        import sheets
        g = self.cur['game']
        work, xl, out, bk = self.dirs()
        if not os.path.isdir(xl):
            raise RuntimeError('Спочатку натисни «1. Дістати текст з гри».')
        busy = self.open_books(xl)
        if busy:
            self.say('Увага: в Excel відкрито ' + ', '.join(busy) +
                     '. Беру те, що збережено на диску — незбережені зміни не потраплять у гру.',
                     'warn')
        self._require_closed()
        self._check_space()
        self._require_originals()
        self.say('Читаю переклад з Excel…')
        st = sheets.read_into_work(xl, work, self.step)
        self.say(f'  книг: {st["books"]}, рядків: {st["rows"]}, '
                 f'перекладено: {st["translated"]}', 'dim')
        if not st['translated']:
            raise RuntimeError('У книгах немає жодного перекладу — нема чого заливати.')
        warn = sheets.validate(work)
        if warn:
            self.say(f'\nПопереджень: {len(warn)} (перші 10; клік — відкрити рядок у книзі)', 'warn')
            self._say_warnings(warn, 10)
        self.say('\nЗбираю файли гри…')
        self.set_status('Перепаковую…')
        shutil.rmtree(out, ignore_errors=True)
        ns = type('a', (), {})()
        ns.work_dir, ns.out_dir, ns.orig_dir = work, out, bk
        if g == 'msk':
            import translate_msk as t
            ns.game_dir = self.root_dir()
        elif g == 'nep':
            import translate_nep as t
            ns.game_dir = self.root_dir()
        else:
            import translate_crystar as t
            ns.root, ns.slot = self.data_dir(), self.cur['slot']
            self.say(f'Пишу переклад у {"англійський" if ns.slot == "en" else "японський"} слот — '
                     'у грі має стояти ця ж мова тексту.', 'dim')
        self._capture(lambda: t.cmd_import(ns, self.step))
        if self.cancel.is_set():
            raise Cancelled()
        n = self._copy_into_game(out, self.data_dir(), bk)
        self.set_status(f'Готово. У грі замінено файлів: {n}.')
        self.say(f'\nЗамінено файлів: {n}. Оригінали збережено в {bk}.\n'
                 'Тепер тисни «3. Запустити гру».', 'ok')

    def _require_closed(self):
        """Гра відкрита — Windows може дозволити підмінити архів, але гра й далі
        працюватиме зі старим (так було з Neptunia). Краще зупинитись одразу."""
        exe = GAMES[self.cur['game']]['exe']
        if running(exe):
            raise RuntimeError(f'Гра зараз запущена ({exe}). Закрий її і натисни кнопку ще раз.')

    def _check_space(self):
        """Чи вистачить місця на перепакування (робоча копія + копія в грі)."""
        try:
            root = self.root_dir()
            need = 0
            for d in (root, os.path.join(root, 'data')):
                if not os.path.isdir(d):
                    continue
                for fn in os.listdir(d):
                    p = os.path.join(d, fn)
                    # Neptunia: перепаковуються лише архіви з текстом, не 8 ГБ моделей і звуку
                    if fn.lower().endswith('.pac') and fn not in ('SYSTEM00000.pac', 'GAME00000.pac'):
                        continue
                    if os.path.isfile(p) and fn.lower().endswith(('.bra', '.cpk', '.pac')):
                        need += os.path.getsize(p)
            if not need:
                need = 512 * 1024 * 1024
            need = int(need * 1.3)
            for label, path in (('де лежить програма', HERE), ('де лежить гра', root)):
                free = shutil.disk_usage(path).free
                if free < need:
                    raise RuntimeError(
                        f'Мало місця на диску, {label}: вільно {human(free)}, '
                        f'потрібно приблизно {human(need)}.\n'
                        'Звільни місце й спробуй ще раз.')
        except RuntimeError:
            raise
        except OSError:
            pass

    def _copy_into_game(self, out, dest, bk):
        """Копіюємо через тимчасовий файл поруч і os.replace — щоб у теці гри
        ніколи не лежав недописаний архів."""
        n = 0
        files = []
        for dp, _d, fs in os.walk(out):
            for fn in fs:
                files.append(os.path.join(dp, fn))
        for k, src in enumerate(files, 1):
            self.step_nc(k, len(files), 'Копіюю у гру')
            rel = os.path.relpath(src, out)
            dst = os.path.join(dest, rel)
            if not os.path.exists(dst):
                self.say(f'  ? у грі немає {rel} — пропускаю', 'warn')
                continue
            keep = os.path.join(bk, rel)
            if not os.path.exists(keep):
                os.makedirs(os.path.dirname(keep), exist_ok=True)
                shutil.copy2(dst, keep)
            tmp = dst + '.new'
            try:
                if _same_drive(src, dst):
                    # той самий диск — просто переносимо готовий файл (миттєво,
                    # без ще одного запису сотень МБ); out\ після цього не потрібен
                    os.replace(src, dst)
                else:
                    shutil.copy2(src, tmp)           # інший диск — копія й підміна
                    os.replace(tmp, dst)
            except OSError as ex:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                raise RuntimeError(f'Не вдалося записати {rel}: {ex}\n'
                                   'Можливо, гра зараз запущена — закрий її.')
            self.say(f'  -> {rel}', 'dim')
            n += 1
        return n

    def do_restore(self):
        _w, _x, _o, bk = self.dirs()
        self._require_closed()
        if not os.path.isdir(bk):
            raise RuntimeError('Резервних копій немає.')
        dest, n = self.data_dir(), 0
        files = []
        for dp, _d, fs in os.walk(bk):
            for fn in fs:
                files.append(os.path.join(dp, fn))
        for k, src in enumerate(files, 1):
            self.step_nc(k, len(files), 'Повертаю оригінали')
            dst = os.path.join(dest, os.path.relpath(src, bk))
            tmp = dst + '.new'
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            n += 1
        self.set_status(f'Повернуто оригіналів: {n}.')
        self.say(f'Повернуто оригіналів: {n}.', 'ok')

    def do_progress(self):
        import sheets
        g = self.cur['game']
        work, xl, _o, _b = self.dirs()
        busy = self.open_books(xl)
        if busy:
            self.say('Увага: відкриті в Excel книги рахуються по останньому '
                     'збереженню — ' + ', '.join(busy), 'warn')
        self.set_status('Рахую…')
        self.say(f'\n=== {GAMES[g]["title"]} ===', 'head')
        pr = sheets.progress(xl, work)
        for line in sheets.report(pr):
            self.say(line, 'mono')
        self._remember_pc(pr['done'], pr['total'])
        pc = 100 * pr['done'] / pr['total'] if pr['total'] else 0
        self.set_status(f'Перекладено {pr["done"]}/{pr["total"]} ({pc:.1f}%).')

    def do_check(self):
        import sheets, common
        work, xl, _o, _b = self.dirs()
        if os.path.isdir(xl):
            sheets.read_into_work(xl, work, self.step)
        done, total = common.stats(work)
        self._remember_pc(done, total)
        self.say(f'\nПерекладено {done} з {total} рядків '
                 f'({100 * done / total if total else 0:.1f}%).', 'head')
        warn = sheets.validate(work)
        if not warn:
            self.say('Попереджень немає — усе чисто.', 'ok')
        else:
            self.say(f'Попереджень: {len(warn)} (клік по рядку — відкрити його в книзі)', 'warn')
            self._say_warnings(warn, 200)
        self.set_status(f'Перекладено {done}/{total}.')

    def _say_warnings(self, warn, limit):
        import sheets
        src = getattr(sheets.validate, 'src', {})
        for s, i, w in warn[:limit]:
            self.say_link(f'  {s} [{i}] — {w}', (s, i, src.get((s, i))))
        if len(warn) > limit:
            self.say(f'  …і ще {len(warn) - limit}', 'warn')

    def _goto(self, target):
        """Клік по попередженню: відкрити книгу на рядку з цим перекладом."""
        game, source, eid, src = target
        if self.busy:
            self.set_status('Зачекай, поки закінчиться поточна дія.')
            return
        xl = os.path.join(XLSX, GAMES[game]['folder'])
        self.set_status('Шукаю рядок у книгах…')

        def job():
            import sheets
            try:
                loc = sheets.locate(xl, game, source, eid, src)
            except Exception as ex:
                self.set_status(f'Не вдалося знайти рядок: {ex}')
                return
            if not loc:
                self.set_status('Такого рядка в книгах немає — натисни «1», щоб перезібрати книги.')
                return
            path, sheet, cell = loc
            name = os.path.basename(path)
            if os.path.exists(os.path.join(xl, '~$' + name)):
                ok = self._excel_goto(path, sheet, cell)       # книга вже відкрита в Excel
                self.set_status(f'{name[:-5]}: аркуш «{sheet}», клітинка {cell}' +
                                ('' if ok else ' (книга вже відкрита — перейди туди сам)'))
                return
            try:
                sheets.goto_cell(path, sheet, cell)
            except Exception as ex:
                self.say(f'Не вдалося позначити рядок у книзі: {ex}', 'dim')
            self._open_path(path)
            self.set_status(f'Відкриваю {name[:-5]}: аркуш «{sheet}», клітинка {cell}.')
        threading.Thread(target=job, daemon=True).start()

    @staticmethod
    def _excel_goto(path, sheet, cell):
        """Перейти до клітинки в уже відкритій книзі Excel (через COM з PowerShell)."""
        if os.name != 'nt':
            return False
        ps = ("$ErrorActionPreference='Stop';"
              "$xl=[Runtime.InteropServices.Marshal]::GetActiveObject('Excel.Application');"
              "foreach($w in $xl.Workbooks){if($w.FullName -eq $env:KL_BOOK){"
              "$w.Activate();$s=$w.Worksheets.Item($env:KL_SHEET);$s.Activate();"
              "$xl.Goto($s.Range($env:KL_CELL),$false);"
              "(New-Object -ComObject WScript.Shell).AppActivate($xl.Caption)|Out-Null;exit 0}};exit 1")
        env = dict(os.environ, KL_BOOK=os.path.abspath(path), KL_SHEET=sheet, KL_CELL=cell)
        try:
            r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                               env=env, capture_output=True, timeout=20,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return r.returncode == 0
        except Exception:
            return False

    def _capture(self, fn):
        """Перехопити print() зі скриптів у лог."""
        app = self

        class W:
            def __init__(s):
                s.buf = ''

            def write(s, t):
                s.buf += t
                while '\n' in s.buf:
                    line, s.buf = s.buf.split('\n', 1)
                    line = line.strip()
                    if not line:
                        continue
                    tag = 'warn' if line.startswith('!') else 'dim'
                    app.say('  ' + line, tag)

            def flush(s):
                pass
        old, sys.stdout = sys.stdout, W()
        try:
            fn()
        finally:
            sys.stdout = old

    def _open_path(self, p):
        if os.name == 'nt':
            os.startfile(p)
        else:
            subprocess.Popen(['xdg-open', p])

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

    def open_pics(self):
        """Вікно перекладу написів на картинках з живим прев'ю."""
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
            atlas_editor.Editor(self)
        except Exception as e:
            messagebox.showerror('Написи на картинках', str(e))

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


if __name__ == '__main__':
    App().mainloop()
