# -*- coding: utf-8 -*-
"""Вікно «Нагадування»: розділи (reminders.SECTIONS) і записи в них.

Ліворуч — розділи й записи (новіші вгорі), праворуч — вибраний запис: знімок з
рамкою навколо тексту (якщо є), що це за рядок, примітка (зберігається сама),
«Відкрити рядок у редакторі», «Видалити». Нагадування — окремо для кожної гри
(Переклад\\<гра>\\нагадування).
"""
import os
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageDraw, ImageTk

import reminders

PIC_W = 640


class RemindersWindow(tk.Toplevel):
    def __init__(self, app, xl, title):
        super().__init__(app)
        self.app, self.xl = app, xl
        self.title(f'Нагадування — {title}')
        self.geometry('1100x680')
        self.minsize(760, 440)
        c = app.colors()
        self.configure(bg=c['bg'])
        self.cur = None
        self.photo = None
        self._note_job = None
        self._build(c)
        try:
            app.dark_titlebar(self)
        except Exception:                                   # noqa: BLE001
            pass
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.reload()

    def _build(self, c):
        body = ttk.Panedwindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=10, pady=10)
        left = ttk.Frame(body)
        self.tree = ttk.Treeview(left, show='tree', selectmode='browse')
        sb = ttk.Scrollbar(left, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda e: self._select())
        self.tree.bind('<Delete>', lambda e: self._delete())
        body.add(left, weight=1)

        right = ttk.Frame(body, padding=(10, 0, 0, 0))
        body.add(right, weight=2)
        self.head = tk.StringVar()
        ttk.Label(right, textvariable=self.head, font=('Segoe UI', 11, 'bold'),
                  wraplength=PIC_W, justify='left').pack(anchor='w')
        self.info = tk.StringVar()
        ttk.Label(right, textvariable=self.info, style='Hint.TLabel').pack(anchor='w', pady=(2, 6))
        self.pic = tk.Label(right, bg=c['bg'], bd=0, fg=c.get('dim', '#888888'))
        self.pic.pack(anchor='w')
        nf = ttk.Frame(right)
        nf.pack(fill='x', pady=(8, 0))
        ttk.Label(nf, text='Примітка:').pack(side='left')
        self.note = tk.StringVar()
        self.note_entry = ttk.Entry(nf, textvariable=self.note, width=60)
        self.note_entry.pack(side='left', padx=6)
        self.note.trace_add('write', lambda *_: self._note_changed())
        bf = ttk.Frame(right)
        bf.pack(fill='x', pady=(8, 0))
        self.b_open = ttk.Button(bf, text='Відкрити рядок у редакторі', command=self._open)
        self.b_open.pack(side='left')
        self.b_del = ttk.Button(bf, text='Видалити нагадування', command=self._delete)
        self.b_del.pack(side='left', padx=8)
        self.hint = tk.StringVar()
        ttk.Label(self, textvariable=self.hint, style='Hint.TLabel', padding=(10, 0, 10, 8)).pack(fill='x')

    # ------------------------------------------------------------ дані
    def reload(self, xl=None, title=None):
        """Перечитати нагадування (нове додано з іншого вікна, або перемкнули гру)."""
        if xl:
            self.xl = xl
        if title:
            self.title(f'Нагадування — {title}')
        self._flush_note()
        keep = self.cur
        self.store = reminders.Store(self.xl)
        self.tree.delete(*self.tree.get_children())
        for sec, items in self.store.by_section().items():
            name = reminders.SECTIONS.get(sec, sec or 'Інше')
            node = self.tree.insert('', 'end', iid='sec:' + sec, text=f'{name} ({len(items)})', open=True)
            for x in items:
                when = x.get('час', '')
                when = f'{when[8:10]}.{when[5:7]} {when[11:16]}' if len(when) >= 16 else when
                self.tree.insert(node, 'end', iid=x['id'], text=f'{when} · {" ".join(x.get("текст", "").split())[:70]}')
        n = len(self.store.items)
        self.hint.set('Нагадувань: %d. Додати: «Гра на екрані» → права кнопка по рядку журналу → '
                      '«Зберегти в нагадування».' % n)
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
        else:
            self._show(None)

    def _select(self):
        sel = self.tree.selection()
        self._flush_note()
        self._show(self.store.get(sel[0]) if sel and not sel[0].startswith('sec:') else None)

    def _show(self, item):
        self.cur = item['id'] if item else None
        state = ['!disabled'] if item else ['disabled']
        for w in (self.b_del, self.note_entry):
            w.state(state)
        self.b_open.state(['!disabled'] if item and item.get('ключ') else ['disabled'])
        self._loading = True
        self.note.set(item.get('примітка', '') if item else '')
        self._loading = False
        if not item:
            self.head.set('')
            self.info.set('')
            self.pic.configure(image='', text='Вибери нагадування ліворуч.')
            return
        self.head.set(' '.join(item.get('текст', '').split()))
        when = item.get('час', '').replace('T', ' ')
        self.info.set(' · '.join(x for x in (item.get('стан'), item.get('хто'), when) if x))
        p = self.store.file(item)
        if not p:
            self.pic.configure(image='', text='(без знімка)')
            return
        try:
            img = Image.open(p).convert('RGB')
        except OSError:
            self.pic.configure(image='', text='(знімок не читається)')
            return
        k = min(1.0, PIC_W / img.width)
        im = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.BILINEAR)
        box = item.get('рамка')
        if box:
            d = ImageDraw.Draw(im)
            d.rectangle([box[0] * k - 3, box[1] * k - 3, box[2] * k + 3, box[3] * k + 3],
                        outline='#ffcc33', width=3)
        self.photo = ImageTk.PhotoImage(im)
        self.pic.configure(image=self.photo, text='')

    # ------------------------------------------------------------ дії
    def _note_changed(self):
        if getattr(self, '_loading', False) or not self.cur:
            return
        if self._note_job:
            self.after_cancel(self._note_job)
        self._note_job = self.after(700, self._flush_note)

    def _flush_note(self):
        if self._note_job:
            self.after_cancel(self._note_job)
            self._note_job = None
        if self.cur and getattr(self, 'store', None) is not None:
            try:
                self.store.set_note(self.cur, self.note.get())
            except OSError:
                pass

    def _open(self):
        item = self.cur and self.store.get(self.cur)
        if item and item.get('ключ'):
            self.app.open_editor(goto=item['ключ'])

    def _delete(self):
        item = self.cur and self.store.get(self.cur)
        if not item:
            return
        if not messagebox.askyesno('Нагадування', 'Видалити це нагадування (разом зі знімком)?', parent=self):
            return
        self.store.delete(item['id'])
        self.cur = None
        self.reload()

    def _close(self):
        self._flush_note()
        if getattr(self.app, 'reminders_win', None) is self:
            self.app.reminders_win = None
        self.destroy()
