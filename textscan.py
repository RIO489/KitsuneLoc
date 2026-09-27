# -*- coding: utf-8 -*-
"""Пошук написів на картинках гри (вікно «Знайти написи», textscan_window.py).

Програма не знає сама, де на текстурах текст: кожен напис хтось має розмітити
(атлас/написи.json, нептун.json). Тут — допомога перекладачу:

  * перелік текстур, де можуть бути написи інтерфейсу (без моделей, облич, фонів…),
    і завантаження картинки з кадрами нарізки — однаково для MSK і Neptunia;
  * розпізнавання тексту вбудованим у Windows OCR (textscan_ocr.ps1): знайдені
    англійські слова — кандидати, які перекладач переглядає, поправляє рамку,
    приймає чи відкидає;
  * перевірка, чи є в Windows англійське розпізнавання, і встановлення (UAC).

Знайдене зберігається в кеш\\пошук_написів_<гра>.json (можна видалити будь-коли),
рішення перекладача — у розмітці «мої» (maryskelter/atlas.load_marks).
"""
import json, os, re, subprocess, tempfile

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PS1 = os.path.join(HERE, 'textscan_ocr.ps1')
CACHE = os.path.join(HERE, 'кеш')
MARKS = {'msk': 'написи.json', 'nep': 'нептун.json'}
OCR_CAP = 'Language.OCR~~~en-US~0.0.1.0'
_NOWIN = 0x08000000                             # CREATE_NO_WINDOW: без чорного вікна консолі

# не інтерфейс: моделі, підземелля, обличчя/фони/CG сцен, арти, ефекти (MSK)
MSK_SKIP = re.compile(r'(EnemyModel|dunmdl|^PC\\|mapData|\\FACE\\|\\STILL\\|\\BG\\|BigChara|bonus\\Character|'
                      r'bonus\\Novel|Effect\\|\.ism2|MOVIE|BigMA|SmallMA|BigCollision|SmallCollision|Hole|'
                      r'EVENT\\SCRIPT|EVENT\\EFFECT|CARD|PET\\|TEXTURE\\item\\|world_bg|obj_sh|Logo\\)', re.I)
MSK_ARCS = ('TTM1.bra', 'TTM3.bra', 'Game.bra', 'DLC.bra', 'System.bra', 'Scratch.bra', 'MA.bra')
# Neptunia: моделі, предмети, обличчя, фони й персонажі подій, галерея
NEP_SKIP = re.compile(r'(^model/|picture/item|picture/chara|symbol/texture|gallery/|global/face|'
                      r'event/chara|event/bg|event/mp|event/ma)', re.I)


# ------------------------------------------------------------------ текстури
class Textures:
    """Текстури гри з чистих оригіналів (backup, інакше тека гри)."""

    def __init__(self, game, backup_dir, game_dir):
        self.game, self.dirs = game, [d for d in (backup_dir, game_dir) if d]
        self.game_dir = game_dir
        self._pacs = {}

    def _path(self, arc):
        return next((os.path.join(d, *arc.split('/')) for d in self.dirs
                     if os.path.exists(os.path.join(d, *arc.split('/')))), None)

    def list(self, everything=False):
        """[джерело]: 'TTM3.bra/TEXTURE/…/x.CL3' | 'data/GAME00000.pac/…/x.tid'.
        everything — і те, де тексту для розпізнавання немає (арти, новели, портрети, моделі):
        для своїх картинок перекладача (pics.py)."""
        out = []
        if self.game == 'msk':
            from maryskelter.bra import Bra
            for arc in MSK_ARCS:
                p = self._path(arc)
                if not p:
                    continue
                for e in Bra(p).entries:
                    if e.name.lower().endswith(('.cl3', '.dds')) and (everything or not MSK_SKIP.search(e.name)):
                        out.append(arc + '/' + e.name.replace('\\', '/'))
        else:
            import translate_nep as tn
            for arc in tn.archives(self.game_dir):
                for e in self._pac(arc).entries:
                    n = e.name.replace('\\', '/')
                    if n.lower().endswith('.tid') and (everything or not NEP_SKIP.search(n)):
                        out.append(arc + '/' + n)
        return out

    def _pac(self, arc):
        from neptunia.pac import Pac
        if arc not in self._pacs:
            self._pacs[arc] = Pac(self._path(arc))
        return self._pacs[arc]

    def frames_of(self, srcs):
        """{джерело: {текстура: кадри нарізки}} без розкодування текстур (для лічильників)."""
        out = {}
        if self.game != 'msk':
            return {s: {'': {}} for s in srcs}
        from maryskelter.bra import Bra
        from maryskelter import atlas as atl
        by_arc = {}
        for s in srcs:
            arc, _, name = s.partition('/')
            by_arc.setdefault(arc, []).append(name.replace('/', '\\'))
        for arc, names in by_arc.items():
            for name, blob in Bra.read_some(self._path(arc), names).items():
                src = arc + '/' + name.replace('\\', '/')
                try:
                    c = atl.container(blob)
                    tids = [f for f in c.files if f[0].lower().endswith('.tid')]
                    out[src] = {os.path.splitext(f[0])[0]:
                                atl.frames(c, os.path.splitext(f[0])[0] if len(tids) > 1 else None)
                                for f in tids}
                except Exception:                                     # noqa: BLE001
                    out[src] = {}
        return out

    def load(self, src):
        """[(текстура, RGBA-картинка, {кадр: (x0, y0, x1, y1)})] — у CL3 пар буває кілька."""
        if self.game == 'msk':
            from maryskelter.bra import Bra
            from maryskelter import atlas as atl, dds
            arc, _, name = src.partition('/')
            name = name.replace('/', '\\')
            c = atl.container(Bra.read_some(self._path(arc), [name])[name])
            tids = [f for f in c.files if f[0].lower().endswith('.tid')]
            out = []
            for f in tids:
                stem = os.path.splitext(f[0])[0]
                img = dds.decode(bytes(f[1])).convert('RGBA')
                out.append((stem, img, atl.frames(c, stem if len(tids) > 1 else None)))
            return out
        from neptunia import atlas as natl
        from neptunia.tid import Tid
        arc, inner = natl.split_src(src)
        return [('', Tid(self._pac(arc).read(inner)).image().convert('RGBA'), {})]


# ------------------------------------------------------------------ OCR
def _ps(*args, timeout=None):
    return subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', PS1, *args],
                          capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=timeout, creationflags=_NOWIN)


def ocr_status():
    """'ok' — англійське розпізнавання є; 'nolang' — треба доставити мовний компонент;
    'noapi' — Windows без OCR (старіша за 10); 'error' — не вдалося запустити."""
    try:
        r = _ps('-check', timeout=60)
    except Exception:                                                 # noqa: BLE001
        return 'error'
    out = (r.stdout or '').strip().splitlines()
    return out[-1] if out and out[-1] in ('ok', 'nolang', 'noapi') else 'error'


def install_ocr():
    """Доставити англійське розпізнавання (компонент Windows; потрібні права адміністратора —
    з'явиться запит UAC). Повертає новий ocr_status()."""
    cmd = f"Add-WindowsCapability -Online -Name '{OCR_CAP}'"
    subprocess.run(['powershell', '-NoProfile', '-Command',
                    f"Start-Process powershell -Verb RunAs -Wait -ArgumentList "
                    f"'-NoProfile','-Command',\"{cmd}\""],
                   capture_output=True, text=True, creationflags=_NOWIN)
    return ocr_status()


_WORD = re.compile(r"^[A-Za-z][A-Za-z'\-!?.:/&%]*$")


def plausible(t):
    """Схоже на справжнє англійське слово, а не на візерунок малюнка."""
    t = t.strip('.,:;!?()[]"\'')
    letters = re.sub(r'[^A-Za-z]', '', t)
    if len(letters) < 2 or not _WORD.match(t):
        return False
    if t.isupper() and len(letters) <= 5:
        return True
    if not re.search(r'[aeiouyAEIOUY]', letters):
        return False
    return not (re.search(r'[a-z][A-Z]', letters) and not letters.isupper())


BATCH = 6                       # текстур за один запуск OCR: великі атласи ×2 — до ~30 МБ PNG кожен


def scan(textures, srcs, progress=None, stop=None):
    """Розпізнати текст на текстурах; повертає {джерело: {текстура: [{текст, рамка}]}}.
    Рамка — у пікселях текстури. Картинки для OCR — на темному тлі й ×2 (дрібний
    текст так розпізнається краще); пачками по BATCH, тимчасові файли одразу прибираємо."""
    res = {}
    for b0 in range(0, len(srcs), BATCH):
        if stop and stop():
            return {}
        part = _scan_batch(textures, srcs[b0:b0 + BATCH], stop,
                           lambda k, t: progress and progress(b0 + k, len(srcs), t))
        if part is None:
            return {}
        res.update(part)
    return res


def _scan_batch(textures, srcs, stop, step):
    tmp = tempfile.mkdtemp(prefix='kitsune_ocr_')
    pngs, where = [], {}
    try:
        for k, src in enumerate(srcs):
            step(k + 0.5, 'Готую картинки…')
            try:
                parts = textures.load(src)
            except Exception:                                         # noqa: BLE001
                continue
            for stem, img, _fr in parts:
                bg = Image.new('RGBA', img.size, (24, 16, 32, 255))
                bg.alpha_composite(img)
                p = os.path.join(tmp, f'{len(pngs):05d}.png')
                bg.convert('RGB').resize((img.width * 2, img.height * 2), Image.LANCZOS).save(p)
                pngs.append(p)
                where[p] = (src, stem)
        if not pngs:
            return {}
        lst, out = os.path.join(tmp, 'list.txt'), os.path.join(tmp, 'ocr.jsonl')
        with open(lst, 'w', encoding='utf-8') as f:
            f.write('\n'.join(pngs))
        proc = subprocess.Popen(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', PS1,
                                 '-list', lst, '-out', out], stdout=subprocess.PIPE, text=True,
                                encoding='utf-8', errors='replace', creationflags=_NOWIN)
        done = 0
        for line in proc.stdout:
            if line.startswith('done '):
                done += 1
                step(done * len(srcs) / len(pngs), 'Розпізнаю текст…')
            if stop and stop():
                proc.kill()
                return None
        proc.wait()
        res = {}
        if not os.path.exists(out):
            return res
        for line in open(out, encoding='utf-8-sig'):
            d = json.loads(line)
            src, stem = where.get(d.get('file'), (None, None))
            if src is None:
                continue
            found = []
            for ln in d.get('lines') or []:
                ws = [w for w in (ln.get('words') or []) if plausible(w['text'])]
                if not ws:
                    continue
                box = [min(w['x'] for w in ws) // 2, min(w['y'] for w in ws) // 2,
                       (max(w['x'] + w['w'] for w in ws) + 1) // 2, (max(w['y'] + w['h'] for w in ws) + 1) // 2]
                found.append({'текст': ' '.join(w['text'] for w in ws), 'рамка': box})
            res.setdefault(src, {})[stem] = found
        return res
    finally:
        for p in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, p))
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


# ------------------------------------------------------------------ знайдене
def cache_path(game):
    return os.path.join(CACHE, f'пошук_написів_{game}.json')


def load_found(game):
    try:
        with open(cache_path(game), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_found(game, data):
    os.makedirs(CACHE, exist_ok=True)
    p = cache_path(game)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(p + '.tmp', p)


def overlap(a, b):
    """Частка площі меншої з рамок, що перекривається."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    s = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (x1 - x0) * (y1 - y0) / max(1, s)
