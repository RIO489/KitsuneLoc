# -*- coding: utf-8 -*-
"""Робоча логіка KitsuneLoc без вікна: шляхи, ігри (GAMES), налаштування, пошук ігор у Steam і
клас Core — кроки «1» / «2» / «3», перевірка й прогрес, резервні копії й запис у гру, повернення
оригіналів, інсталятор, перехід на редактор, журнал.

Core — домішка до класу вікна (qtui/main.py: MainWindow; старе Tk-вікно — Переклад-старе.pyw). Вікно
дає: self.cur (знімок стану на час дії), self.settings, self.q (черга подій потік -> вікно: log, link,
act, step, status, call, done, chibi, books), self.cancel, self.busy, self.game (.get()), self.badge і
self.editor_hint (.configure(text=, foreground=)), _show_books, book_file, _refresh_title, open_editor,
_after(мс, функція). Методи Core працюють і з фонового потоку — віджетів не чіпають.
"""
import os, re, sys, json, time, shutil, threading, traceback, subprocess, queue
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')   # numpy (через openpyxl) інакше резервує ~30 МБ на кожне ядро
VERSION = '3.1'
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


class Core:
    """Робоча логіка вікна програми (домішка; див. опис модуля)."""

    # для вікон в окремих модулях (feedback_window): цей файл не імпортується
    GAMES, VERSION = GAMES, VERSION

    steam_libraries = staticmethod(steam_libraries)

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

    def _refresh_badge(self):
        """Що зараз лежить у грі — переклад чи оригінал (порівнюємо з backup)."""
        c = __import__('themes').get(self.theme)
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

    def _remember_pc(self, done, total):
        pc = f'{100 * done / total:.1f}%' if total else None
        d = self.settings.setdefault('pc', {})
        if pc:
            d[self.game.get()] = pc
            save_settings(self.settings)
        self._refresh_title()

    def _textures_on(self, game=None):
        return bool((self.settings.get('textures') or {}).get(game or self.game.get(), True))

    def _mt_in_game(self, game=None):
        return bool((self.settings.get('mt_in_game') or {}).get(game or self.game.get(), False))

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

    DEPS = (('openpyxl', 'openpyxl'), ('UnityPy', 'UnityPy'), ('PIL', 'Pillow'),
            ('texture2ddecoder', 'texture2ddecoder'), ('etcpak', 'etcpak'),
            ('sv_ttk', 'sv-ttk'), ('fontTools', 'fonttools'), ('numpy', 'numpy'),
            ('PySide6', 'PySide6'))             # вікно програми (Qt, з 3.1)
    # anthropic (Claude) — не тут: потрібна лише для машинного перекладу через Claude, ставиться
    # кнопкою «Встановити» у вікні машинного перекладу (mt/engines.install_anthropic)

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
        done = self._read_translation()
        lent = self._lend_mt(g, xl)
        # скільки чернеток пішло в гру останнім «2» — інсталятор попередить
        self.settings.setdefault('mt_in_last_import', {})[g] = lent[2] if lent else 0
        save_settings(self.settings)
        if not done and not lent:
            raise RuntimeError('Перекладу ще немає — нема чого заливати.')
        try:
            self._import_build(g, work, xl, out, bk)
        finally:
            if lent:                    # у work\ знову лише справжній переклад
                lent[0].return_mt(lent[1])

    def _lend_mt(self, g, xl):
        """Галочка «Підставляти машинний»: чернетки -> work\\ на час збирання. -> (проєкт,
        source-и) або None. Без редактора (лише книги Excel) машинного немає."""
        import project
        pr = getattr(self, '_project', None)
        if not self._mt_in_game(g) or not project.enabled(xl) or pr is None or pr.game != g:
            return None
        n, sources = pr.lend_mt()
        if not n:
            return None
        self.say(f'  Увага: машинний переклад підставлено в {n} рядків (галочка «Підставляти '
                 'машинний…») — це неперевірена чернетка, лише для пробного проходу.', 'warn')
        return pr, sources, n

    def _import_build(self, g, work, xl, out, bk):
        """Перевірка, збирання файлів гри й копія в гру (переклад уже в work\\)."""
        import sheets
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

    def do_installer(self, path):
        """Переклад, що зараз у грі (після «2»), — у zip з «Встановити переклад.bat»."""
        import installer
        g = self.cur['game']
        _w, _x, _o, bk = self.dirs()
        root, dest = self.root_dir(), self.data_dir()
        if not os.path.isdir(bk):
            raise RuntimeError('У грі ще немає перекладу. Спершу натисни «2. Залити переклад у гру».')
        n_mt = (self.settings.get('mt_in_last_import') or {}).get(g, 0)
        if n_mt:
            self.say(f'Увага: в останнє «2» підставлено машинний переклад у {n_mt} рядків — вони потраплять '
                     'в інсталятор неперевіреними. Щоб їх не було: вимкни «Підставляти машинний…», '
                     'натисни «2» і створи інсталятор знову.', 'warn')
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
        if pr.get('reviewed') or pr.get('mt'):          # рівні перекладу (з 3.0)
            n = lambda v: f'{v:,}'.replace(',', ' ')
            self.say(f'Вичитано: {n(pr.get("reviewed", 0))} з {n(pr["done"])} перекладених рядків; '
                     f'лише машинний (перекладу ще немає): {n(pr.get("mt", 0))}.', 'mono')
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

    def _xl_now(self):
        """Тека перекладу гри, вибраної вгорі (не лише під час дії)."""
        return os.path.join(XLSX, GAMES[self.game.get()]['folder'])

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
                self.q.put(('call', lambda: self._after(5 * 60 * 1000, self._feedback_flush)))
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
