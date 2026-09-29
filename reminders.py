# -*- coding: utf-8 -*-
"""Нагадування перекладача: те, до чого він хоче повернутися пізніше.

Зберігаються з перекладом гри: `Переклад\\<гра>\\нагадування\\нагадування.json` + файли
(знімки) у тій самій теці. Розділи — SECTIONS (нові призначення додаються сюди:
ключ розділу -> назва); запис — dict:
  id, розділ, час (ISO), текст, ключ (рядок перекладу, якщо є), стан, хто,
  файл (знімок у теці нагадувань, якщо є), рамка [x0, y0, x1, y1] (де на знімку текст),
  примітка.
Запис на диск — атомарно (через .new).
"""
import json, os, shutil, time, uuid

SECTIONS = {
    'екран': 'Збережений журнал екрану гри',     # знімки з «Гра на екрані» (screenwatch_window)
}
FOLDER = 'нагадування'


class Store:
    def __init__(self, xl):
        self.dir = os.path.join(xl, FOLDER)
        self.path = os.path.join(self.dir, 'нагадування.json')
        try:
            with open(self.path, encoding='utf-8') as f:
                self.items = json.load(f).get('items', [])
        except (OSError, ValueError):
            self.items = []

    def save(self):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path + '.new'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'items': self.items}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def add(self, section, text, image=None, **fields):
        """Нове нагадування; image — шлях до знімка (копіюється в теку нагадувань)."""
        rid = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]
        item = {'id': rid, 'розділ': section, 'час': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'текст': text, 'примітка': ''}
        item.update({k: v for k, v in fields.items() if v is not None})
        if image and os.path.exists(image):
            os.makedirs(self.dir, exist_ok=True)
            name = rid + os.path.splitext(image)[1].lower()
            shutil.copy2(image, os.path.join(self.dir, name))
            item['файл'] = name
        self.items.append(item)
        self.save()
        return item

    def get(self, rid):
        return next((x for x in self.items if x['id'] == rid), None)

    def file(self, item):
        """Повний шлях до знімка запису або None."""
        p = item.get('файл') and os.path.join(self.dir, item['файл'])
        return p if p and os.path.exists(p) else None

    def set_note(self, rid, note):
        item = self.get(rid)
        if item is not None and item.get('примітка', '') != note:
            item['примітка'] = note
            self.save()

    def delete(self, rid):
        item = self.get(rid)
        if item is None:
            return
        p = self.file(item)
        if p:
            try:
                os.remove(p)
            except OSError:
                pass
        self.items.remove(item)
        self.save()

    def by_section(self):
        """{розділ: [записи, новіші перші]} — у порядку SECTIONS (невідомі розділи — в кінці)."""
        out = {k: [] for k in SECTIONS}
        for x in self.items:
            out.setdefault(x.get('розділ', ''), []).append(x)
        for v in out.values():
            v.sort(key=lambda x: x.get('час', ''), reverse=True)
        return out
