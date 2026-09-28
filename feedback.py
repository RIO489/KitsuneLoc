# -*- coding: utf-8 -*-
"""Звернення перекладача (помилки, правки, побажання) — у групу Telegram.

Кожне звернення — окрема тема форуму групи: опис, скриншоти, технічне (версія,
лог, рядок). Надсилає бот; ключ (`звернення.key`: токен бота + id групи) не в
git — власник передає файл перекладачеві сам.

Спершу звернення лягає в `звернення/черга/<час>/` (report.json + картинки),
потім надсилається; немає інтернету — лишається в черзі й піде пізніше.
Надіслане переїжджає в `звернення/надіслано/`. Уже зроблені кроки (тема, текст,
фото, файл) записуються в report.json — повтор після збою не дублює.
Без сторонніх бібліотек (urllib); картинки — Pillow.
"""
import html, io, json, os, platform, re, shutil, time, uuid
import urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KEYFILE = os.path.join(HERE, 'звернення.key')
DIR = os.path.join(HERE, 'звернення')
QUEUE = os.path.join(DIR, 'черга')
SENT = os.path.join(DIR, 'надіслано')
DRAFT = os.path.join(DIR, 'чернетка.json')
LOGFILE = os.path.join(HERE, 'лог.txt')

# тип звернення -> (підпис, колір значка теми; Telegram дозволяє лише ці шість)
KINDS = {
    'текст': ('Не перекладено', 16766590),      # жовтий
    'вигляд': ('Криво виглядає в грі', 7322096),  # блакитний
    'програма': ('Помилка програми', 16478047),   # червоний
    'побажання': ('Побажання', 9367192),          # зелений
}
GAME_TAGS = {'msk': 'MSK', 'nep': 'Nep', 'crystar': 'Crystar', '': 'Програма'}
# Steam appid — запасні, якщо appmanifest не знайдеться
APPIDS = {'msk': '837610', 'nep': '282900'}

MSG_MAX = 4000          # Telegram: 4096 символів на повідомлення
TOPIC_MAX = 120         # Telegram: 128 символів на назву теми
PHOTO_SIDE = 2560       # довша сторона фото; більше Telegram однаково стисне


class TgError(Exception):
    """Telegram відповів помилкою (не мережа)."""


class Offline(Exception):
    """Немає зв'язку з Telegram — спробувати пізніше."""


# ------------------------------------------------------------------ ключ
def _read_key(path):
    """JSON ключа; зайве навколо {…} (пробіли, крапка після копіювання) — не заважає."""
    t = open(path, encoding='utf-8-sig').read()
    a, b = t.find('{'), t.rfind('}')
    return json.loads(t[a:b + 1] if a >= 0 and b > a else t)


def load_key():
    """{'token', 'chat', 'назва'} або None."""
    try:
        k = _read_key(KEYFILE)
    except (OSError, ValueError):
        return None
    return k if k.get('token') and k.get('chat') else None


def save_key(token, chat, title=''):
    with open(KEYFILE, 'w', encoding='utf-8') as f:
        json.dump({'token': token, 'chat': chat, 'назва': title}, f, ensure_ascii=False, indent=1)


def import_key(path):
    """Узяти файл ключа, який надіслав власник (перевірка + копія в теку програми)."""
    try:
        k = _read_key(path)
    except (OSError, ValueError) as ex:
        raise ValueError(f'Це не файл ключа: {ex}')
    if not k.get('token') or not k.get('chat'):
        raise ValueError('У файлі немає токена чи групи.')
    if os.path.abspath(path) != os.path.abspath(KEYFILE):
        save_key(k['token'], k['chat'], k.get('назва', ''))
    return k


def find_key_file():
    """Файл ключа, який перекладач уже завантажив (Завантаження, Telegram Desktop)."""
    down = os.path.join(os.path.expanduser('~'), 'Downloads')
    for d in (down, os.path.join(down, 'Telegram Desktop')):
        p = os.path.join(d, 'звернення.key')
        if os.path.isfile(p):
            return p
    return None


# --------------------------------------------------------------- Telegram
def _multipart(fields, files):
    b = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        out.write(str(v).encode('utf-8') + b'\r\n')
    for k, (name, data, ctype) in files.items():
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n'
                  f'Content-Type: {ctype}\r\n\r\n'.encode('utf-8'))
        out.write(data + b'\r\n')
    out.write(f'--{b}--\r\n'.encode())
    return out.getvalue(), f'multipart/form-data; boundary={b}'


def api(token, method, fields=None, files=None, timeout=60):
    """Виклик Bot API; повертає result. Мережа — Offline, відмова — TgError."""
    fields = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
              for k, v in (fields or {}).items() if v is not None}
    if files:
        data, ctype = _multipart(fields, files)
    else:                    # порожній multipart Telegram не приймає (400 без JSON)
        data = urllib.parse.urlencode(fields).encode('utf-8')
        ctype = 'application/x-www-form-urlencoded'
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/{method}', data=data,
                                 headers={'Content-Type': ctype, 'User-Agent': 'KitsuneLoc'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ans = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as ex:
        try:
            ans = json.loads(ex.read().decode('utf-8'))
        except Exception:                                   # noqa: BLE001
            raise TgError(f'HTTP {ex.code}')
        if ex.code == 429 or ex.code >= 500:
            raise Offline(ans.get('description') or f'HTTP {ex.code}')
    except (urllib.error.URLError, OSError, TimeoutError) as ex:
        raise Offline(str(getattr(ex, 'reason', ex)))
    if not ans.get('ok'):
        raise TgError(ans.get('description', 'невідома помилка'))
    return ans['result']


def find_chats(token):
    """Групи, куди додано бота: [(id, назва, чи форум, чи може керувати темами)].
    Бачить лише групи з повідомленнями за останню добу — тому «напиши в групу»."""
    me = api(token, 'getMe', timeout=20)
    ids = {}
    for u in api(token, 'getUpdates', timeout=20):
        for key in ('message', 'edited_message', 'my_chat_member', 'chat_member', 'channel_post'):
            m = u.get(key)
            if not m or 'chat' not in m:
                continue
            c = m['chat']
            if c.get('type') in ('group', 'supergroup'):
                ids[m.get('migrate_to_chat_id') or c['id']] = c.get('title', '')
    out = []
    for cid, title in ids.items():
        try:
            c = api(token, 'getChat', {'chat_id': cid}, timeout=20)
        except TgError:
            continue                  # стара група, що стала супергрупою, тощо
        try:
            mem = api(token, 'getChatMember', {'chat_id': cid, 'user_id': me['id']}, timeout=20)
            topics = mem.get('status') == 'creator' or bool(mem.get('can_manage_topics'))
        except TgError:
            topics = False
        out.append((c['id'], c.get('title', title), bool(c.get('is_forum')), topics))
    return me.get('username', ''), out


def topic_link(chat, thread):
    s = str(chat)
    if not thread or not s.startswith('-100'):
        return None
    return f'https://t.me/c/{s[4:]}/{thread}'


# --------------------------------------------------------------- скриншоти
def _appid(libs, installdir, game):
    for lib in libs:
        d = os.path.join(lib, 'steamapps')
        try:
            names = [n for n in os.listdir(d) if n.startswith('appmanifest_')]
        except OSError:
            continue
        for n in names:
            try:
                txt = open(os.path.join(d, n), encoding='utf-8', errors='replace').read()
            except OSError:
                continue
            m = re.search(r'"installdir"\s*"([^"]*)"', txt)
            if m and m.group(1).lower() == installdir.lower():
                return n[len('appmanifest_'):-len('.acf')]
    return APPIDS.get(game)


def shot_dirs(libs, installdir='', game=''):
    """Теки зі знімками екрана: Steam цієї гри, Win+PrtSc, Xbox Game Bar."""
    dirs = []
    appid = _appid(libs, installdir, game) if installdir else None
    if appid:
        for lib in libs:
            ud = os.path.join(lib, 'userdata')
            try:
                users = os.listdir(ud)
            except OSError:
                continue
            for u in users:
                dirs.append(os.path.join(ud, u, '760', 'remote', appid, 'screenshots'))
    home = os.path.expanduser('~')
    for base in (home, os.path.join(home, 'OneDrive')):
        for sub in (('Pictures', 'Screenshots'), ('Зображення', 'Знімки екрана'),
                    ('Videos', 'Captures')):
            dirs.append(os.path.join(base, *sub))
    return [d for d in dict.fromkeys(dirs) if os.path.isdir(d)]


def recent_shots(dirs, n=12, days=30):
    """Найсвіжіші знімки з тек (новіші за `days` днів), новіші — першими."""
    old = time.time() - days * 86400
    found = []
    for d in dirs:
        try:
            for e in os.scandir(d):
                if e.is_file() and e.name.lower().endswith(('.jpg', '.jpeg', '.png')):
                    t = e.stat().st_mtime
                    if t >= old:
                        found.append((t, e.path))
        except OSError:
            continue
    return [p for _t, p in sorted(found, reverse=True)[:n]]


def clipboard_images():
    """Картинки з буфера обміну (скопійований знімок чи файли) — список PIL."""
    try:
        from PIL import ImageGrab, Image
        got = ImageGrab.grabclipboard()
    except Exception:                                       # noqa: BLE001
        return []
    if got is None:
        return []
    if isinstance(got, list):
        out = []
        for p in got:
            try:
                out.append(Image.open(p))
            except Exception:                               # noqa: BLE001
                pass
        return out
    return [got]


def _photo_bytes(im):
    """Картинка для sendPhoto: не більше PHOTO_SIDE, JPEG (до 10 МБ)."""
    im = im.convert('RGB')
    if max(im.size) > PHOTO_SIDE:
        k = PHOTO_SIDE / max(im.size)
        from PIL import Image
        im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=92)
    return buf.getvalue()


# ------------------------------------------------------------------ звернення
def log_tail(n=60):
    try:
        with open(LOGFILE, encoding='utf-8', errors='replace') as f:
            return ''.join(f.readlines()[-n:])
    except OSError:
        return ''


def enqueue(report, images):
    """Покласти звернення в чергу. images — PIL-картинки чи шляхи до файлів."""
    from PIL import Image
    name = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:4]
    d = os.path.join(QUEUE, name)
    os.makedirs(d)
    shots = []
    for i, im in enumerate(images):
        if not isinstance(im, Image.Image):
            im = Image.open(im)
        p = f'знімок{i + 1}.jpg'
        with open(os.path.join(d, p), 'wb') as f:
            f.write(_photo_bytes(im))
        shots.append(p)
    report = dict(report, знімки=shots, створено=time.strftime('%Y-%m-%d %H:%M:%S'),
                  зроблено=[])
    _save(d, report)
    return d


def _save(d, report):
    tmp = os.path.join(d, 'report.json.new')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(d, 'report.json'))


def pending():
    try:
        return sorted(os.path.join(QUEUE, n) for n in os.listdir(QUEUE)
                      if os.path.isfile(os.path.join(QUEUE, n, 'report.json')))
    except OSError:
        return []


def _one_line(s, n):
    s = ' '.join((s or '').split())
    return s if len(s) <= n else s[:n - 1].rstrip() + '…'


def topic_title(r):
    kind = KINDS.get(r.get('тип'), ('Звернення',))[0]
    head = f'[{GAME_TAGS.get(r.get("гра", ""), r.get("гра", ""))}] {kind}: '
    return _one_line(head + (r.get('що') or r.get('де') or ''), TOPIC_MAX)


def message_html(r):
    e = html.escape
    kind = KINDS.get(r.get('тип'), ('Звернення',))[0]
    parts = [f'<b>{e(kind)}</b> · {e(r.get("назва гри") or "програма загалом")}']
    for label, key in (('Що не так', 'що'), ('Як мало бути', 'як'), ('Де в грі', 'де')):
        if (r.get(key) or '').strip():
            parts.append(f'<b>{label}:</b>\n{e(r[key].strip())}')
    row = r.get('рядок')
    if row:
        lines = [f'<code>{e(row.get("source", ""))} [{e(str(row.get("id", "")))}]</code>']
        for label, key in (('Оригінал', 'src'), ('Переклад', 'tr'), ('Попередження', 'warn'),
                           ('Де', 'where')):
            if row.get(key):
                lines.append(f'{label}: {e(_one_line(row[key], 600))}')
        parts.append('<b>Рядок:</b>\n' + '\n'.join(lines))
    tech = r.get('технічне') or {}
    if tech:
        parts.append(f'<i>KitsuneLoc {e(tech.get("версія", "?"))} · {e(tech.get("система", ""))} · '
                     f'{e(r.get("створено", ""))}</i>')
    text = '\n\n'.join(parts)
    if len(text) > MSG_MAX:            # повний текст однаково йде файлом
        text = '\n\n'.join(parts[:1] + [f'<b>Що не так:</b>\n{e(_one_line(r.get("що"), 3000))}',
                                        '<i>(повний текст — у файлі нижче)</i>'])
    return text


def details_text(r):
    """Усе звернення текстом — файлом до теми (довге, лог, рядок повністю)."""
    out = [topic_title(r), '']
    for label, key in (('Що не так', 'що'), ('Як мало бути', 'як'), ('Де в грі', 'де')):
        if (r.get(key) or '').strip():
            out += [f'{label}:', r[key].strip(), '']
    if r.get('рядок'):
        out += ['Рядок:', json.dumps(r['рядок'], ensure_ascii=False, indent=1), '']
    tech = r.get('технічне') or {}
    for k, v in tech.items():
        if k != 'лог':
            out.append(f'{k}: {v}')
    if tech.get('лог'):
        out += ['', '---- кінець лог.txt ----', tech['лог']]
    return '\n'.join(out)


def technical(version, game_title=''):
    return {'версія': version, 'гра': game_title,
            'система': f'Windows {platform.version()}' if os.name == 'nt' else platform.platform(),
            'python': platform.python_version(), 'лог': log_tail()}


def send(d, key):
    """Надіслати одне звернення з черги; повертає посилання на тему (чи None)."""
    r = json.load(open(os.path.join(d, 'report.json'), encoding='utf-8'))
    done = r.setdefault('зроблено', [])
    tok, chat = key['token'], key['chat']
    thread = r.get('тема')

    if 'тема' not in done:
        try:
            t = api(tok, 'createForumTopic', {'chat_id': chat, 'name': topic_title(r),
                                              'icon_color': KINDS.get(r.get('тип'), ('', 7322096))[1]})
            thread = t['message_thread_id']
        except TgError as ex:
            if 'not a forum' not in str(ex).lower():
                raise
            thread = None                # теми вимкнено — усе просто в загальний чат
        r['тема'] = thread
        done.append('тема')
        _save(d, r)
    where = {'chat_id': chat, 'message_thread_id': thread}

    if 'текст' not in done:
        api(tok, 'sendMessage', dict(where, text=message_html(r), parse_mode='HTML',
                                     link_preview_options={'is_disabled': True}))
        done.append('текст')
        _save(d, r)

    shots = [p for p in r.get('знімки', []) if os.path.exists(os.path.join(d, p))]
    if shots and 'фото' not in done:
        for i in range(0, len(shots), 10):          # альбом — до 10 фото
            chunk = shots[i:i + 10]
            files = {f'p{j}': (p, open(os.path.join(d, p), 'rb').read(), 'image/jpeg')
                     for j, p in enumerate(chunk)}
            if len(chunk) == 1:
                api(tok, 'sendPhoto', dict(where, photo='attach://p0'), files, timeout=180)
            else:
                api(tok, 'sendMediaGroup', dict(where, media=[
                    {'type': 'photo', 'media': f'attach://p{j}'} for j in range(len(chunk))]),
                    files, timeout=300)
        done.append('фото')
        _save(d, r)

    if 'файл' not in done:
        body = details_text(r).encode('utf-8')
        api(tok, 'sendDocument', dict(where), {'document': ('технічне.txt', body, 'text/plain')},
            timeout=120)
        done.append('файл')
        _save(d, r)

    os.makedirs(SENT, exist_ok=True)
    shutil.move(d, os.path.join(SENT, os.path.basename(d)))
    return topic_link(chat, thread)


def flush(say=None):
    """Надіслати всю чергу. Повертає (надіслано, лишилось, [посилання])."""
    key = load_key()
    items = pending()
    if not key or not items:
        return 0, len(items), []
    sent, links, errors = 0, [], set()
    for d in items:
        try:
            links.append(send(d, key))
            sent += 1
        except Offline as ex:
            if say:
                say(f'Звернення не надіслано (немає зв\'язку з Telegram: {ex}) — спробую пізніше.', 'warn')
            break
        except TgError as ex:            # решту черги однаково пробуємо: може, заважає лише це
            if say and str(ex) not in errors:
                say(f'Telegram не прийняв звернення: {ex}', 'err')
            errors.add(str(ex))
    return sent, len(pending()), links


# --------------------------------------------------------------- чернетка
def load_draft():
    try:
        return json.load(open(DRAFT, encoding='utf-8'))
    except (OSError, ValueError):
        return None


def save_draft(d):
    os.makedirs(DIR, exist_ok=True)
    with open(DRAFT, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def drop_draft():
    try:
        os.remove(DRAFT)
    except OSError:
        pass
