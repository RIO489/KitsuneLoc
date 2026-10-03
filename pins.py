# -*- coding: utf-8 -*-
"""Картинки, прикріплені до тексту (з 2.11, прохання перекладача).

Забувається, як виглядає предмет, монстр чи персонаж або де стоїть напис. Перекладач
прикріплює картинку (файл чи з буфера) до слова — і вона показується в усіх рядках, де
це слово є (в оригіналі, словами тегів чи в японській), — або лише до одного рядка.
Хрестик ховає картинку в конкретному рядку («не тут»); сховане можна повернути.

`Переклад\\<гра>\\картинки\\картинки.json`:
  {"версія": 1, "картинки": [{"id", "файл", "слово", "рядок", "сховано": [ключі рядків]}]}
поруч — самі файли (PNG). Для будь-якої гри.
"""
import json, os, re, time, uuid

DIR = 'картинки'
FILE = 'картинки.json'


class Pins:
    def __init__(self, xl):
        self.dir = os.path.join(xl, DIR)
        self.path = os.path.join(self.dir, FILE)
        self.items = []
        try:
            with open(self.path, encoding='utf-8') as f:
                self.items = [x for x in json.load(f).get('картинки', []) if isinstance(x, dict) and x.get('файл')]
        except (OSError, ValueError, AttributeError):
            pass
        self._rx = {}

    def save(self):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path + '.new'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'версія': 1, 'картинки': self.items}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def file(self, item):
        p = os.path.join(self.dir, item['файл'])
        return p if os.path.exists(p) else None

    def add(self, image, word='', row=None):
        """image — PIL Image або шлях до файлу. Повертає запис."""
        from PIL import Image
        os.makedirs(self.dir, exist_ok=True)
        im = Image.open(image) if isinstance(image, str) else image
        if im.mode not in ('RGB', 'RGBA'):
            im = im.convert('RGBA' if 'A' in im.getbands() else 'RGB')
        name = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6] + '.png'
        im.save(os.path.join(self.dir, name))
        item = {'id': uuid.uuid4().hex[:10], 'файл': name, 'слово': (word or '').strip(),
                'рядок': None if (word or '').strip() else row, 'сховано': []}
        self.items.append(item)
        self.save()
        return item

    def get(self, pid):
        return next((x for x in self.items if x['id'] == pid), None)

    def remove(self, pid):
        item = self.get(pid)
        if not item:
            return
        self.items.remove(item)
        p = self.file(item)
        if p:
            try:
                os.remove(p)
            except OSError:
                pass
        self.save()

    def set_word(self, pid, word, row=None):
        item = self.get(pid)
        if item:
            item['слово'] = (word or '').strip()
            if item['слово']:
                item['рядок'] = None
            elif row and not item.get('рядок'):
                item['рядок'] = row              # без слова — лише до рядка, де змінили
            self.save()

    def hide(self, pid, row, on=True):
        item = self.get(pid)
        if not item:
            return
        h = item.setdefault('сховано', [])
        if on and row not in h:
            h.append(row)
        elif not on and row in h:
            h.remove(row)
        self.save()

    def _match(self, word, texts):
        rx = self._rx.get(word)
        if rx is None:
            # латинка/кирилиця — цілим словом без огляду на регістр; японська — підрядком
            rx = self._rx[word] = re.compile(r'(?<!\w)' + re.escape(word) + r'(?!\w)', re.I)
        return any(t and (rx.search(t) or (not word.isascii() and word in t)) for t in texts)

    def for_row(self, row, texts):
        """(показані, сховані) картинки рядка `row`; texts — оригінал словами, японська…"""
        shown, hidden = [], []
        for x in self.items:
            if x.get('слово'):
                hit = self._match(x['слово'], texts)
            else:
                hit = x.get('рядок') == row
            if hit:
                (hidden if row in (x.get('сховано') or []) else shown).append(x)
        return shown, hidden
