# -*- coding: utf-8 -*-
"""Вікно «Замінити всюди» редактора перекладу на Qt — те саме, що replace_window.py (Tk).

Однакові рядки й так пов'язані (переклав один — перекладено всі), але схожі — ні:
«Avenir Warehouse №2» / «№4», однаковий опис з різним іменем. Тут перекладач задає
переклад фрагмента («Avenir Warehouse» -> «Склад Авенір»), а програма:
  - де перекладу ще немає — бере оригінал і замінює в ньому фрагмент (за замовчуванням
    лише коли після заміни не лишилось англійських слів, тобто рядок перекладено цілком);
  - де фрагмент лишився в готовому перекладі — замінює там.
Спершу показує, що зміниться; останню заміну можна скасувати. Логіка — Project.replace_plan.

Від редактора (qtui/editor.EditorWindow) потрібні: pr, node, _node_rows(node), node_title(),
goto(k), _commit(), _after_bulk(keys, що), _refresh_pcs(), _count().
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QDialog, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

SHOW_MAX = 2000                     # скільки рядків плану показати в списку


def one_line(s):
    return (s or '').replace('\n', ' ⏎ ')


class ReplaceWindow(QDialog):
    def __init__(self, ed, find=''):
        super().__init__(ed)
        self.ed, self.pr = ed, ed.pr
        self.plan, self.last = [], None          # last — план останньої заміни (для «Скасувати»)
        self.setWindowTitle('Замінити всюди')
        self.resize(980, 560)
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        g = QGridLayout()
        g.addWidget(QLabel('Фрагмент оригіналу:'), 0, 0)
        self.find = QLineEdit(find)
        self.find.setFixedWidth(340)
        g.addWidget(self.find, 0, 1)
        g.addWidget(QLabel('Переклад фрагмента:'), 1, 0)
        self.repl = QLineEdit()
        self.repl.setFixedWidth(340)
        g.addWidget(self.repl, 1, 1)
        g.setColumnStretch(2, 1)
        lay.addLayout(g)
        (self.repl if find else self.find).setFocus()

        def check(text, on=True):
            c = QCheckBox(text)
            c.setChecked(on)
            c.toggled.connect(lambda _on: self._recount())
            return c
        self.words = check('Цілі слова')
        self.case = check('Враховувати регістр')
        self.fill = check('Де перекладу ще немає — взяти оригінал і замінити фрагмент')
        self.clean = check('…лише якщо після заміни не лишається англійських слів')
        self.in_tr = check('Де фрагмент лишився в готовому перекладі — замінити там')
        for items in ((self.words, self.case), (self.fill, self.clean), (self.in_tr,)):
            h = QHBoxLayout()
            for c in items:
                h.addWidget(c)
                h.addSpacing(10)
            h.addStretch(1)
            lay.addLayout(h)
        h = QHBoxLayout()
        h.addWidget(QLabel('Де:'))
        self.scope = QButtonGroup(self)
        self.r_all = QRadioButton('уся гра')
        self.r_all.setChecked(True)
        self.r_node = QRadioButton(f'вибраний розділ дерева («{ed.node_title()}»)')
        for rb in (self.r_all, self.r_node):
            self.scope.addButton(rb)
            rb.toggled.connect(lambda on: on and self._recount())
            h.addWidget(rb)
        h.addStretch(1)
        lay.addLayout(h)

        self.info = QLabel('')
        self.info.setObjectName('Hint')
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.tv = QTreeWidget()
        self.tv.setColumnCount(3)
        self.tv.setHeaderLabels(['Оригінал', 'Було', 'Стане'])
        self.tv.setRootIsDecorated(False)
        self.tv.setUniformRowHeights(True)
        self.tv.setSelectionMode(QAbstractItemView.SingleSelection)
        for c, w in ((0, 300), (1, 250)):
            self.tv.setColumnWidth(c, w)
        self.tv.header().setSectionResizeMode(QHeaderView.Interactive)
        self.tv.header().setStretchLastSection(True)
        self.tv.itemDoubleClicked.connect(lambda it, _c: self.ed.goto(it.data(0, Qt.UserRole)))
        lay.addWidget(self.tv, 1)

        h = QHBoxLayout()
        self.b_go = QPushButton('Замінити')
        self.b_go.setObjectName('Accent')
        self.b_go.setDefault(True)               # Enter — замінити
        self.b_go.clicked.connect(self._apply)
        h.addWidget(self.b_go)
        self.b_undo = QPushButton('Скасувати останню заміну')
        self.b_undo.setEnabled(False)
        self.b_undo.setAutoDefault(False)
        self.b_undo.clicked.connect(self._undo)
        h.addWidget(self.b_undo)
        h.addStretch(1)
        b = QPushButton('Закрити')
        b.setAutoDefault(False)
        b.clicked.connect(self.close)            # Escape — теж закрити (QDialog)
        h.addWidget(b)
        lay.addLayout(h)

        self.t_recount = QTimer(self, singleShot=True, interval=250, timeout=self._recount)
        for e in (self.find, self.repl):
            e.textChanged.connect(lambda _t: self.t_recount.start())
        self._recount()

    def _rows(self):
        if self.r_node.isChecked():
            return self.ed._node_rows(self.ed.node)
        return self.pr.rows

    def _recount(self):
        self.t_recount.stop()
        find, repl = self.find.text(), self.repl.text()
        self.tv.clear()
        if not find.strip():
            self.plan = []
            self.info.setText('Впиши фрагмент оригіналу (або виділи його в полі «Оригінал» → права кнопка → '
                              '«Замінити всюди…») і його переклад.')
            self.b_go.setEnabled(False)
            return
        self.plan = self.pr.replace_plan(self._rows(), find, repl, self.words.isChecked(), self.case.isChecked(),
                                         self.fill.isChecked(), self.in_tr.isChecked(), self.clean.isChecked())
        n_links = sum(len(self.pr.linked(k)) for k, _o, _n in self.plan)
        items = []
        for k, old, new in self.plan[:SHOW_MAX]:
            it = QTreeWidgetItem([one_line(self.pr.by_key[k]['e']['src']), one_line(old) or '—', one_line(new)])
            it.setData(0, Qt.UserRole, k)
            items.append(it)
        self.tv.addTopLevelItems(items)
        more = f' (показано перші {SHOW_MAX})' if len(self.plan) > SHOW_MAX else ''
        self.info.setText(f'Зміниться перекладів: {len(self.plan)}' +
                          (f' (разом з однаковими рядками — {n_links} місць)' if n_links > len(self.plan) else '') +
                          f'{more}. Подвійний клік — перейти до рядка.' +
                          ('' if repl.strip() else '  Увага: переклад фрагмента порожній — фрагмент буде видалено.'))
        self.b_go.setEnabled(bool(self.plan))

    def _apply(self):
        if self.t_recount.isActive():            # Enter одразу після набору — спершу свіжий план
            self._recount()
        if not self.plan:
            return
        if not self.repl.text().strip() and QMessageBox.question(
                self, 'Замінити всюди',
                'Переклад фрагмента порожній — фрагмент просто видалиться. Продовжити?') != QMessageBox.Yes:
            return
        self.ed._commit()
        plan = self.plan
        changed = self.pr.apply_plan(plan)
        self.last = plan
        self.b_undo.setEnabled(True)
        self._done(changed, 'Замінено всюди')

    def _undo(self):
        if not self.last:
            return
        self.ed._commit()
        changed = self.pr.apply_plan(self.last, undo=True)
        self.last = None
        self.b_undo.setEnabled(False)
        self._done(changed, 'Заміну скасовано')

    def _done(self, changed, what):
        self.ed._after_bulk(changed, what)
        self.ed._refresh_pcs()
        self.ed._count()
        self._recount()
