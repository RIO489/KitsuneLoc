# -*- coding: utf-8 -*-
"""Стеження за грою: що зараз на екрані — який це рядок перекладу.

Кадр вікна гри -> Windows OCR (textscan_ocr.ps1) -> нечіткий пошук розпізнаного
серед оригіналів (англійський текст на екрані: рядок не перекладено або переклад
ще не залито в гру) і перекладів (український: переклад у грі є).

Пошук (`Index`): текст зводиться до «скелета» — малі літери й цифри, схожі
латинські й кириличні літери злиті (OCR плутає «а»/«a», «і»/«i»), решта
відкинута. Кандидати — за спільними триграмами, остаточно — difflib.
Англійський OCR читає українську латиницею, тож українського OCR не треба:
на кадрах шрифтом гри знаходиться ~93% реплік і англійських, і українських.

Кадр (`grab`): PrintWindow з PW_RENDERFULLCONTENT — вікно гри може бути
перекрите іншими; чорний кадр (повноекранний режим) — знімок ділянки екрана.

Розпізнавання: для Neptunia — свій розпізнавач шрифтами гри (`GameFontOcr`,
fontocr.py), для решти — Windows OCR (`Ocr`).
"""
import collections, ctypes, difflib, json, os, re, subprocess, tempfile, threading, time
from ctypes import wintypes

# «Візуальний скелет». Англійський Windows OCR читає українську латиницею, але
# систематично: «такі Богині» -> «TaKi 50rMHi», И -> VI/M, Ш -> W/LU, Я -> 9.
# Тож і кирилицю, і латиницю зводимо до тих самих класів «схожих на вигляд» знаків.
_MULTI = [('ы', 'bi'), ('ю', 'io'), ('щ', 'w'), ('ш', 'w'),
          ('vi', 'm'), ('vl', 'm'), ('lu', 'w'), ('luo', 'w'), ('111', 'w'), ('j1', 'n'), ('jl', 'n')]
_LOOK = str.maketrans({
    # кирилиця -> латинська «схожа»
    'а': 'a', 'б': '6', 'в': 'b', 'г': 'r', 'ґ': 'r', 'д': 'a', 'е': 'e', 'є': 'e', 'ё': 'e',
    'ж': 'k', 'з': '3', 'и': 'm', 'і': 'i', 'ї': 'i', 'й': 'm', 'к': 'k', 'л': 'n', 'м': 'm',
    'н': 'h', 'о': 'o', 'п': 'n', 'р': 'p', 'с': 'c', 'т': 't', 'у': 'y', 'ф': 'f', 'х': 'x',
    'ц': 'y', 'ч': 'y', 'ь': 'b', 'ъ': 'b', 'э': 'e', 'я': '9',
    # латиниця й цифри, які OCR плутає між собою
    'u': 'y', 'v': 'y', 'l': 'i', 'j': 'i', '1': 'i', '0': 'o', '5': '6', 'g': 'r'})
_CODES = re.compile(r'#[A-Za-z]+(?:\[[^\]]*\])?|%[-+0#]*\d*(?:\.\d+)?(?:ll|l|h)?[a-zA-Z%]|<[A-Z]+>')
_KEEP = re.compile(r'[^\w]+|_')


def skeleton(text):
    """Текст для порівняння: без кодів, пунктуації, регістру й різниці схожих
    на вигляд літер (кирилиця й латиниця — в одних класах)."""
    t = _KEEP.sub('', _CODES.sub(' ', text or '').lower())
    for a, b in _MULTI:
        t = t.replace(a, b)
    return t.translate(_LOOK)


# частковий збіг (на екрані — шматок довгого рядка) лише для довших фраз: коротка
# «He nepeKnaaeH0» інакше «знаходилась» усередині випадкової довгої репліки
PARTIAL_MIN = 20
# короткі фрази — лише майже точний збіг: сміття з тла («Combined», «Vanargandr»)
# інакше «знаходилось» серед назв предметів
SHORT, SHORT_RATIO = 15, 0.8
# з якої схожості довгий рядок «задає сцену» (далі погано розпізнане шукаємо в ній)
SCENE_RATIO = 0.7
DIALOG = re.compile(r'/event/script/|/EVENT/DATA/|/Event/', re.I)   # файли сцен (як sheets._DIALOG + Crystar)


def _grams(s):
    return {s[i:i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}


class Index:
    """Пошук рядків за розпізнаним текстом. items: [(ключ, вид, текст)],
    вид — 'src' (оригінал) або 'tr' (переклад)."""

    MIN_LEN = 4                   # коротше — надто багато випадкових збігів

    def __init__(self, items):
        self.texts = {}                                  # скелет -> [(ключ, вид)]
        for key, kind, text in items:
            sk = skeleton(text)
            if len(sk) >= self.MIN_LEN:
                self.texts.setdefault(sk, []).append((key, kind))
        self.grams = collections.defaultdict(list)
        self.by_source = collections.defaultdict(set)    # файл гри -> скелети його рядків
        for sk, keys in self.texts.items():
            for g in _grams(sk):
                self.grams[g].append(sk)
            for key, _kind in keys:
                self.by_source[key.split('\t')[0]].add(sk)

    def find_near(self, text, sources, min_ratio=0.6):
        """Пошук лише серед рядків цих файлів (сцена, яку щойно впізнали): кандидатів
        сотня, а не десятки тисяч, тож годиться й гірше розпізнане (похилий шрифт
        історії діалогів англійський OCR читає погано)."""
        q = skeleton(text)
        if len(q) < self.MIN_LEN:
            return []
        if len(q) < SHORT:
            min_ratio = max(min_ratio, SHORT_RATIO)
        out = []
        for src in sources:
            for sk in self.by_source.get(src, ()):
                m = difflib.SequenceMatcher(None, q, sk, autojunk=False)
                if m.real_quick_ratio() < min_ratio:
                    continue
                r = m.ratio()
                if len(sk) > len(q) * 1.3 and len(q) >= PARTIAL_MIN:
                    r = max(r, 0.92 * _partial(q, sk))
                if r >= min_ratio:
                    keys = [(k, kind) for k, kind in self.texts[sk] if k.split('\t')[0] == src]
                    out.append((r, sk, keys))
        out.sort(key=lambda x: -x[0])
        return out

    def find(self, text, min_ratio=0.62, top=25):
        """-> [(схожість, скелет, [(ключ, вид)])] найкращі збіги, від кращого."""
        q = skeleton(text)
        if len(q) < self.MIN_LEN:
            return []
        if q in self.texts:                              # точний збіг — одразу
            return [(1.0, q, self.texts[q])]
        if len(q) < SHORT:
            min_ratio = max(min_ratio, SHORT_RATIO)
        qg = _grams(q)
        cnt = collections.Counter()
        for g in qg:
            for sk in self.grams.get(g, ()):
                cnt[sk] += 1
        out = []
        for k, (sk, n) in enumerate(cnt.most_common(top)):
            r = difflib.SequenceMatcher(None, q, sk, autojunk=False).ratio()
            # частковий — лише для перших кандидатів, де спільних триграм ≥ половини
            if k < 8 and n * 2 >= len(qg) and len(sk) > len(q) * 1.3 and len(q) >= PARTIAL_MIN:
                # на екрані лише частина рядка (перенос, прокрутка): найкращий шматок
                # такої ж довжини; трохи нижче за повний збіг, щоб той вигравав
                r = max(r, 0.92 * _partial(q, sk))
            if r >= min_ratio:
                out.append((r, sk, self.texts[sk]))
        out.sort(key=lambda x: -x[0])
        return out


def _partial(q, sk):
    n, best = len(q), 0.0
    m = difflib.SequenceMatcher(None, q, '', autojunk=False)
    for i in range(0, len(sk) - n + 1, 4):
        m.set_seq2(sk[i:i + n])
        if m.real_quick_ratio() > best and m.quick_ratio() > best:
            best = max(best, m.ratio())
    return best


# ================================================================== вікно гри
_u32 = ctypes.WinDLL('user32', use_last_error=True)
_g32 = ctypes.WinDLL('gdi32')
_k32 = ctypes.WinDLL('kernel32')
_ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_u32.EnumWindows.argtypes = [_ENUM, wintypes.LPARAM]
_u32.GetDC.restype = wintypes.HDC
_g32.CreateCompatibleDC.restype = wintypes.HDC
_g32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_g32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_g32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_g32.SelectObject.restype = wintypes.HGDIOBJ
_g32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_g32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_g32.DeleteDC.argtypes = [wintypes.HDC]
_g32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
_u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_u32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
try:                                                   # Windows 10 1607+
    _u32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
    _u32.GetWindowDpiAwarenessContext.argtypes = [wintypes.HWND]
    _u32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    _u32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    _DPI_CTX = True
except AttributeError:
    _DPI_CTX = False
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                            ctypes.POINTER(wintypes.DWORD)]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]


def _exe_of(pid):
    h = _k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ''
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.basename(buf.value)
        return ''
    finally:
        _k32.CloseHandle(h)


def find_window(exe):
    """Найбільше видиме вікно процесу exe (без урахування регістру) або None."""
    exe = exe.lower()
    best = [None, 0]

    def cb(hwnd, _l):
        if not _u32.IsWindowVisible(hwnd) or _u32.IsIconic(hwnd):
            return True
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if _exe_of(pid.value).lower() != exe:
            return True
        r = wintypes.RECT()
        _u32.GetClientRect(hwnd, ctypes.byref(r))
        area = r.right * r.bottom
        if area > best[1]:
            best[:] = [hwnd, area]
        return True

    _u32.EnumWindows(_ENUM(cb), 0)
    return best[0]


class _BMIH(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG), ('biHeight', wintypes.LONG),
                ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD),
                ('biCompression', wintypes.DWORD), ('biSizeImage', wintypes.DWORD),
                ('biXPelsPerMeter', wintypes.LONG), ('biYPelsPerMeter', wintypes.LONG),
                ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD)]


def grab(hwnd):
    """Кадр клієнтської частини вікна (PIL RGB) або None.

    Розміри й координати беремо в DPI-контексті САМОГО вікна гри. Наша програма для
    Windows «не знає DPI», тож за масштабу 150% розмір вікна гри, яка сама враховує DPI
    (Mary Skelter), приходив у логічних пікселях (1280×720 замість 1920×1080), а PrintWindow
    малював фізичні — у кадр потрапляв лише верхній лівий кут. Гра, що DPI не враховує
    (Neptunia), так і лишається в логічних — як було."""
    old_ctx = None
    if _DPI_CTX:
        ctx = _u32.GetWindowDpiAwarenessContext(hwnd)
        if ctx:
            old_ctx = _u32.SetThreadDpiAwarenessContext(ctx)
    try:
        return _grab(hwnd)
    finally:
        if old_ctx:
            _u32.SetThreadDpiAwarenessContext(old_ctx)


def _grab(hwnd):
    from PIL import Image, ImageGrab
    r = wintypes.RECT()
    if not _u32.GetClientRect(hwnd, ctypes.byref(r)) or r.right < 32 or r.bottom < 32:
        return None
    w, h = r.right, r.bottom
    wdc = _u32.GetDC(hwnd)
    mdc = _g32.CreateCompatibleDC(wdc)
    bmp = _g32.CreateCompatibleBitmap(wdc, w, h)
    old = _g32.SelectObject(mdc, bmp)
    img = None
    try:
        # PW_CLIENTONLY | PW_RENDERFULLCONTENT: і вікна DirectX, і перекрите іншими
        if _u32.PrintWindow(hwnd, mdc, 3):
            bi = _BMIH(ctypes.sizeof(_BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            buf = ctypes.create_string_buffer(w * h * 4)
            _g32.SelectObject(mdc, old)                  # GetDIBits — з невибраним бітмапом
            if _g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0):
                img = Image.frombuffer('RGB', (w, h), buf.raw, 'raw', 'BGRX', 0, 1)
    finally:
        _g32.SelectObject(mdc, old)
        _g32.DeleteObject(bmp)
        _g32.DeleteDC(mdc)
        _u32.ReleaseDC(hwnd, wdc)
    if img is None or img.convert('L').getextrema()[1] < 8:
        # повноекранний режим: PrintWindow дає чорне — беремо ділянку екрана. Pillow знімає
        # екран у фізичних пікселях — тож і рамку вікна беремо у фізичних (контекст -4:
        # PER_MONITOR_AWARE_V2), незалежно від того, чи гра знає про DPI
        prev = _u32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4)) if _DPI_CTX else None
        try:
            _u32.GetClientRect(hwnd, ctypes.byref(r))
            pt = wintypes.POINT(0, 0)
            _u32.ClientToScreen(hwnd, ctypes.byref(pt))
        finally:
            if prev:
                _u32.SetThreadDpiAwarenessContext(prev)
        try:
            img = ImageGrab.grab(bbox=(pt.x, pt.y, pt.x + r.right, pt.y + r.bottom), all_screens=True)
        except OSError:
            return None
    return img


# ================================================================== OCR
class Ocr:
    """Постійний процес Windows OCR (textscan_ocr.ps1 -serve): кадр за кадром
    без перезапуску PowerShell (запуск — ~1 с)."""

    PS1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'textscan_ocr.ps1')

    def __init__(self):
        self.proc = None
        self.dir = tempfile.mkdtemp(prefix='kitsuneloc_watch_')
        self.n = 0

    def _start(self):
        self.proc = subprocess.Popen(
            ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', self.PS1, '-serve'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=0x08000000)                    # CREATE_NO_WINDOW

    def lines(self, img):
        """[(текст рядка, (x0, y0, x1, y1))] з картинки."""
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        # кожен кадр — свій файл: Windows OCR ще може тримати попередній відкритим,
        # і запис поверх нього давав «[Errno 22] Invalid argument»
        self.n += 1
        p = os.path.join(self.dir, f'frame{self.n}.png')
        img.save(p)
        self._clean()
        self.proc.stdin.write((p + '\n').encode('utf-8'))
        self.proc.stdin.flush()
        if not self.proc.stdout.readline():
            raise RuntimeError('Windows OCR не відповідає')
        with open(p + '.json', encoding='utf-8-sig') as f:
            j = json.load(f)
        lines = j.get('lines') or []
        if isinstance(lines, dict):                      # ConvertTo-Json: один елемент — не список
            lines = [lines]
        out = []
        for ln in lines:
            ws = ln.get('words') or []
            if isinstance(ws, dict):
                ws = [ws]
            if not ws:
                continue
            box = (min(w['x'] for w in ws), min(w['y'] for w in ws),
                   max(w['x'] + w['w'] for w in ws), max(w['y'] + w['h'] for w in ws))
            out.append((ln.get('text', ''), box))
        return out

    def _clean(self, keep=2):
        """Прибрати старі кадри (зайняті OCR — наступного разу)."""
        for fn in os.listdir(self.dir):
            m = re.match(r'frame(\d+)\.png', fn)
            if m and int(m.group(1)) <= self.n - keep:
                for f in (fn, fn + '.json'):
                    try:
                        os.remove(os.path.join(self.dir, f))
                    except OSError:
                        pass

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(b'quit\n')
                self.proc.stdin.flush()
                self.proc.wait(3)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
        self.proc = None
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


class GameFontOcr:
    """Свій розпізнавач шрифтами гри (fontocr.py) з тим самим lines(), що в Ocr.
    Шрифти — усі з профілю гри рушія (Neptunia: advfont — головне вікно діалогу, msgfont —
    історія, імена, меню; MSK: msgfont). На 133 кадрах Neptunia знаходить рядків удвічі
    більше, ніж англійський Windows OCR (той українську читає латинськими «двійниками»
    або не читає зовсім). На кадрах MSK ще не перевірявся."""

    def __init__(self, game, backup_dir):
        import fontocr, preview
        from compileheart import profiles
        prof = profiles.get(game)
        if prof is None:
            raise ValueError('шрифти цієї гри для розпізнавання не підготовлено')
        self.ocr = fontocr.FontOCR({name: preview.GameFont(game, backup_dir, name) for name in prof.FONTS})

    def lines(self, img):
        return [(t, b) for t, b, _m in self.ocr.read_frame(img)]

    def close(self):
        pass


# ================================================================== розбір кадру
def blocks(lines):
    """Рядки OCR -> абзаци: сусідні рядки один під одним, близько, з близьким
    лівим краєм. -> [(текст, рамка, [(текст рядка, рамка)])]."""
    out = []
    for text, b in sorted(lines, key=lambda l: (l[1][1], l[1][0])):
        h = max(1, b[3] - b[1])
        for blk in out:
            bb, parts = blk
            last = parts[-1][1]
            if 0 <= b[1] - last[3] < 1.2 * h and abs(b[0] - bb[0]) < 3 * h:
                parts.append((text, b))
                blk[0] = (min(bb[0], b[0]), bb[1], max(bb[2], b[2]), b[3])
                break
        else:
            out.append([b, [(text, b)]])
    return [(' '.join(t for t, _b in parts), bb, parts) for bb, parts in out]


def looks_like_text(text):
    """Чи схоже розпізнане на справжній текст (а не на візерунок тла): кілька
    «слів» з літер і досить довгий скелет."""
    words = [w for w in re.findall(r'\w+', text) if sum(c.isalpha() for c in w) >= 2]
    return len(words) >= 3 and len(skeleton(text)) >= 15


def match(index, lines, min_ratio=0.62, near=()):
    """Що з рядків перекладу на кадрі: [(схожість, [(ключ, вид)], рамка, текст OCR)].
    Спершу абзац цілком; не знайшовся певно — кожен рядок окремо (ім'я мовця над
    вікном, кілька підписів поруч). Абзац, схожий на текст, але не знайдений ніде, —
    з порожнім списком ключів: у грі текст, якого вже немає в перекладі (старий
    переклад — не залито «2») або якого програма не знає. near — файли гри (сцени),
    які щойно впізнано певно: там шукаємо й погано розпізнане."""
    def look(t):
        res = index.find(t, min_ratio)
        if near and (not res or res[0][0] < 0.8):
            # не певно — спершу серед рядків сцени, яку щойно впізнали
            n = index.find_near(t, near)
            if n:
                return n
        return res

    hits = []
    for text, box, parts in blocks(lines):
        res = look(text)
        if res and (res[0][0] >= 0.8 or len(parts) == 1):
            hits.append((res[0][0], res[0][2], box, text))
            continue
        got = False
        for t, b in parts:
            r = look(t)
            if r:
                hits.append((r[0][0], r[0][2], b, t))
                got = True
        if res and not got:
            hits.append((res[0][0], res[0][2], box, text))
        elif not res and not got and looks_like_text(text):
            hits.append((0.0, [], box, text))
    return hits


SAME = 1.5             # середня різниця кадрів 64×36 (0..255), до якої кадр «той самий»
STABLE_GAP, STABLE_TRIES = 0.25, 6     # очікування, поки репліка «додрукується»: до 1,5 с


class Watcher(threading.Thread):
    """Фоновий цикл: кадр -> OCR -> пошук. on_frame(стан, картинка, влучання),
    стан — 'nogame' | 'same' | 'ok' | 'err:<текст>'. OCR — лише тоді, коли кадр
    помітно змінився (гра стоїть на тому самому екрані — нічого не робимо)."""

    def __init__(self, exe, index, on_frame, interval=1.0, ocr=None):
        super().__init__(daemon=True)
        self.exe, self.index, self.on_frame, self.interval = exe, index, on_frame, interval
        self.stop_ev = threading.Event()
        self.ocr = ocr or Ocr()                 # свій розпізнавач (GameFontOcr) або Windows OCR
        self.last = None
        self.near = []                  # файли гри, звідки щойно певно впізнано рядки
        self.save_dir = None            # тека — зберігати кадри й розпізнане (налагодження)

    def _save(self, img, lines):
        try:
            os.makedirs(self.save_dir, exist_ok=True)
            name = time.strftime('%Y%m%d-%H%M%S') + f'-{int(time.time() * 1000) % 1000:03d}'
            img.save(os.path.join(self.save_dir, name + '.png'))
            with open(os.path.join(self.save_dir, name + '.json'), 'w', encoding='utf-8') as f:
                json.dump({'lines': lines}, f, ensure_ascii=False)
        except OSError:
            pass

    def _remember_scene(self, hits):
        """Сцена — з досить певних влучань (довгий текст); пам'ятаємо дві останні."""
        for r, keys, _box, text in hits:
            src = keys[0][0].split('\t')[0] if keys else ''
            # сцена — лише файл діалогів (а не довідник предметів через випадковий збіг)
            if r >= SCENE_RATIO and DIALOG.search(src) and len(skeleton(text)) >= 15:
                if src in self.near:
                    self.near.remove(src)
                self.near.insert(0, src)
        del self.near[2:]

    @staticmethod
    def _small(img):
        import numpy as np
        return np.asarray(img.convert('L').resize((64, 36)), dtype=np.int16)

    @staticmethod
    def _diff(a, b):
        return float(abs(a - b).mean())

    def _settle(self, hwnd, img, small):
        """Гра «друкує» репліку по букві: чекаємо, поки кадр перестане змінюватись
        (не довше STABLE_TRIES × STABLE_GAP) — щоб не розпізнавати кожен проміжний."""
        for _ in range(STABLE_TRIES):
            if self.stop_ev.wait(STABLE_GAP):
                break
            img2 = grab(hwnd)
            if img2 is None:
                break
            s2 = self._small(img2)
            still = self._diff(s2, small) <= SAME
            img, small = img2, s2
            if still:
                break
        return img, small

    def run(self):
        hwnd, seen = None, 0.0
        try:
            while not self.stop_ev.is_set():
                t0 = time.time()
                try:
                    if hwnd is None or not _u32.IsWindow(hwnd) or t0 - seen > 5:
                        hwnd, seen = find_window(self.exe), t0
                    img = grab(hwnd) if hwnd else None
                    small = self._small(img) if img is not None else None
                    if img is None:
                        self.last = None
                        self.on_frame('nogame', None, [])
                    elif self.last is not None and self._diff(small, self.last) <= SAME:
                        self.on_frame('same', None, None)
                    else:
                        img, small = self._settle(hwnd, img, small)
                        self.last = small
                        lines = self.ocr.lines(img)
                        if self.save_dir:
                            self._save(img, lines)
                        hits = match(self.index, lines, near=self.near)
                        self._remember_scene(hits)
                        self.on_frame('ok', img, hits)
                except Exception as ex:                  # noqa: BLE001
                    self.on_frame(f'err:{ex}', None, [])
                self.stop_ev.wait(max(0.2, self.interval - (time.time() - t0)))
        finally:
            self.ocr.close()

    def stop(self):
        self.stop_ev.set()
