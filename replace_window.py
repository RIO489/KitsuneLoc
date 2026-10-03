# -*- coding: utf-8 -*-
"""Вікно «Замінити всюди» редактора перекладу (з 2.9, прохання перекладача).

Однакові рядки й так пов'язані (переклав один — перекладено всі), але схожі — ні:
«Avenir Warehouse №2» / «№4», однаковий опис з різним іменем. Тут перекладач задає
переклад фрагмента («Avenir Warehouse» -> «Склад Авенір»), а програма:
  - де перекладу ще немає — бере оригінал і замінює в ньому фрагмент (за замовчуванням
    лише коли після заміни не лишилось англійських слів, тобто рядок перекладено цілком);
  - де фрагмент лишився в готовому перекладі — замінює там.
Спершу показує, що зміниться; останню заміну можна скасувати. Логіка — Project.replace_plan.
"""
import tkinter as tk
from tkinter import ttk, messagebox


def one_line(s):
    return (s or '').replace('\n', ' ⏎ ')


class ReplaceWindow(tk.Toplevel):
    def __init__(self, ed, find=''):
        super().__init__(ed)
        self.ed, self.pr = ed, ed.pr
        self.plan, self.last = [], None          # last — план останньої заміни (для «Скасувати»)
        self.title('Замінити всюди')
        self.geometry('980x560')
        self.configure(bg=ed.c['bg'])
        self.transient(ed)
        pad = dict(padx=10, pady=(6, 0))

        top = ttk.Frame(self)
        top.pack(fill='x', **pad)
        ttk.Label(top, text='Фрагмент оригіналу:').grid(row=0, column=0, sticky='w')
        self.find = tk.StringVar(value=find)
        e1 = ttk.Entry(top, textvariable=self.find, width=40)
        e1.grid(row=0, column=1, sticky='w', padx=6)
        ttk.Label(top, text='Переклад фрагмента:').grid(row=1, column=0, sticky='w', pady=(4, 0))
        self.repl = tk.StringVar()
        e2 = ttk.Entry(top, textvariable=self.repl, width=40)
        e2.grid(row=1, column=1, sticky='w', padx=6, pady=(4, 0))
        (e2 if find else e1).focus_set()

        opts = ttk.Frame(self)
        opts.pack(fill='x', **pad)
        self.words = tk.BooleanVar(value=True)
        self.case = tk.BooleanVar(value=True)
        self.fill = tk.BooleanVar(value=True)
        self.clean = tk.BooleanVar(value=True)
        self.in_tr = tk.BooleanVar(value=True)
        self.scope = tk.StringVar(value='all')
        for row, items in enumerate((
                (('Цілі слова', self.words), ('Враховувати регістр', self.case)),
                (('Де перекладу ще немає — взяти оригінал і замінити фрагмент', self.fill),
                 ('…лише якщо після заміни не лишається англійських слів', self.clean)),
                (('Де фрагмент лишився в готовому перекладі — замінити там', self.in_tr),))):
            fr = ttk.Frame(opts)
            fr.pack(fill='x')
            for label, var in items:
                ttk.Checkbutton(fr, text=label, variable=var,
                                command=self._recount).pack(side='left', padx=(0, 14))
        fr = ttk.Frame(opts)
        fr.pack(fill='x', pady=(2, 0))
        ttk.Label(fr, text='Де:').pack(side='left')
        node = ed.tree.item(ed.node, 'text') if ed.node in ed.nodes else ''
        ttk.Radiobutton(fr, text='уся гра', value='all', variable=self.scope,
                        command=self._recount).pack(side='left', padx=6)
        ttk.Radiobutton(fr, text=f'вибраний розділ дерева («{node}»)', value='node', variable=self.scope,
                        command=self._recount).pack(side='left', padx=6)

        self.info = tk.StringVar()
        ttk.Label(self, textvariable=self.info, style='Hint.TLabel').pack(anchor='w', **pad)
        fr = ttk.Frame(self)
        fr.pack(fill='both', expand=True, padx=10, pady=(4, 0))
        self.tv = ttk.Treeview(fr, columns=('src', 'old', 'new'), show='headings', style='Ed.Treeview')
        for c, t, w in (('src', 'Оригінал', 300), ('old', 'Було', 250), ('new', 'Стане', 300)):
            self.tv.heading(c, text=t)
            self.tv.column(c, width=w)
        sb = ttk.Scrollbar(fr, orient='vertical', command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.tv.bind('<Double-1>', lambda ev: self.tv.identify_row(ev.y) and ed.goto(self.tv.identify_row(ev.y)))

        bf = ttk.Frame(self)
        bf.pack(fill='x', padx=10, pady=8)
        self.b_go = ttk.Button(bf, text='Замінити', command=self._apply)
        self.b_go.pack(side='left')
        self.b_undo = ttk.Button(bf, text='Скасувати останню заміну', command=self._undo, state='disabled')
        self.b_undo.pack(side='left', padx=6)
        ttk.Button(bf, text='Закрити', command=self.destroy).pack(side='right')
        for v in (self.find, self.repl):
            v.trace_add('write', lambda *_: self._later())
        self.bind('<Return>', lambda e: self._apply())
        self.bind('<Escape>', lambda e: self.destroy())
        self._recount()

    def _later(self):
        if getattr(self, '_job', None):
            self.after_cancel(self._job)
        self._job = self.after(250, self._recount)

    def _rows(self):
        if self.scope.get() == 'node':
            return self.ed._node_rows(self.ed.node)
        return self.pr.rows

    def _recount(self):
        self._job = None
        find, repl = self.find.get(), self.repl.get()
        self.tv.delete(*self.tv.get_children())
        if not find.strip():
            self.plan = []
            self.info.set('Впиши фрагмент оригіналу (або виділи його в полі «Оригінал» → права кнопка → '
                          '«Замінити всюди…») і його переклад.')
            self.b_go.state(['disabled'])
            return
        self.plan = self.pr.replace_plan(self._rows(), find, repl, self.words.get(), self.case.get(),
                                         self.fill.get(), self.in_tr.get(), self.clean.get())
        n_links = sum(len(self.pr.linked(k)) for k, _o, _n in self.plan)
        for k, old, new in self.plan[:2000]:
            self.tv.insert('', 'end', iid=k, values=(one_line(self.pr.by_key[k]['e']['src']),
                                                     one_line(old) or '—', one_line(new)))
        more = f' (показано перші 2000)' if len(self.plan) > 2000 else ''
        self.info.set(f'Зміниться перекладів: {len(self.plan)}' +
                      (f' (разом з однаковими рядками — {n_links} місць)' if n_links > len(self.plan) else '') +
                      f'{more}. Подвійний клік — перейти до рядка.' +
                      ('' if repl.strip() else '  Увага: переклад фрагмента порожній — фрагмент буде видалено.'))
        self.b_go.state(['!disabled'] if self.plan else ['disabled'])

    def _apply(self):
        if not self.plan:
            return
        if not self.repl.get().strip() and not messagebox.askyesno(
                'Замінити всюди', 'Переклад фрагмента порожній — фрагмент просто видалиться. Продовжити?',
                parent=self):
            return
        self.ed._commit()
        plan = self.plan
        changed = self.pr.apply_plan(plan)
        self.last = plan
        self.b_undo.state(['!disabled'])
        self.ed._after_bulk(changed, 'Замінено всюди')
        self.ed._refresh_pcs()
        self.ed._count(self.ed.view)
        self._recount()

    def _undo(self):
        if not self.last:
            return
        changed = self.pr.apply_plan(self.last, undo=True)
        self.ed._after_bulk(changed, 'Заміну скасовано')
        self.ed._refresh_pcs()
        self.ed._count(self.ed.view)
        self.last = None
        self.b_undo.state(['disabled'])
        self._recount()
