# -*- coding: utf-8 -*-
"""Сервіси машинного перекладу. Спільне:

    eng.translate(items, scene) -> {id: текст із заглушками}
        items — [{'id', 'text' (із заглушками), 'src', 'ja', 'who', 'note', 'lines',
                  'chars', 'words' {N: слово}, 'retry' (попередній варіант), 'problem'}]
        scene — [{'who', 'src', 'tr'}] рядки сцени перед пакетом (лише для контексту)
    eng.style        — заглушки 'br' (⟦N⟧) чи 'xml' (<x i="N"/>), див. codes.py
    eng.batch_rows / batch_chars — скільки рядків в одному запиті
    eng.spent()      — скільки витрачено (рядок для журналу)
Помилка — MTError: fatal (ключ, ліміт рахунку) зупиняє все, інакше пропускається пакет;
split — відповідь не влізла, пакет треба поділити.
"""
import json, time, urllib.error, urllib.parse, urllib.request

from mt import keys


class MTError(Exception):
    def __init__(self, msg, fatal=False, split=False):
        super().__init__(msg)
        self.fatal, self.split = fatal, split


# ============================================================ бібліотека anthropic (лише для Claude)
# Не обов'язкова (з 3.1): потрібна лише тим, хто перекладає через Claude, — ставиться кнопкою
# «Встановити» у вікні машинного перекладу, а не «Встановити.bat».
def anthropic_state():
    """'ok' | 'missing' | 'old' (0.x не знає fallbacks / output_config) — без імпорту бібліотеки."""
    import importlib.metadata, importlib.util
    if importlib.util.find_spec('anthropic') is None:
        return 'missing'
    try:
        major = int(importlib.metadata.version('anthropic').split('.')[0])
    except Exception:                                       # noqa: BLE001
        return 'ok'
    return 'old' if major < 1 else 'ok'


def install_anthropic():
    """pip install --upgrade anthropic (кличуть у фоні, ~1 хв). -> (вдалося, повідомлення)."""
    import importlib, subprocess, sys
    try:
        r = subprocess.run([sys.executable, '-m', 'pip', 'install', '--upgrade', 'anthropic'],
                           capture_output=True, text=True, timeout=600,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception as ex:                                 # noqa: BLE001
        return False, f'не вдалося запустити pip: {ex}'
    importlib.invalidate_caches()
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or '').strip().splitlines()[-3:]
        return False, 'pip не встановив бібліотеку: ' + ' / '.join(tail)
    old = sys.modules.get('anthropic')
    if old is not None and int(getattr(old, '__version__', '1').split('.')[0]) < 1:
        return True, 'оновлено — перезапусти програму, щоб узялась нова версія'
    return True, 'встановлено'


# ============================================================ Claude
# (id, підпис, $ за 1 млн токенів: вхід, вихід) — ціни Anthropic API на 2026-09
CLAUDE_MODELS = (('claude-opus-5-5', 'Opus 5.5 — найкраща якість', 4.0, 20.0),
                 ('claude-sonnet-5-5', 'Sonnet 5.5 — удвічі дешевше', 2.0, 10.0),
                 ('claude-haiku-4-5', 'Haiku 4.5 — найдешевше', 1.0, 5.0))

SYSTEM = """Ти — досвідчений перекладач відеоігор з англійської на українську. Перекладаєш гру «{title}». \
Твій переклад — чернетка, яку потім вичитає людина-перекладач, тож перекладай природно, як для виданої \
гри, а не дослівно: жива українська мова, без русизмів і кальок.

Правила:
1. Заглушки ⟦0⟧, ⟦1⟧… — службові коди гри (кольори, змінні, кнопки, значення). Кожну заглушку з оригіналу \
постав у переклад рівно один раз і не змінюй її; переставляти можна. Нових заглушок не вигадуй.
2. Якщо до рядка дано "words" (номер заглушки → слово) — це слово, яке гра підставить замість коду. \
Можеш лишити заглушку або замість неї написати слово українською в потрібному відмінку.
3. Переноси рядків (\\n): не більше "max_lines" рядків. Якщо дано "max_chars" — рядок перекладу не довший \
за стільки знаків: це ширина вікна гри, довше гра обріже. Коротка репліка лишається короткою.
4. Терміни з глосарію перекладай саме так, як у глосарії (відмінюй за потреби). Імена — як у переліку імен.
5. "ja" — японський оригінал (якщо є): допоміжне джерело, коли англійська неоднозначна чи надто вільна. \
"who" — хто говорить; "context" — попередні рядки сцени (не перекладай їх): з них зрозумілі тон, рід і звертання.
6. "note" — примітка перекладача до рядка: виконуй її.
7. "retry" — твій попередній варіант не підійшов з причини в "problem": виправ саме це.
8. Відповідь — лише JSON за схемою: переклад для кожного "id".

Вказівки перекладача до цієї гри:
{style}

Глосарій (англійською → українською):
{glossary}

Імена й назви, які вже перекладено:
{names}
"""

SCHEMA = {'type': 'object',
          'properties': {'translations': {'type': 'array', 'items': {
              'type': 'object',
              'properties': {'id': {'type': 'string'}, 'text': {'type': 'string'}},
              'required': ['id', 'text'], 'additionalProperties': False}}},
          'required': ['translations'], 'additionalProperties': False}


class Claude:
    name, style = 'claude', 'br'
    batch_rows, batch_chars = 30, 5000

    def __init__(self, model, title, style_text, glossary, names):
        import anthropic                # лише коли справді перекладаємо через Claude
        if int(anthropic.__version__.split('.')[0]) < 1:
            # 0.x не знає fallbacks / output_config — оновлює «Встановити.bat»
            raise MTError(f'стара бібліотека anthropic {anthropic.__version__} — запусти «Встановити.bat»',
                          fatal=True)
        self.anthropic = anthropic
        self.model = model
        self.label = f'Claude {next((m[1].split(" — ")[0] for m in CLAUDE_MODELS if m[0] == model), model)}'
        self.price = next(((a, b) for m, _l, a, b in CLAUDE_MODELS if m == model), (4.0, 20.0))
        # без ключа SDK бере ANTHROPIC_API_KEY чи профіль `ant auth login`
        self.client = anthropic.Anthropic(api_key=keys.get('claude') or None, max_retries=4, timeout=600)
        gl = '\n'.join(f"{t['en']} → {t['ua']}" + (f" ({t['примітка']})" if t.get('примітка') else '')
                       for t in glossary if t.get('ua')) or '(порожній)'
        nm = '\n'.join(f'{en} → {ua}' for en, ua in names) or '(ще немає)'
        self.system = SYSTEM.format(title=title, style=style_text.strip() or '(немає)', glossary=gl, names=nm)
        self.usage = dict(input=0, cache_write=0, cache_read=0, output=0)

    def translate(self, items, scene):
        rows = []
        for it in items:
            d = {'id': it['id'], 'src': it['text']}
            for k_in, k_out in (('who', 'who'), ('ja', 'ja'), ('note', 'note'), ('lines', 'max_lines'),
                                ('chars', 'max_chars'), ('retry', 'retry'), ('problem', 'problem')):
                if it.get(k_in):
                    d[k_out] = it[k_in]
            if it.get('words'):
                d['words'] = {f'⟦{n}⟧': w for n, w in it['words'].items()}
            rows.append(d)
        user = json.dumps({'context': scene, 'rows': rows}, ensure_ascii=False)
        kw = dict(model=self.model, max_tokens=16000,
                  system=[{'type': 'text', 'text': self.system, 'cache_control': {'type': 'ephemeral'}}],
                  messages=[{'role': 'user', 'content': user}],
                  output_config={'format': {'type': 'json_schema', 'schema': SCHEMA}})
        a = self.anthropic
        try:
            if self.model == 'claude-haiku-4-5':
                resp = self.client.messages.create(**kw)
            else:
                kw['output_config']['effort'] = 'medium'
                # відмова класифікатора безпеки — сервер сам повторює запит іншою моделлю
                resp = self.client.beta.messages.create(betas=['server-side-fallback-2026-07-01'],
                                                        fallbacks='default', **kw)
        except a.AuthenticationError:
            raise MTError('ключ Claude не підходить (перевір ключ у вікні)', fatal=True)
        except a.PermissionDeniedError as ex:
            raise MTError(f'Claude: немає доступу ({ex.message})', fatal=True)
        except a.BadRequestError as ex:
            msg = ex.message or ''
            fatal = 'credit' in msg.lower() or 'billing' in msg.lower()
            raise MTError(f'Claude: {msg}', fatal=fatal)
        except a.RateLimitError:
            raise MTError('Claude: забагато запитів — спробуй пізніше')
        except a.APIStatusError as ex:
            raise MTError(f'Claude: помилка сервера {ex.status_code}')
        except a.APIConnectionError:
            raise MTError('немає з\'єднання з Claude — перевір інтернет')
        u = resp.usage
        self.usage['input'] += u.input_tokens or 0
        self.usage['cache_write'] += getattr(u, 'cache_creation_input_tokens', 0) or 0
        self.usage['cache_read'] += getattr(u, 'cache_read_input_tokens', 0) or 0
        self.usage['output'] += u.output_tokens or 0
        if resp.stop_reason == 'refusal':
            raise MTError('Claude відмовився перекладати цей пакет')
        if resp.stop_reason == 'max_tokens':
            raise MTError('відповідь не влізла', split=True)
        text = next((b.text for b in resp.content if b.type == 'text'), '')
        try:
            got = json.loads(text)['translations']
        except (ValueError, KeyError, TypeError):
            raise MTError('Claude повернув не JSON — пакет пропущено')
        return {str(g.get('id')): g.get('text', '') for g in got if isinstance(g, dict)}

    def cost(self):
        pin, pout = self.price
        u = self.usage
        return (u['input'] * pin + u['cache_write'] * pin * 1.25 + u['cache_read'] * pin * 0.1
                + u['output'] * pout) / 1e6

    def spent(self):
        u, n = self.usage, (lambda v: f'{v:,}'.replace(',', ' '))
        return (f'токенів: {n(u["input"] + u["cache_write"] + u["cache_read"])} на вхід '
                f'(з кешу {n(u["cache_read"])}), {n(u["output"])} на вихід; ≈ ${self.cost():.2f}')

    def estimate(self, n_rows, chars):
        """Оцінка вартості до старту, $ (грубо: англійська ~4 знаки на токен, українська ~2,5;
        контекст сцени, JSON і роздуми моделі — множники з досвіду)."""
        pin, pout = self.price
        batches = max(1, -(-n_rows // self.batch_rows))
        sys_tok = len(self.system) / 2.5
        tin = chars / 4 * 2.5 + batches * 300
        tout = chars / 2.5 * 1.3 + batches * 150
        thinking = 0 if self.model == 'claude-haiku-4-5' else tout * 0.6
        return ((tin + sys_tok * 1.25) * pin + sys_tok * 0.1 * pin * (batches - 1)
                + (tout + thinking) * pout) / 1e6


# ============================================================ DeepL, Google (HTTP)
def _post(url, body, headers, who):
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'),
                                 headers=dict(headers, **{'Content-Type': 'application/json'}))
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as ex:
            code = ex.code
            try:
                detail = ex.read().decode('utf-8', 'replace')[:300]
            except Exception:                               # noqa: BLE001
                detail = ''
            if code in (401, 403):
                raise MTError(f'{who}: ключ не підходить ({code})', fatal=True)
            if code == 456:
                raise MTError(f'{who}: вичерпано ліміт символів на рахунку', fatal=True)
            if code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt * 2)
                continue
            raise MTError(f'{who}: помилка {code} {detail}')
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < 3:
                time.sleep(2 ** attempt * 2)
                continue
            raise MTError(f'немає з\'єднання з {who} — перевір інтернет')
    raise MTError(f'{who}: не відповідає')


class DeepL:
    """DeepL API (v2): режим розмітки — коди йдуть тегом <x/>, який DeepL не чіпає.
    Безкоштовний ключ (закінчується на «:fx») — 500 тис. знаків на місяць."""
    name, label, style = 'deepl', 'DeepL', 'xml'
    batch_rows, batch_chars = 40, 20000
    PRICE = 25.0                                    # $ за 1 млн знаків (DeepL API Pro)

    def __init__(self):
        self.key = keys.get('deepl')
        if not self.key:
            raise MTError('немає ключа DeepL', fatal=True)
        host = 'api-free.deepl.com' if self.key.endswith(':fx') else 'api.deepl.com'
        self.url = f'https://{host}/v2/translate'
        self.chars = 0

    def translate(self, items, scene):
        body = {'text': [it['text'] for it in items], 'source_lang': 'EN', 'target_lang': 'UK',
                'tag_handling': 'xml', 'ignore_tags': ['x'], 'preserve_formatting': True}
        ctx = '\n'.join(s['src'] for s in scene)
        if ctx:
            body['context'] = ctx                   # за контекст DeepL не рахує знаки
        got = _post(self.url, body, {'Authorization': f'DeepL-Auth-Key {self.key}'}, 'DeepL')
        self.chars += sum(len(it['text']) for it in items)
        tr = got.get('translations') or []
        return {it['id']: t.get('text', '') for it, t in zip(items, tr)}

    def spent(self):
        free = self.key.endswith(':fx')
        return (f'знаків: {self.chars:,}'.replace(',', ' ')
                + ('' if free else f'; ≈ ${self.chars * self.PRICE / 1e6:.2f}'))

    def estimate(self, n_rows, chars):
        return 0.0 if self.key.endswith(':fx') else chars * self.PRICE / 1e6


class Google:
    """Google Cloud Translation (Basic, v2) з ключем API: формат html — коди тегом <x/>."""
    name, label, style = 'google', 'Google', 'xml'
    batch_rows, batch_chars = 100, 25000
    PRICE = 20.0                                    # $ за 1 млн знаків

    def __init__(self):
        self.key = keys.get('google')
        if not self.key:
            raise MTError('немає ключа Google Cloud', fatal=True)
        self.chars = 0

    def translate(self, items, scene):
        url = 'https://translation.googleapis.com/language/translate/v2?' + urllib.parse.urlencode({'key': self.key})
        body = {'q': [it['text'] for it in items], 'source': 'en', 'target': 'uk', 'format': 'html'}
        got = _post(url, body, {}, 'Google')
        self.chars += sum(len(it['text']) for it in items)
        tr = (got.get('data') or {}).get('translations') or []
        return {it['id']: t.get('translatedText', '') for it, t in zip(items, tr)}

    def spent(self):
        return f'знаків: {self.chars:,}; ≈ ${self.chars * self.PRICE / 1e6:.2f}'.replace(',', ' ')

    def estimate(self, n_rows, chars):
        return chars * self.PRICE / 1e6
