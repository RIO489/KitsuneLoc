# -*- coding: utf-8 -*-
"""Бібліотека шрифтів для написів на картинках.

Шрифти стилів лежать у атлас/шрифти/, бібліотека для порівняння — у
атлас/шрифти/кандидати/ (Google Fonts з і ї є ґ, див. завантажити.py).
Вікно «Глянути різні шрифти» малює той самий напис кожним із них; коли
шрифт кандидата записують у розмітку, він копіюється в атлас/шрифти/.

Свої шрифти (знайдені в інтернеті) — у атлас/шрифти/мої/ (не в git: це
бібліотека перекладача, оновлення її не чіпають). Додаються кнопками вікна
«Глянути різні шрифти»: з файлу (.ttf, .otf або .zip з ними) чи з інтернету
(пряме посилання на файл/архів, сторінка fonts.google.com або назва шрифту
Google Fonts). Шрифт без і ї є ґ не додається — на ньому напис не намалювати.
"""
import io, json, os, re, shutil, urllib.parse, urllib.request, zipfile

from PIL import ImageFont

from . import atlas as atl

CAND_DIR = os.path.join(atl.FONTS, 'кандидати')
MY_DIR = os.path.join(atl.FONTS, 'мої')
FONT_EXT = ('.ttf', '.otf')
NEED = 'іїєґІЇЄҐабвгджзклмнпрстуфхцчшщьюяЖЩ'
GOOGLE_API = 'https://api.github.com/repos/google/fonts/contents/{}/{}'


def font_files():
    """[(тека, файл)]: спершу шрифти стилів, свої, далі кандидати (без повторів)."""
    out, seen = [], set()
    for d in (atl.FONTS, MY_DIR, CAND_DIR):
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d), key=str.lower):
            if f.lower().endswith(('.ttf', '.otf')) and f not in seen:
                seen.add(f)
                out.append((d, f))
    return out


def axes(path):
    """{'Weight': {...}, 'Width': {...}} — осі варіативного шрифту ({} для статичного)."""
    try:
        f = ImageFont.truetype(path, 20)
        return {a['name'].decode() if isinstance(a['name'], bytes) else a['name']: a
                for a in f.get_variation_axes()}
    except Exception:
        return {}


def variation(path, weight):
    """Варіація для товщини `weight` (у межах осі шрифту) або None для статичного."""
    ax = axes(path)
    if 'Weight' not in ax:
        return None
    a = ax['Weight']
    v = {'wght': int(min(a['maximum'], max(a['minimum'], weight)))}
    if 'Width' in ax:
        v['wdth'] = ax['Width']['default']
    return v


def register(d, fn):
    """Щоб atlas._font знайшов шрифт кандидата, не копіюючи файл."""
    if d != atl.FONTS:
        atl.EXTRA_FONT_DIRS[fn] = d


def adopt(fn):
    """Шрифт, записаний у розмітку, — у атлас/шрифти/ (щоб поїхав до перекладача)."""
    dst = os.path.join(atl.FONTS, fn)
    for d in (MY_DIR, CAND_DIR):
        src = os.path.join(d, fn)
        if not os.path.exists(dst) and os.path.exists(src):
            shutil.copy2(src, dst)


# ------------------------------------------------------------ свої шрифти
def missing_letters(data):
    """Літери з NEED, яких у шрифті немає (порожньо або квадрат .notdef)."""
    f = ImageFont.truetype(io.BytesIO(data), 40)
    notdef = bytes(f.getmask('￿'))
    out = []
    for ch in NEED:
        m = f.getmask(ch)
        if not m.getbbox() or bytes(m) == notdef:
            out.append(ch)
    return out


def _is_font(data):
    return data[:4] in (b'\x00\x01\x00\x00', b'OTTO', b'true')


def _safe_name(name):
    name = os.path.basename(name.replace('\\', '/'))
    return re.sub(r'[<>:"/\\|?*]', '_', name).strip() or 'шрифт.ttf'


def _add_font(name, data, report):
    """Покласти один файл шрифту в MY_DIR (якщо в ньому є українські літери)."""
    name = _safe_name(name)
    if not name.lower().endswith(FONT_EXT):
        name += '.otf' if data[:4] == b'OTTO' else '.ttf'
    try:
        miss = missing_letters(data)
    except Exception as ex:                                           # noqa: BLE001
        report.append(f'✗ {name}: не читається як шрифт ({ex})')
        return
    if any(c in miss for c in 'іїєґабвгд'):
        report.append(f'✗ {name}: немає українських літер ({" ".join(miss[:12])})')
        return
    for d in (atl.FONTS, CAND_DIR):
        if os.path.exists(os.path.join(d, name)):
            report.append(f'= {name}: такий шрифт уже є в бібліотеці')
            return
    os.makedirs(MY_DIR, exist_ok=True)
    with open(os.path.join(MY_DIR, name), 'wb') as f:
        f.write(data)
    report.append(f'✓ {name}' + (f' (без {" ".join(miss)})' if miss else ''))


def _add_blob(name, data, report):
    """Шрифт або .zip зі шрифтами (з підтеками)."""
    if data[:2] == b'PK':
        z = zipfile.ZipFile(io.BytesIO(data))
        fonts = [i for i in z.infolist() if i.filename.lower().endswith(FONT_EXT)
                 and not os.path.basename(i.filename).startswith('._')]
        # у архівах Google Fonts є і варіативні, і static/ — вистачить варіативних
        var = [i for i in fonts if '[' in i.filename and '/static/' not in '/' + i.filename]
        for i in (var or fonts):
            _add_font(i.filename, z.read(i), report)
        if not fonts:
            report.append(f'✗ {name}: в архіві немає .ttf чи .otf')
    elif _is_font(data):
        _add_font(name, data, report)
    else:
        report.append(f'✗ {name}: це не шрифт і не .zip (можливо, посилання веде на сторінку, '
                      'а не на файл)')


def add_files(paths):
    """Додати шрифти з файлів на диску. Повертає звіт (список рядків)."""
    report = []
    for p in paths:
        with open(p, 'rb') as f:
            _add_blob(os.path.basename(p), f.read(), report)
    return report


def _get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'KitsuneLoc'})
    with urllib.request.urlopen(req, timeout=60) as r:
        name = ''
        cd = r.headers.get('Content-Disposition') or ''
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
        if m:
            name = urllib.parse.unquote(m.group(1))
        return r.read(), name or urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(r.url).path))


def _google(family, report):
    """Сімейство Google Fonts (напр. «Rubik Mono One») з github.com/google/fonts."""
    folder = re.sub(r'[^a-z0-9]', '', family.lower())
    for lic in ('ofl', 'apache', 'ufl') if folder else ():
        try:
            listing, _n = _get(GOOGLE_API.format(lic, folder))
        except Exception:                                             # noqa: BLE001
            continue
        files = [x for x in json.loads(listing) if x.get('type') == 'file'
                 and x['name'].lower().endswith(FONT_EXT)]
        if not files:
            continue
        # варіативні файли (з [осями]) покривають усі товщини; інакше — усі статичні
        for x in [x for x in files if '[' in x['name']] or files:
            data, _n = _get(x['download_url'])
            _add_font(x['name'], data, report)
        return True
    report.append(f'✗ «{family}»: такого шрифту в Google Fonts не знайдено')
    return False


def download(text):
    """Додати шрифт з інтернету: пряме посилання на .ttf/.otf/.zip, сторінка
    fonts.google.com/specimen/… або просто назва шрифту Google Fonts."""
    text = text.strip()
    report = []
    if not re.match(r'^https?://', text, re.I):
        _google(text, report)
        return report
    u = urllib.parse.urlparse(text)
    if u.netloc.endswith('fonts.google.com'):
        m = re.search(r'/specimen/([^/?#]+)', u.path)
        if m:
            _google(urllib.parse.unquote_plus(m.group(1)), report)
            return report
    data, name = _get(text)
    _add_blob(name or 'шрифт', data, report)
    return report


def remove(fn):
    """Прибрати свій шрифт з бібліотеки (лише з MY_DIR)."""
    p = os.path.join(MY_DIR, fn)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False
