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

VERSION = '2.13'

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
    # для вікон в окремих модулях (feedback_window): цей файл не імпортується
    GAMES, VERSION = GAMES, VERSION
    steam_libraries = staticmethod(steam_libraries)

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
        self.b_theme = ttk.Button(row, text='Темна тема', command=self._toggle_theme)
        self.b_theme.pack(side='right', padx=8)
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
        self.chibi.configure(bg=c['logbg'])

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

    def _refresh_editor_hint(self):
        import project
        xl = os.path.join(XLSX, GAMES[self.game.get()]['folder'])
        if project.enabled(xl):
            self.editor_hint.configure(text='переклад живе в програмі; книги Excel — лише копія '
                                            '(«Вивантажити в Excel» у редакторі)')
        else:
            self.editor_hint.configure(text='зараз переклад у книгах Excel; редактор перенесе його '
                                            'в програму')

    def _refresh_books(self):
        xl = os.path.join(XLSX, GAMES[self.game.get()]['folder'])
        names = []
        if os.path.isdir(xl):
            names = sorted(f for f in os.listdir(xl)
                           if f.endswith('.xlsx') and not f.startswith('~$'))
        self._show_books(xl, names)
        import project
        if project.enabled(xl):
            # переклад у редакторі, книги — лише копія: не читати їх заради відсотків
            # (MSK — 25 книг, кілька секунд процесора; саме тоді, коли відкривається редактор)
            return
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

    def _textures_on(self, game=None):
        return bool((self.settings.get('textures') or {}).get(game or self.game.get(), True))

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
        target = (source, id, оригінал, попередження, переклад, затверджено)."""
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

    @staticmethod
    def _fresh():
        """Перечитати модулі з диска перед кожною дією: якщо скрипти оновили,
        поки вікно відкрите, воно одразу працює з новою версією."""
        import importlib
        for name in ('common', 'crystar.unitystr', 'crystar.tags', 'crystar',
                     'compileheart.ffu', 'compileheart.fontfix', 'compileheart.scheme', 'compileheart.archive',
                     'compileheart.profiles', 'compileheart',
                     'maryskelter.bra', 'maryskelter.gbnl', 'maryskelter.cl3',
                     'maryskelter.textdata', 'maryskelter.chars',
                     'maryskelter.enc', 'maryskelter.table', 'maryskelter.lzo1x',
                     'maryskelter.cpk', 'maryskelter.ffu', 'maryskelter.fontfix',
                     'maryskelter.profile', 'maryskelter',
                     'neptunia.pac', 'neptunia.chars', 'neptunia.gbnl', 'neptunia.stcm',
                     'neptunia.ffu', 'neptunia.fontfix', 'neptunia.tid', 'neptunia.ssa',
                     'maryskelter.atlas', 'neptunia.atlas', 'neptunia.crowdin', 'neptunia.profile', 'neptunia',
                     'unity.tmpfont', 'unity.sdf', 'unity.fontfix', 'unity.tmptext', 'unity',
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

    DEPS = (('openpyxl', 'openpyxl'), ('UnityPy', 'UnityPy'), ('PIL', 'Pillow'),
            ('texture2ddecoder', 'texture2ddecoder'), ('etcpak', 'etcpak'),
            ('sv_ttk', 'sv-ttk'), ('fontTools', 'fonttools'), ('numpy', 'numpy'))

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
            known = {}
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
        # решта резервних копій (відбитків немає: сцени й таблиці Crystar тощо) — за
        # слідами перекладу. Crystar: копія parameter з перекладом давала українське
        # в колонці «Японська». Копії не змінюються — вердикт кешуємо (кеш\)
        cache_p = os.path.join(HERE, 'кеш', f'оригінали_{g}.json')
        try:
            cache = json.load(open(cache_p, encoding='utf-8'))
        except (OSError, ValueError):
            cache = {}
        seen = {}
        for dp, _d, fs in os.walk(bk_root) if os.path.isdir(bk_root) else ():
            for fn in fs:
                bk = os.path.join(dp, fn)
                rel = os.path.relpath(bk, bk_root).replace(os.sep, '/')
                if rel in known:
                    continue
                st = os.stat(bk)
                sig = [st.st_size, int(st.st_mtime)]
                hit = cache.get(rel)
                if hit and hit[:2] == sig:
                    patched = hit[2]
                else:
                    try:
                        patched = common.looks_patched(bk)
                    except Exception:
                        patched = None
                seen[rel] = sig + [patched]
                if not patched:
                    continue
                gm = os.path.join(dest, *rel.split('/'))
                try:
                    g_ok = os.path.exists(gm) and common.looks_patched(gm) is False
                except Exception:
                    g_ok = False
                if g_ok and fix:
                    self.say(f'  резервна копія {rel} не була оригіналом — '
                             f'замінюю чистим файлом з теки гри', 'warn')
                    shutil.copy2(gm, bk + '.new')
                    os.replace(bk + '.new', bk)
                    st = os.stat(bk)
                    seen[rel] = [st.st_size, int(st.st_mtime), False]
                else:
                    bad.append(rel)
        if seen != cache:
            try:
                os.makedirs(os.path.dirname(cache_p), exist_ok=True)
                with open(cache_p, 'w', encoding='utf-8') as f:
                    json.dump(seen, f, ensure_ascii=False)
            except OSError:
                pass
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
        import project
        in_editor = project.enabled(xl)
        if in_editor:
            pass                        # переклад — у переклад.json, книги не читаємо
        elif os.path.isdir(xl):
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
        if in_editor:
            # нові рядки з гри — у проєкт; переклад із переклад.json — у work
            pr = project.Project(g, work, xl)
            pr.sync_work()
            self._project = pr
            win = getattr(self, 'editor_win', None)
            if win is not None:
                self.q.put(('call', lambda: win.winfo_exists() and win.reload(pr)))
            p = pr.progress()
            self._remember_pc(p['done'], p['total'])
            self.set_status(f'Готово. Перекладено {p["done"]} з {p["total"]} рядків.')
            self.say('\nТекст з гри оновлено. Перекладай у «Редактор перекладу…».', 'ok')
            self._chibi(g)
            return
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
        self._chibi(g)

    def _chibi(self, g):
        try:
            import chibi
            if chibi.ensure(g, self.dirs()[3], self.root_dir(), self.step_nc):
                self.q.put(('chibi', g))
        except Exception as ex:
            self.say(f'  (картинки для журналу не вдалося витягти: {ex})', 'dim')

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
        if not self._read_translation():
            raise RuntimeError('Перекладу ще немає — нема чого заливати.')
        warn, hidden = sheets.split_approved(sheets.validate(work, terms=__import__('glossary').load(xl), widths=sheets.load_widths(xl), rows=sheets.load_row_limits(xl)), xl)
        if warn:
            self.say(f'\nПопереджень: {len(warn)} (перші 10; клік — відкрити рядок, '
                     'правий клік — затвердити)', 'warn')
        self._say_warnings(warn, 10, hidden)
        self.say('\nЗбираю файли гри…')
        self.set_status('Перепаковую…')
        shutil.rmtree(out, ignore_errors=True)
        ns = type('a', (), {})()
        ns.work_dir, ns.out_dir, ns.orig_dir = work, out, bk
        ns.pics_dir = xl                # свої картинки перекладача: <xl>\Свої картинки (pics.py)
        ns.textures = self._textures_on(g)   # галочка «Заливати в гру написи на картинках»
        if not ns.textures:
            self.say('Написи на картинках вимкнено — текстури в гру не заливаю.', 'dim')
        if g == 'msk':
            import translate_msk as t
            ns.game_dir = self.root_dir()
        elif g == 'nep':
            import translate_nep as t
            ns.game_dir = self.root_dir()
        else:
            import translate_crystar as t
            ns.root, ns.slot = self.data_dir(), self.cur['slot']
            self.settings['crystar_slot_in_game'] = ns.slot    # для інсталятора: яка мова в грі
            save_settings(self.settings)
            self.say(f'Пишу переклад у {"англійський" if ns.slot == "en" else "японський"} слот — '
                     'у грі має стояти ця ж мова тексту.', 'dim')
        self._capture(lambda: t.cmd_import(ns, self.step))
        if self.cancel.is_set():
            raise Cancelled()
        n = self._copy_into_game(out, self.data_dir(), bk)
        self.set_status(f'Готово. У грі замінено файлів: {n}.')
        self.say(f'\nЗамінено файлів: {n}. Оригінали збережено в {bk}.\n'
                 'Тепер тисни «3. Запустити гру».', 'ok')

    def _read_translation(self):
        """Переклад -> work\\: з редактора (переклад.json) або, як раніше, з книг Excel.
        Повертає кількість перекладених рядків."""
        import sheets, project
        g = self.cur['game']
        work, xl, _o, _b = self.dirs()
        if project.enabled(xl):
            self.say('Беру переклад з редактора…')
            pr = getattr(self, '_project', None)
            if pr is None or pr.game != g:
                pr = self._project = project.Project(g, work, xl)
            pr.save()                   # переклад.json + work (правки з вікна вже скинуто _flush_editor)
            done = sum(1 for r in pr.rows if r['e'].get('tr'))
            self.say(f'  перекладено рядків: {done}', 'dim')
            newer = pr.excel_newer()
            if newer:
                self.say('  Увага: книги Excel змінено після вивантаження (' + ', '.join(newer[:5]) +
                         ('…' if len(newer) > 5 else '') + '). Переклад береться з редактора; '
                         'щоб узяти зміни з книг — «Завантажити з Excel…» у редакторі.', 'warn')
            return done
        self.say('Читаю переклад з Excel…')
        st = sheets.read_into_work(xl, work, self.step)
        self.say(f'  книг: {st["books"]}, рядків: {st["rows"]}, '
                 f'перекладено: {st["translated"]}', 'dim')
        return st['translated']

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
        # Спершу — чи всі файли гри вільні. Гру щойно закрито, процесу вже немає, а Windows
        # ще кілька секунд тримає файли (Crystar: «2» замінив текст, а uistatic зі шрифтом —
        # ні: у грі українське без і є ґ і з широкими літерами). Зайнятий файл — чекаємо;
        # не звільнився — зупиняємось, НІЧОГО не змінивши
        self._wait_unlocked([os.path.join(dest, os.path.relpath(s, out)) for s in files])
        done = []
        for k, src in enumerate(files, 1):
            self.step_nc(k, len(files), 'Копіюю у гру')
            rel = os.path.relpath(src, out)
            dst = os.path.join(dest, rel)
            # наші власні файли поруч з exe (MSK: переклад рядків ПК-порту і патч,
            # що його підставляє) — у чистій грі їх ще немає, і це нормально
            ours = rel.lower() in ('ua_strings.bin', 'dinput8.dll')
            if not os.path.exists(dst) and not ours:
                self.say(f'  ? у грі немає {rel} — пропускаю', 'warn')
                continue
            keep = os.path.join(bk, rel)
            if os.path.exists(dst) and not os.path.exists(keep) and not ours:
                os.makedirs(os.path.dirname(keep), exist_ok=True)
                shutil.copy2(dst, keep)
            tmp = dst + '.new'
            for attempt in range(10):
                try:
                    if _same_drive(src, dst):
                        # той самий диск — просто переносимо готовий файл (миттєво,
                        # без ще одного запису сотень МБ); out\ після цього не потрібен
                        os.replace(src, dst)
                    else:
                        shutil.copy2(src, tmp)           # інший диск — копія й підміна
                        os.replace(tmp, dst)
                    break
                except PermissionError:
                    # файл на мить зайняв антивірус / Steam — ще раз за секунду
                    if attempt < 9:
                        time.sleep(1)
                        continue
                    ex = sys.exc_info()[1]
                except OSError as e:
                    ex = e
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                half = (f'\nУВАГА: {len(done)} з {len(files)} файлів уже замінено ({", ".join(done[:5])}'
                        f'{"…" if len(done) > 5 else ""}), а цей — ні: гра зараз наполовину перекладена '
                        '(напр. текст новий, а шрифт старий — без і є ґ). Зачекай хвилину й натисни «2» '
                        'ще раз або «Повернути оригінали гри».') if done else '\nУ теці гри нічого не змінено.'
                raise RuntimeError(f'Не вдалося записати {rel}: {ex}\n'
                                   'Файл зайнятий: гра ще закривається, її запущено знову або його '
                                   'тримає інша програма (антивірус, Steam).' + half)
            self.say(f'  -> {rel}', 'dim')
            done.append(rel)
            n += 1
        return n

    def _wait_unlocked(self, paths, wait=30):
        """Чекати (до `wait` с), поки файли гри можна відкрити на запис; ні — помилка до
        будь-яких змін."""
        t0 = time.time()
        said = False
        while True:
            busy = []
            for p in paths:
                if not os.path.exists(p):
                    continue
                try:
                    with open(p, 'r+b'):
                        pass
                except PermissionError:
                    busy.append(p)
                except OSError:
                    pass
            if not busy:
                return
            if time.time() - t0 > wait:
                names = ', '.join(os.path.basename(p) for p in busy[:5])
                raise RuntimeError(f'Файли гри зайняті: {names}. Гра ще закривається, її запущено '
                                   'знову або файли тримає інша програма (антивірус, Steam).\n'
                                   'У теці гри нічого не змінено. Зачекай хвилину й натисни кнопку ще раз '
                                   '(якщо не допоможе — перезавантаж комп\'ютер).')
            if not said:
                self.say(f'  файл гри ще зайнятий ({os.path.basename(busy[0])}) — чекаю, поки звільниться…',
                         'warn')
                self.set_status('Чекаю, поки гра відпустить файли…')
                said = True
            time.sleep(1)

    def do_restore(self):
        _w, _x, _o, bk = self.dirs()
        self._require_closed()
        if not os.path.isdir(bk):
            raise RuntimeError('Резервних копій немає.')
        self._require_originals()       # інакше «повернемо» в гру перекладений файл
        dest, n = self.data_dir(), 0
        files = []
        for dp, _d, fs in os.walk(bk):
            for fn in fs:
                files.append(os.path.join(dp, fn))
        self._wait_unlocked([os.path.join(dest, os.path.relpath(s, bk)) for s in files])
        for k, src in enumerate(files, 1):
            self.step_nc(k, len(files), 'Повертаю оригінали')
            dst = os.path.join(dest, os.path.relpath(src, bk))
            tmp = dst + '.new'
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            n += 1
        # переклад рядків ПК-порту (MSK) лежить окремим файлом поруч з exe — прибрати,
        # інакше dinput8.dll і далі підставлятиме українські рядки
        extra = os.path.join(dest, 'ua_strings.bin')
        if os.path.exists(extra):
            os.remove(extra)
            n += 1
        self.set_status(f'Повернуто оригіналів: {n}.')
        self.say(f'Повернуто оригіналів: {n}.', 'ok')

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

    def do_installer(self, path):
        """Переклад, що зараз у грі (після «2»), — у zip з «Встановити переклад.bat»."""
        import installer
        g = self.cur['game']
        _w, _x, _o, bk = self.dirs()
        root, dest = self.root_dir(), self.data_dir()
        if not os.path.isdir(bk):
            raise RuntimeError('У грі ще немає перекладу. Спершу натисни «2. Залити переклад у гру».')
        game = dict(GAMES[g], data=os.path.relpath(dest, root) if dest != root else '')
        # наші файли без оригіналу — ті самі, що _copy_into_game кладе без бекапу
        extra = []
        if os.path.exists(os.path.join(dest, 'ua_strings.bin')):      # MSK: рядки exe + патч
            extra += ['ua_strings.bin', 'dinput8.dll']
        note = ''
        if g == 'crystar':      # мова тексту — налаштування самої гри, переклад лише в одному слоті
            slot = self.settings.get('crystar_slot_in_game') or self.cur['slot']
            note = ('У налаштуваннях гри має стояти мова тексту ' +
                    ('English' if slot == 'en' else 'Japanese (日本語)') + ' — переклад записано замість неї.')
        self.say(f'\nЗбираю інсталятор: {path}')
        self.set_status('Збираю інсталятор…')
        n, size = installer.build(path, game, dest, bk, extra, note, VERSION,
                                  step=self.step, say=lambda t: self.say(t, 'dim'))
        self.set_status(f'Інсталятор готовий: {human(size)}.')
        self.say(f'Готово: файлів гри — {n}, розмір — {human(size)}.\n'
                 'Цей zip можна надсилати: людина розпаковує його й запускає «Встановити переклад.bat» '
                 '(гру скрипт знайде сам).', 'ok')
        if note:
            self.say('  ' + note, 'dim')
        try:
            subprocess.Popen(['explorer', '/select,', path])
        except OSError:
            pass

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
        import project
        if project.enabled(xl):
            pr = project.Project(g, work, xl).progress()
            pr['prev'] = sheets._snapshot(work, pr['done'], pr['total'])
        else:
            pr = sheets.progress(xl, work)
        for line in sheets.report(pr):
            self.say(line, 'mono')
        if pr.get('words'):                  # редактор: ще й слова (в оригіналі)
            wp = 100 * pr['words_done'] / pr['words']
            self.say(f'Слів (в оригіналі): перекладено {pr["words_done"]:,} з {pr["words"]:,} '
                     f'({wp:.1f}%). Рядки «не перекладати» не рахуються.'.replace(',', ' '), 'mono')
        if pr.get('ja'):                     # японський оригінал: знаки без пробілів
            jp = 100 * pr['ja_done'] / pr['ja']
            n = lambda v: f'{v:,}'.replace(',', ' ')
            self.say(f'Японською (знаки без пробілів): {n(pr["ja"])} знаків, з них перекладено '
                     f'{n(pr["ja_done"])} ({jp:.1f}%).', 'mono')
        if pr.get('uniq'):                   # редактор: обсяг без повторів (для ціни перекладу)
            n = lambda v: f'{v:,}'.replace(',', ' ')
            u = pr['uniq']
            line = (f'Без повторів (однаковий рядок — один раз): {n(u["rows"])} рядків, '
                    f'{n(u["words"])} слів')
            if u.get('ja'):
                line += f', {n(u["ja"])} японських знаків'
            line += f'; перекладено {n(u["done"])} рядків, {n(u["words_done"])} слів'
            if u.get('ja'):
                line += f', {n(u["ja_done"])} знаків'
            self.say(line + '.', 'mono')
            h = pr.get('hidden') or {}
            parts = [f'{lab}: {n(h[w]["rows"])} рядків, {n(h[w]["words"])} слів'
                     + (f', {n(h[w]["ja"])} яп. знаків' if h[w]['ja'] else '')
                     for w, lab in (('keep', '«не перекладати»'), ('key', 'службові ключі'),
                                    ('empty', 'порожній оригінал')) if h.get(w, {}).get('rows')]
            if parts:
                self.say('Не рахуються (приховано): ' + '; '.join(parts) + '.', 'mono')
        self._remember_pc(pr['done'], pr['total'])
        pc = 100 * pr['done'] / pr['total'] if pr['total'] else 0
        self.set_status(f'Перекладено {pr["done"]}/{pr["total"]} ({pc:.1f}%).')

    def do_check(self):
        import sheets, common, project
        work, xl, _o, _b = self.dirs()
        if project.enabled(xl):
            self._read_translation()
        elif os.path.isdir(xl):
            sheets.read_into_work(xl, work, self.step)
        done, total = common.stats(work)
        self._remember_pc(done, total)
        self.say(f'\nПерекладено {done} з {total} рядків '
                 f'({100 * done / total if total else 0:.1f}%).', 'head')
        warn, hidden = sheets.split_approved(sheets.validate(work, terms=__import__('glossary').load(xl), widths=sheets.load_widths(xl), rows=sheets.load_row_limits(xl)), xl)
        if not warn:
            self.say('Попереджень немає — усе чисто.', 'ok')
        else:
            self.say(f'Попереджень: {len(warn)} (клік — відкрити рядок, '
                     'правий клік — затвердити: це не помилка)', 'warn')
        self._say_warnings(warn, 200, hidden)
        self.set_status(f'Перекладено {done}/{total}.')

    def _say_warnings(self, warn, limit, hidden=(), approved=False):
        import sheets
        src = getattr(sheets.validate, 'src', {})
        tr = getattr(sheets.validate, 'tr', {})
        for s, i, w in warn[:limit]:
            self.say_link(f'  {s} [{i}] — {w}',
                          (s, i, src.get((s, i)), w, tr.get((s, i), ''), approved),
                          'dim' if approved else 'warn')
        if len(warn) > limit:
            self.say(f'  …і ще {len(warn) - limit}', 'warn')
        if hidden:
            self._hidden = list(hidden)
            self.q.put(('act', (f'  Затверджено й приховано: {len(hidden)} — показати', 'dim',
                                self._show_hidden)))

    def _show_hidden(self):
        hidden = getattr(self, '_hidden', [])
        self.say('\nЗатверджені попередження (правий клік — скасувати затвердження):', 'dim')
        self._say_warnings(hidden, 500, approved=True)

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

    def _goto(self, target):
        """Клік по попередженню: відкрити книгу на рядку з цим перекладом."""
        game, source, eid, src = target
        if self.busy:
            self.set_status('Зачекай, поки закінчиться поточна дія.')
            return
        xl = os.path.join(XLSX, GAMES[game]['folder'])
        import project
        if project.enabled(xl):
            if game != self.game.get():
                self.set_status('Це попередження іншої гри — перемкни гру вгорі.')
                return
            self.open_editor(goto=f'{source}\t{eid}')
            return
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

    def _xl_now(self):
        """Тека перекладу гри, вибраної вгорі (не лише під час дії)."""
        return os.path.join(XLSX, GAMES[self.game.get()]['folder'])

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
        dark_titlebar(win, self.theme == 'dark')

    def open_feedback(self, ctx=None):
        """Вікно «Повідомити про проблему» (feedback_window.py); ctx — рядок, звідки відкрито."""
        try:
            import importlib, feedback, feedback_window
            importlib.reload(feedback)
            importlib.reload(feedback_window)
            feedback_window.FeedbackWindow(self, ctx)
        except Exception as e:
            messagebox.showerror('Звернення', f'{e}\n\n{traceback.format_exc()}')

    def say_sent(self, link):
        if link:
            import webbrowser
            self.q.put(('act', ('Звернення надіслано ✓ — відкрити тему в Telegram', 'ok',
                                lambda: webbrowser.open(link))))
        else:
            self.say('Звернення надіслано ✓', 'ok')

    def _feedback_flush(self):
        """Надіслати звернення, що чекають у черзі (без інтернету чи до підключення);
        при старті й далі раз на 5 хвилин, поки черга не порожня."""
        import feedback
        if not feedback.pending():
            return
        if not feedback.load_key():
            self.say(f'Звернень чекає відправки: {len(feedback.pending())} — підключи відправку '
                     '(«Повідомити про проблему…» → «Підключити…»).', 'warn')
            return

        def job():
            try:
                sent, left, links = feedback.flush(self.say)
            except Exception as ex:                          # noqa: BLE001
                sent, left, links = 0, 1, []
                self.say(f'Звернення не надіслано: {ex}', 'err')
            for link in links:
                self.q.put(('call', lambda l=link: self.say_sent(l)))
            if left:
                self.q.put(('call', lambda: self.after(5 * 60 * 1000, self._feedback_flush)))
        threading.Thread(target=job, daemon=True).start()

    # ------------------------------------------------------ редактор перекладу
    def get_project(self):
        """Спільний проєкт (project.py) поточної гри: один на всі вікна, щоб
        редактор і вікно написів не затирали правки одне одного."""
        import project
        g = self.cur['game']
        work, xl, _o, _b = self.dirs()
        pr = getattr(self, '_project', None)
        if pr is None or pr.game != g:
            pr = self._project = project.Project(g, work, xl)
        return pr

    def _flush_editor(self):
        """Правки з відкритого редактора — на диск (перед «1», «2», перевіркою)."""
        win = getattr(self, 'editor_win', None)
        if win is not None and win.winfo_exists():
            win.flush()
        pr = getattr(self, '_project', None)
        if pr is not None and pr.pending():
            pr.save()

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

    def _migrate(self):
        """Перший перехід на редактор (у фоні): книги -> work -> переклад.json."""
        import sheets, project
        work, xl, _o, _b = self.dirs()
        self.say('Переношу переклад із книг Excel у програму…')
        if os.path.isdir(xl):
            st = sheets.read_into_work(xl, work, self.step)
            self.say(f'  з книг прочитано перекладів: {st["translated"]}', 'dim')
        pr, (filled, detached) = project.Project.create(self.cur['game'], work, xl)
        self._project = pr
        done = sum(1 for r in pr.rows if r['e'].get('tr'))
        self.say(f'  готово: перекладено {done} рядків; однакових заповнено {filled}, '
                 f'залишено окремими (різний переклад) {detached}.', 'ok')
        self.say(f'  переклад тепер у файлі {project.store_path(xl)}', 'dim')
        self.q.put(('call', lambda: (self._refresh_editor_hint(), self.open_editor())))

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


if __name__ == '__main__':
    App().mainloop()
