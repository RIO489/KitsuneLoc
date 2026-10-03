# -*- coding: utf-8 -*-
"""«Редактор перекладу» на Qt (прототип, гілка qt) — те саме, що editor.py (Tk), інший вигляд.

Ліворуч — дерево (усі рядки / повтори / мої групи / за файлами гри), посередині — рядки
(модель Qt: малює лише видиме, 34 тис. рядків MSK — без затримки), праворуч — вибраний
рядок: оригінал, японська, переклад (Enter — далі, Shift+Enter — новий рядок), «Вичитано»,
«Машинний», однакові рядки, примітка, попередження перевірки, терміни, прев'ю шрифтом гри.
Дані — project.Project; зберігається само (як у Tk-редакторі).

Вікна «Машинний переклад…» (qtui/mt_window.py) і «Замінити всюди» (qtui/replace_window.py, Ctrl+H),
«Гра на екрані» і «Терміни» (через головне вікно), «Відкрити у вікні написів…».
Як у Tk-редакторі: групи (додати / перейменувати / прибрати / видалити), пошук ▲▼ з підсвіткою,
теги-посилання словами, схожі переклади, картинки до тексту, межі вікон діалогу й окремого
рядка (клік по прев'ю — межа там), масштаб прев'ю, Crowdin (Neptunia).
"""
import os, queue, re, threading

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QObject, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter, QTableView,
                               QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout, QWidget)

import glossary, project, sheets
from qtui import theme as qtheme

FILTERS = ('Усі', 'Неперекладені', 'Перекладені', 'Відв\'язані', 'З приміткою', 'Не перекладати',
           'Лише машинний', 'Переклад ≠ машинний', 'Вичитані', 'Не вичитані')
COUNT_MODES = {'all': '⇄ Усе', 'uniq': '⇄ Без повторів', 'hidden': '⇄ Приховане'}
COMMIT_MS, SAVE_MS = 250, 1500


def one_line(s, n=200):
    s = (s or '').replace('\n', ' ⏎ ')
    return s if len(s) <= n else s[:n] + '…'


def chars_word(n):
    last2, last = n % 100, n % 10
    w = 'знаків' if 11 <= last2 <= 14 else 'знак' if last == 1 else 'знаки' if 2 <= last <= 4 else 'знаків'
    return f'{n} {w}'


class Bridge(QObject):
    """Фоновий потік -> вікно (сигнали Qt потокобезпечні)."""
    got = Signal(str, object)


# ============================================================ модель списку
class RowsModel(QAbstractTableModel):
    HEADS = ('№', '', 'Хто', 'Оригінал', 'Переклад')

    def __init__(self, ed):
        super().__init__()
        self.ed, self.pr = ed, ed.pr
        self.rows, self.pos = [], {}
        t = ed.t
        self.c_dim, self.c_warn = QColor(t['dim']), QColor(t['warn'])
        self.c_ok = QColor(t['ok'])
        self.italic = QFont('Segoe UI', 10)
        self.italic.setItalic(True)

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.pos = {r['k']: i for i, r in enumerate(rows)}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADS)

    def headerData(self, s, orient, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orient == Qt.Horizontal:
            return self.HEADS[s]
        return None

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        r, c = self.rows[idx.row()], idx.column()
        pr = self.pr
        if role == Qt.DisplayRole:
            if c == 0:
                return str(idx.row() + 1)
            if c == 1:
                twins = len(pr.twins(r['k']))
                st = '⊘' if r.get('лишити') else '✂' if r.get('окремо') else (f'×{twins}' if twins > 1 else '')
                if pr.reviewed(r):
                    st = '✓' + st
                elif not r['e'].get('tr') and pr.mt(r):
                    st = '≈' + st
                return st
            if c == 2:
                return 'ім\'я' if r['kind'] == 'name' else 'ключ' if r['kind'] == 'key' else pr.speaker(r)
            if c == 3:
                return one_line(r['e']['src'])
            tr = r['e'].get('tr', '')
            if not tr and pr.mt(r):
                return '≈ ' + one_line(pr.mt(r))
            return one_line(tr)
        if role == Qt.ForegroundRole:
            if r.get('лишити'):
                return self.c_dim
            if r.get('окремо'):
                return self.c_warn
            if c == 1 and pr.reviewed(r):
                return self.c_ok
            if not r['e'].get('tr'):
                return self.c_dim
            return None
        if role == Qt.FontRole and (r.get('лишити') or (not r['e'].get('tr') and pr.mt(r) and c == 4)):
            return self.italic
        if role == Qt.TextAlignmentRole and c == 0:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def changed(self, keys):
        for k in keys:
            i = self.pos.get(k)
            if i is not None:
                self.dataChanged.emit(self.index(i, 0), self.index(i, len(self.HEADS) - 1))


# ============================================================ поле перекладу
class TrEdit(QPlainTextEdit):
    """Enter — наступний рядок, Shift/Alt+Enter — перенос, Ctrl+↑/↓ — сусідній рядок."""

    def __init__(self, ed):
        super().__init__()
        self.ed = ed

    def keyPressEvent(self, ev):
        k, mods = ev.key(), ev.modifiers()
        if k in (Qt.Key_Return, Qt.Key_Enter):
            if mods & (Qt.ShiftModifier | Qt.AltModifier):
                self.insertPlainText('\n')
            else:
                self.ed.step(1)
            return
        if mods & Qt.ControlModifier and k in (Qt.Key_Up, Qt.Key_Down):
            self.ed.step(-1 if k == Qt.Key_Up else 1)
            return
        super().keyPressEvent(ev)


def _clear(lay):
    """Прибрати віджети з розкладки: одразу сховати й від'єднати (deleteLater видаляє лише в циклі
    подій — до того старі кнопки лишались видимими поверх нових), потім видалити."""
    while lay.count():
        w = lay.takeAt(0).widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()


class _ClickPic(QLabel):
    """Картинка прев'ю: клік — x у пікселях картинки (межа вікна / рядка там, де клікнули)."""

    def __init__(self, on_click):
        super().__init__()
        self.on_click = on_click
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.on_click(ev.position().x())


class _PicLabel(QLabel):
    """Мініатюра картинки до тексту: подвійний клік — переглянути, права кнопка — меню."""

    def __init__(self, on_open, on_menu):
        super().__init__()
        self.on_open, self.on_menu = on_open, on_menu

    def mouseDoubleClickEvent(self, ev):
        self.on_open()

    def contextMenuEvent(self, ev):
        self.on_menu(ev.pos())


def _fit(edit, lo, hi):
    """Висота поля — за текстом (рядків на екрані з переносами), у межах lo..hi рядків."""
    doc = edit.document()
    # у QPlainTextEdit висота документа — у рядках (з переносами за шириною поля)
    n = max(lo, min(hi, int(doc.documentLayout().documentSize().height()) or 1))
    line = edit.fontMetrics().lineSpacing()
    edit.setFixedHeight(int(n * line + 2 * edit.frameWidth() + doc.documentMargin() * 2 + 10))


# ============================================================ вікно
class EditorWindow(QMainWindow):
    def __init__(self, pr, backup_dir, theme_name='stars', title=None, goto=None,
                 settings=None, save_settings=None, games=None, app=None):
        """settings — словник налаштувань програми (settings.json) або None: тоді нічого не
        запам'ятовується (згода на машинний переклад — щоразу); save_settings(settings) — записати
        його; games — GAMES програми (назва гри для машинного перекладу); app — головне вікно
        (qtui/main.py: «Терміни», «Гра на екрані», нагадування) або None."""
        super().__init__()
        self.app, self.watch_win = app, None
        self.pr, self.bk, self.game = pr, backup_dir, pr.game
        self.settings, self.save_settings, self.games = settings, save_settings, games or {}
        self._refs = None
        self._mt_win = self._replace_win = None
        self.t = qtheme.palette(theme_name)
        self.theme_name = theme_name
        self.terms = glossary.load(pr.xl)
        self.approved = sheets.load_approved(pr.xl)
        self.ctx = None
        self.fonts = None
        self.cur = None
        self.view = []
        self.node = ('all', None)
        self.count_mode = 'all'
        self._loading = False
        self._text_for = None
        self.setWindowTitle(title or f'Редактор перекладу — {os.path.basename(pr.xl)}')
        self.resize(1480, 900)
        self.setStyleSheet(qtheme.qss(theme_name))
        self.bridge = Bridge()
        self.bridge.got.connect(self._got)
        self.t_commit = QTimer(self, singleShot=True, interval=COMMIT_MS, timeout=self._commit)
        self.t_save = QTimer(self, singleShot=True, interval=SAVE_MS, timeout=self._save)
        self.t_preview = QTimer(self, singleShot=True, interval=300, timeout=self._preview)
        self.t_filter = QTimer(self, singleShot=True, interval=300, timeout=self._refill)
        self._build()
        self._fill_nodes()
        self._refill()
        threading.Thread(target=self._load_bg, daemon=True).start()
        if goto:
            QTimer.singleShot(0, lambda: self.goto(goto))

    # ------------------------------------------------------------ вигляд
    def _panel(self):
        f = QFrame()
        f.setObjectName('Panel')
        lay = QVBoxLayout(f)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        self.back.glow(f)
        return f, lay

    def _build(self):
        self.back = qtheme.Backdrop(self.theme_name)
        self.setCentralWidget(self.back)
        root = QVBoxLayout(self.back)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(10)

        # --- верхня смуга -------------------------------------------------
        top = QHBoxLayout()
        top.addWidget(QLabel('Пошук:'))
        self.search = QLineEdit()
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(lambda _t: self.t_filter.start())
        top.addWidget(self.search)
        # наступний / попередній збіг пошуку: ▲▼, Enter / Shift+Enter у полі пошуку, F3 / Shift+F3
        for text, d in (('▲', -1), ('▼', 1)):
            b = QPushButton(text)
            b.setObjectName('Small')         # вузька кнопка: звичайні відступи з'їли б увесь текст
            b.setFixedWidth(34)
            b.clicked.connect(lambda _c=False, d=d: self._hit(d))
            top.addWidget(b)
        self.hit_info = QLabel('')
        self.hit_info.setObjectName('Hint')
        self.hit_info.setMinimumWidth(70)
        top.addWidget(self.hit_info)
        self.search.returnPressed.connect(lambda: self._hit(1))
        for seq, d, ctx in (('Shift+Return', -1, self.search), ('F3', 1, self), ('Shift+F3', -1, self)):
            sc = QShortcut(QKeySequence(seq), ctx, activated=lambda d=d: self._hit(d))
            if ctx is self.search:
                sc.setContext(Qt.WidgetShortcut)
        top.addSpacing(14)
        top.addWidget(QLabel('Показати:'))
        self.flt = QComboBox()
        self.flt.addItems(FILTERS)
        self.flt.currentIndexChanged.connect(lambda _i: self._refill())
        top.addWidget(self.flt)
        top.addStretch(1)
        b = QPushButton('Гра на екрані…')
        b.clicked.connect(self._watch_window)
        b.setEnabled(self.app is not None)
        top.addWidget(b)
        b = self.b_mt = QPushButton('Машинний переклад…')
        b.clicked.connect(lambda: self._mt_window())
        top.addWidget(b)
        b = QPushButton('Терміни…')
        b.clicked.connect(lambda: self.app.open_terms())
        b.setEnabled(self.app is not None and hasattr(self.app, 'open_terms'))
        top.addWidget(b)
        if self.game == 'nep' and self.app is not None:     # тексти stcm-editor/Crowdin є лише в Neptunia
            b = QPushButton('Crowdin')
            m = QMenu(b)
            m.addAction('Забрати переклад з Crowdin (.zip)…', lambda: self._from_crowdin(True))
            m.addAction('Забрати переклад з Crowdin (тека)…', lambda: self._from_crowdin(False))
            m.addSeparator()
            m.addAction('Вивантажити переклад для Crowdin (.zip)…', self._to_crowdin)
            b.setMenu(m)
            top.addWidget(b)
        b = QPushButton('Вивантажити в Excel')
        b.clicked.connect(self._to_excel)
        top.addWidget(b)
        b = QPushButton('Завантажити з Excel…')
        b.clicked.connect(self._from_excel)
        top.addWidget(b)
        root.addLayout(top)

        # --- три панелі ---------------------------------------------------
        self.split = QSplitter(Qt.Horizontal)
        self.split.setHandleWidth(14)
        self.split.splitterMoved.connect(lambda *_a: (self.back.update(), self._place_overlays(),
                                                      self.pv_zoom == 'fit' and self.t_preview.start(200)))
        root.addWidget(self.split, 1)

        left, ll = self._panel()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.currentItemChanged.connect(self._node_selected)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        ll.addWidget(self.tree, 1)
        b = self.b_group = QPushButton('Нова група з виділених…')
        b.clicked.connect(lambda: self._to_group(None))
        ll.addWidget(b)
        self._left_lay = ll
        self.split.addWidget(left)

        mid, ml = self._panel()
        self.model = RowsModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(25)
        hh = self.table.horizontalHeader()
        for col, w in ((0, 54), (1, 44), (2, 110)):
            hh.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table.setColumnWidth(col, w)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.selectionModel().currentRowChanged.connect(self._row_selected)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._list_menu)
        self.table.doubleClicked.connect(lambda _i: self.tr_edit.setFocus())
        ml.addWidget(self.table)
        self.split.addWidget(mid)

        right, rl = self._panel()
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        inner = QWidget()
        self.rlay = QVBoxLayout(inner)
        self.rlay.setContentsMargins(4, 2, 8, 2)
        self.rlay.setSpacing(4)
        sa.setWidget(inner)
        rl.addWidget(sa)
        self.split.addWidget(right)
        self._build_row(self.rlay)
        self.left_panel, self.right_panel = left, right
        self.split.setSizes([300, 640, 640])
        self.split.setStretchFactor(1, 1)

        # --- нижня смуга --------------------------------------------------
        bot = QHBoxLayout()
        self.status = QLabel('')
        self.status.setObjectName('Hint')
        bot.addWidget(self.status, 1)
        self.ontop = QCheckBox('Поверх усіх вікон')
        self.ontop.toggled.connect(self._set_ontop)
        bot.addWidget(self.ontop)
        self.b_count = QPushButton(COUNT_MODES[self.count_mode])
        self.b_count.clicked.connect(self._count_next)
        bot.addWidget(self.b_count)
        self.count = QLabel('')
        bot.addWidget(self.count)
        root.addLayout(bot)

        QShortcut(QKeySequence('Ctrl+F'), self, activated=self.search.setFocus)
        QShortcut(QKeySequence('Ctrl+M'), self, activated=self._take_mt)
        QShortcut(QKeySequence('F8'), self, activated=lambda: self._set_reviewed(not self.rev.isChecked()))
        QShortcut(QKeySequence('Ctrl+H'), self, activated=lambda: self._replace_all())
        # права кнопка в полях: виділений фрагмент -> «Замінити всюди» (як у Tk-редакторі)
        for w in (self.src, self.tr_edit, self.ja):
            w.setContextMenuPolicy(Qt.CustomContextMenu)
            w.customContextMenuRequested.connect(lambda pos, w=w: self._field_menu(w, pos))
        # теги-посилання (<CHARA=…>) в оригіналі й японській — жовтим словом: наведи — підказка, клік —
        # скопіювати тег (tagrefs; з 2.11)
        for w in (self.src, self.ja):
            w.ref_spans = []
            w.viewport().setMouseTracking(True)
            w.viewport().installEventFilter(self)

        # персонажі гри поверх панелей (тема з прикрасами)
        self.overlays = []
        deco = self.t.get('deco')
        if deco and deco.get('portrait'):
            for pil, corner in zip(qtheme.portraits(self.game, 2), ('left', 'right')):
                o = qtheme.Overlay(self.back, pil, corner)
                self.overlays.append(o)
                if corner == 'left':           # місце під кнопкою — персонаж не ховає пункти дерева
                    self._left_lay.addSpacing(o.height() - 24)

    def _section(self, lay, text):
        lab = QLabel(text)
        lab.setObjectName('Section')
        lay.addWidget(lab)
        return lab

    def _build_row(self, lay):
        self.head = QLabel('')
        self.head.setObjectName('Head')
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.where = QLabel('')
        self.where.setObjectName('Hint')
        self.where.setWordWrap(True)
        lay.addWidget(self.where)
        lay.addSpacing(4)

        self._section(lay, '✦ Оригінал')
        self.src = QPlainTextEdit()
        self.src.setReadOnly(True)
        lay.addWidget(self.src)
        self.ja_lab = self._section(lay, '✦ Японська')
        self.ja = QPlainTextEdit()
        self.ja.setReadOnly(True)
        lay.addWidget(self.ja)

        h = QHBoxLayout()
        lab = QLabel('✦ Переклад')
        lab.setObjectName('Section')
        h.addWidget(lab)
        self.tr_count = QLabel('')
        self.tr_count.setObjectName('Hint')
        h.addWidget(self.tr_count)
        h.addStretch(1)
        hint = QLabel('Enter — далі · Shift+Enter — новий рядок · Ctrl+↑/↓ — сусідній рядок')
        hint.setObjectName('Hint')
        h.addWidget(hint)
        lay.addLayout(h)
        self.tr_edit = TrEdit(self)
        self.tr_edit.textChanged.connect(self._modified)
        lay.addWidget(self.tr_edit)
        self.rev = QCheckBox('Вичитано (F8)')
        self.rev.clicked.connect(lambda on: self._set_reviewed(on))
        lay.addWidget(self.rev)

        h = QHBoxLayout()
        self.mt_head = QLabel('Машинний — ще немає')
        h.addWidget(self.mt_head, 1)
        self.b_take = QPushButton('Взяти в переклад (Ctrl+M)')
        self.b_take.clicked.connect(self._take_mt)
        h.addWidget(self.b_take)
        self.b_mt_clear = QPushButton('Прибрати')
        self.b_mt_clear.clicked.connect(lambda: self._clear_mt([self.cur] if self.cur else []))
        h.addWidget(self.b_mt_clear)
        lay.addLayout(h)
        self.mt = QPlainTextEdit()
        self.mt.setReadOnly(True)
        lay.addWidget(self.mt)

        h = QHBoxLayout()
        self.link_info = QLabel('')
        self.link_info.setObjectName('Hint')
        self.link_info.setWordWrap(True)
        h.addWidget(self.link_info, 1)
        self.b_link = QPushButton('')
        self.b_link.clicked.connect(self._toggle_link)
        h.addWidget(self.b_link)
        # напис на картинці — одразу у вікно написів на цей напис (як у Tk-редакторі)
        self.b_atlas = QPushButton('Відкрити у вікні написів…')
        self.b_atlas.clicked.connect(self._open_atlas)
        self.b_atlas.hide()
        h.addWidget(self.b_atlas)
        lay.addLayout(h)

        h = QHBoxLayout()
        h.addWidget(QLabel('Примітка:'))
        self.note = QLineEdit()
        self.note.textEdited.connect(self._note_changed)
        h.addWidget(self.note, 1)
        lay.addLayout(h)
        self.warn = QLabel('')
        self.warn.setObjectName('Warn')
        self.warn.setWordWrap(True)
        lay.addWidget(self.warn)

        self._section(lay, '✦ Терміни в рядку (клік — вставити переклад)')
        self.terms_box = QVBoxLayout()
        self.terms_box.setSpacing(2)
        lay.addLayout(self.terms_box)
        # схожі вже перекладені рядки — як пам'ять перекладів у Crowdin (Project.similar)
        self.sim_lab = self._section(lay, '✦ Схожі вже перекладені (клік — взяти переклад у поле)')
        self.sim_w = QWidget()
        self.sim_box = QVBoxLayout(self.sim_w)
        self.sim_box.setContentsMargins(0, 0, 0, 0)
        self.sim_box.setSpacing(4)
        lay.addWidget(self.sim_w)
        self.sim_lab.hide()
        self.sim_w.hide()
        self.t_sim = QTimer(self, singleShot=True, interval=120, timeout=self._fill_similar)

        # картинки, прикріплені до слова чи рядка (pins.py): як виглядає предмет, монстр…
        h = QHBoxLayout()
        lab = QLabel('✦ Картинки')
        lab.setObjectName('Section')
        h.addWidget(lab)
        self.b_pin = QPushButton('Прикріпити картинку…')
        self.b_pin.clicked.connect(self._pin_add)
        h.addWidget(self.b_pin)
        self.b_pin_hidden = QPushButton('')
        self.b_pin_hidden.clicked.connect(self._pin_toggle_hidden)
        self.b_pin_hidden.hide()
        h.addWidget(self.b_pin_hidden)
        h.addStretch(1)
        lay.addLayout(h)
        from PySide6.QtWidgets import QGridLayout
        self.pins_w = QWidget()
        self.pins_grid = QGridLayout(self.pins_w)
        self.pins_grid.setContentsMargins(0, 0, 0, 0)
        self.pins_grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lay.addWidget(self.pins_w)
        self._pins, self._pin_hidden_mode = None, False
        self.t_pins = QTimer(self, singleShot=True, interval=140, timeout=self._fill_pins)

        # прев'ю шрифтом гри: масштаб («Вписати» — під ширину панелі, інакше сталий, ширше — прокрутка
        # вбік; Ctrl+коліщатко над прев'ю — те саме); кілька екранів діалогу — кожен своїм шрифтом
        h = QHBoxLayout()
        self.pv_title = QLabel('✦ Як у грі')
        self.pv_title.setObjectName('Section')
        h.addWidget(self.pv_title, 1)
        for text, fn in (('−', lambda: self._pv_step(-1)), (None, None), ('+', lambda: self._pv_step(1)),
                         ('Вписати', lambda: self._pv_set('fit'))):
            if text is None:
                self.pv_info = QLabel('')
                self.pv_info.setObjectName('Hint')
                self.pv_info.setMinimumWidth(44)
                self.pv_info.setAlignment(Qt.AlignCenter)
                h.addWidget(self.pv_info)
                continue
            b = QPushButton(text)
            if len(text) == 1:
                b.setObjectName('Small')
                b.setFixedWidth(34)
            b.clicked.connect(fn)
            h.addWidget(b)
        lay.addLayout(h)
        z = (self.settings or {}).get('editor_preview_zoom', 'fit')
        self.pv_zoom = z if z == 'fit' or isinstance(z, (int, float)) else 'fit'
        self._pv_scale_now = 1.0
        self.pv = QLabel('…')
        self.pv.setObjectName('Hint')
        self.pv.setWordWrap(True)
        lay.addWidget(self.pv)
        self.pv_scroll = QScrollArea()
        self.pv_scroll.setWidgetResizable(False)
        self.pv_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pv_scroll.setFrameShape(QFrame.NoFrame)
        self.pv_pics = QWidget()
        self.pv_lay = QVBoxLayout(self.pv_pics)
        self.pv_lay.setContentsMargins(0, 0, 0, 0)
        self.pv_lay.setSpacing(4)
        self.pv_scroll.setWidget(self.pv_pics)
        self.pv_scroll.viewport().installEventFilter(self)
        self.pv_scroll.hide()
        lay.addWidget(self.pv_scroll)
        # межі вікон діалогу, які перекладач поправив сам (у грі обрізає раніше/пізніше)
        self.lim_w = QWidget()
        self.lim_lay = QVBoxLayout(self.lim_w)
        self.lim_lay.setContentsMargins(0, 4, 0, 0)
        self.lim_lay.setSpacing(2)
        self.lim_w.hide()
        lay.addWidget(self.lim_w)
        self.lim_spins = {}
        self._build_rowlim(lay)
        self.t_widths = QTimer(self, singleShot=True, interval=600, timeout=self._save_widths)
        self.t_rowlims = QTimer(self, singleShot=True, interval=600, timeout=self._save_rowlims)
        lay.addStretch(1)

    # ------------------------------------------------------------ персонажі
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        QTimer.singleShot(0, self._place_overlays)
        if getattr(self, 'pv_zoom', None) == 'fit':     # «Вписати» — під нову ширину панелі
            self.t_preview.start(200)

    def showEvent(self, ev):
        super().showEvent(ev)
        QTimer.singleShot(0, self._place_overlays)

    def _place_overlays(self):
        for o in self.overlays:
            if o.corner == 'left':           # унизу лівої панелі, під кнопкою «Нова група…»
                b = self.b_group.mapTo(self.back, self.b_group.rect().bottomLeft())
                o.place(b.x() - 4, b.y() + 6)
            else:                            # угорі праворуч, над шапкою панелі рядка
                p = self.right_panel.mapTo(self.back, self.right_panel.rect().topRight())
                o.place(p.x() - o.width() + 6, p.y() - 14)
        if self.overlays:
            # текст шапки не ховається під персонажем угорі праворуч
            r = next((o for o in self.overlays if o.corner == 'right'), None)
            if r:
                self.head.setContentsMargins(0, 0, r.width() - 20, 0)
                self.where.setContentsMargins(0, 0, r.width() - 20, 0)

    # ------------------------------------------------------------ фон
    def _load_bg(self):
        try:
            docs = [d for _p, d in self.pr.docs.values()]
            ctx = sheets.limits(docs, self.bk, os.path.dirname(os.path.abspath(self.pr.work)),
                                sheets.load_widths(self.pr.xl), sheets.load_row_limits(self.pr.xl))
            self.bridge.got.emit('ctx', ctx)
        except Exception as ex:                                   # noqa: BLE001
            self.bridge.got.emit('status', f'Межі ширини не пораховано: {ex}')
        try:
            import preview
            fonts = {'msg': preview.game_font(self.game, self.bk)}
            if self.game == 'nep':
                fonts['adv'] = preview.game_font(self.game, self.bk, 'adv')
            self.bridge.got.emit('font', fonts)
        except Exception as ex:                                   # noqa: BLE001
            self.bridge.got.emit('status', f'Прев\'ю недоступне: {ex}')

    def _got(self, what, val):
        if what == 'ctx':
            self.ctx = val
            self._build_limits()
            self._checks()
            self.t_preview.start(0)
        elif what == 'font':
            self.fonts = val
            self.t_preview.start(0)
        elif what == 'status':
            self.status.setText(val)
        elif what == 'call':                     # дія в головному потоці (з фонового)
            val()
        elif what == 'excel_out':
            self.status.setText(f'Книги вивантажено: {val}.')
        elif what == 'excel_in':
            self._fill_nodes()
            self._refill()
            self.status.setText(f'З Excel узято змін: {val}.')
        elif what == 'error':
            QMessageBox.warning(self, 'Редактор перекладу', str(val))

    # ------------------------------------------------------------ дерево
    def _pc(self, rows):
        rows = [r for r in rows if self.pr.counted(r)]
        if not rows:
            return ''
        d = sum(1 for r in rows if r['e'].get('tr'))
        return '✓' if d == len(rows) else f'{100 * d // len(rows)}%'

    def _fill_nodes(self):
        t = self.tree
        t.blockSignals(True)
        t.clear()
        books = self.pr.books()
        all_rows = [r for sc in books.values() for rs in sc.values() for r in rs]

        def add(parent, text, node, rows=None):
            it = QTreeWidgetItem(parent, [text, self._pc(rows) if rows is not None else ''])
            it.setData(0, Qt.UserRole, node)
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            return it

        first = add(t, 'Усі рядки', ('all', None), all_rows)
        add(t, 'Повтори', ('dups', None), self.pr.dup_rows(all_rows))
        mine = add(t, 'Мої групи', ('mine', None))
        for g in self.pr.groups():
            add(mine, g['назва'], ('group', g['назва']), self.pr.group_rows(g['назва']))
        files = add(t, 'За файлами гри', ('files', None))
        for book, scenes in books.items():
            bi = add(files, book, ('book', book), [r for rs in scenes.values() for r in rs])
            if len(scenes) > 1:
                for scene, rs in scenes.items():
                    add(bi, scene or '—', ('scene', (book, scene)), rs)
        mine.setExpanded(True)
        files.setExpanded(True)
        # той самий вузол, що був (після перейменування групи тощо), інакше «Усі рядки»
        cur, it = first, QTreeWidgetItemIterator(t)
        while it.value():
            if it.value().data(0, Qt.UserRole) == self.node:
                cur = it.value()
                break
            it += 1
        if cur is first:
            self.node = ('all', None)
        t.setCurrentItem(cur)
        t.blockSignals(False)

    def _node_selected(self, item, _prev):
        if item is None:
            return
        node = item.data(0, Qt.UserRole)
        if node[0] in ('mine', 'files'):
            return
        self.node = node
        self._refill()

    def _node_rows(self, node):
        kind, val = node
        if kind == 'group':
            return self.pr.group_rows(val)
        books = self.pr.books()
        if kind == 'book':
            return [r for rs in books.get(val, {}).values() for r in rs]
        if kind == 'scene':
            return books.get(val[0], {}).get(val[1], [])
        rows = [r for sc in books.values() for rs in sc.values() for r in rs]
        return self.pr.dup_rows(rows) if kind == 'dups' else rows

    # ------------------------------------------------------------ список
    def _refill(self):
        pr = self.pr
        rows = [r for r in self._node_rows(self.node) if r['kind'] != 'key']
        f = self.flt.currentText()
        if f == 'Неперекладені':
            rows = [r for r in rows if not r['e'].get('tr') and r['e']['src'] and not r.get('лишити')]
        elif f == 'Перекладені':
            rows = [r for r in rows if r['e'].get('tr')]
        elif f == 'Відв\'язані':
            rows = [r for r in rows if r.get('окремо')]
        elif f == 'З приміткою':
            rows = [r for r in rows if r['e'].get('note')]
        elif f == 'Не перекладати':
            rows = [r for r in rows if r.get('лишити')]
        elif f == 'Лише машинний':
            rows = [r for r in rows if not r['e'].get('tr') and pr.mt(r) and not r.get('лишити')]
        elif f == 'Переклад ≠ машинний':
            rows = [r for r in rows if r['e'].get('tr') and pr.mt(r) and r['e']['tr'] != pr.mt(r)]
        elif f == 'Вичитані':
            rows = [r for r in rows if pr.reviewed(r)]
        elif f == 'Не вичитані':
            rows = [r for r in rows if r['e'].get('tr') and not pr.reviewed(r)]
        q = self.search.text().strip().lower()
        if q:
            rows = [r for r in rows if q in r['e']['src'].lower() or q in r['e'].get('tr', '').lower()
                    or q in (r['who'] or '').lower()]
        keep = self.cur
        self._commit()
        self.view = rows
        self.model.set_rows(rows)
        self._count()
        self._hit_info()
        i = self.model.pos.get(keep, 0 if rows else None)
        if i is None:
            self.cur = None
            self._show_row()
        else:
            self.table.selectRow(i)
            self.table.scrollTo(self.model.index(i, 0), QAbstractItemView.PositionAtCenter)

    def _row_selected(self, cur, _prev):
        if not cur.isValid() or cur.row() >= len(self.view):
            return
        k = self.view[cur.row()]['k']
        if k == self.cur:
            return
        self._commit()
        self.cur = k
        self._show_row()

    def selected_keys(self):
        return [self.view[i.row()]['k'] for i in self.table.selectionModel().selectedRows()]

    def step(self, d):
        self._commit()
        i = self.model.pos.get(self.cur)
        if i is None:
            return
        j = max(0, min(len(self.view) - 1, i + d))
        self.table.selectRow(j)
        self.table.scrollTo(self.model.index(j, 0))
        self.tr_edit.setFocus()

    def goto(self, key):
        if key not in self.pr.by_key:
            return
        if key not in self.model.pos:
            self.flt.blockSignals(True)
            self.flt.setCurrentIndex(0)
            self.flt.blockSignals(False)
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self.node = ('all', None)
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
            self.tree.blockSignals(False)
            self._refill()
        i = self.model.pos.get(key)
        if i is not None:
            self.table.selectRow(i)
            self.table.scrollTo(self.model.index(i, 0), QAbstractItemView.PositionAtCenter)

    # ------------------------------------------------------------ рядок
    def _set_text(self, w, s):
        w.blockSignals(True)
        w.setPlainText(s or '')
        w.blockSignals(False)

    def _show_row(self):
        self._loading = True
        try:
            k = self.cur
            if not k:
                for w in (self.src, self.ja, self.tr_edit, self.mt):
                    self._set_text(w, '')
                self.head.setText('')
                self.where.setText('')
                self.note.setText('')
                self.warn.setText('')
                self._text_for = None
                return
            r = self.pr.by_key[k]
            e = r['e']
            kind = {'name': 'ім\'я мовця', 'key': 'службовий ключ — не перекладати'}.get(r['kind'])
            self.head.setText(kind or (self.pr.speaker(r) or r['scene']))
            self.where.setText(f'{r["book"]} · {r["scene"]} · {r["source"]} [{e["id"]}]')
            self._set_text(self.src, e['src'])
            self._decorate(self.src, e['src'], 'en')
            has_ja = bool(e.get('ja'))
            self.ja_lab.setVisible(has_ja)
            self.ja.setVisible(has_ja)
            if has_ja:
                self._set_text(self.ja, e['ja'])
                self._decorate(self.ja, e['ja'], 'ja')
                self.ja_lab.setText(f'✦ Японська · {chars_word(project.ja_count(e["ja"]))} (без пробілів)')
            self._set_text(self.tr_edit, e.get('tr', ''))
            self._text_for = k
            self.note.setText(e.get('note', ''))
            self._levels_state()
            self._link_state()
            self.b_atlas.setVisible(self.pr.docs[r['source']][1].get('format') == 'atlas'
                                    and hasattr(self.app, 'open_pics'))
            self._checks()
            self._fill_terms()
            self._count_tr()
        finally:
            self._loading = False
        self._pics([(None, None, '…')])               # старе прев'ю (іншого рядка) — геть одразу
        QTimer.singleShot(0, self._fit_all)
        self.t_preview.start(80)
        self.t_sim.start()
        self.t_pins.start()
        self._hit_info()

    # ------------------------------------------------------------ теги-посилання словами (з 2.11)
    def _decorate(self, w, text, lang):
        """Теги, що мають слово, — показати словом (жовтим, підкреслено); сам текст рядка не змінюється."""
        from PySide6.QtGui import QTextCharFormat, QTextCursor
        w.ref_spans = []
        spans = self.refs().spans(text, lang)
        if not spans:
            return
        out, i, pos = [], 0, 0
        for a, b, tag, word, tr, where in spans:
            out.append(text[i:a])
            pos += a - i
            w.ref_spans.append((pos, pos + len(word), (tag, word, tr, where)))
            out.append(word)
            pos += len(word)
            i = b
        out.append(text[i:])
        self._set_text(w, ''.join(out))
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self.t['warn']))
        fmt.setFontUnderline(True)
        for a, b, _ref in w.ref_spans:
            c = QTextCursor(w.document())
            c.setPosition(a)
            c.setPosition(b, QTextCursor.KeepAnchor)
            c.mergeCharFormat(fmt)

    def _ref_at(self, w, pos):
        p = w.cursorForPosition(pos).position()
        return next((ref for a, b, ref in getattr(w, 'ref_spans', []) if a <= p < b), None)

    def _tag_at(self, w, pos):
        """Тег під курсором: слово-тег або тег без слова (<ITEM>…) — для «Своє слово…»."""
        ref = self._ref_at(w, pos) if w in (self.src, self.ja) else None
        if ref:
            return ref[0]
        import tagrefs
        c = w.cursorForPosition(pos)
        line, col = c.block().text(), c.positionInBlock()
        return next((m.group(0) for m in tagrefs.TAG.finditer(line) if m.start() <= col < m.end()), None)

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QToolTip
        if obj is self.pv_scroll.viewport() and ev.type() == QEvent.Wheel \
                and ev.modifiers() & Qt.ControlModifier:        # Ctrl+коліщатко — масштаб прев'ю
            self._pv_step(1 if ev.angleDelta().y() > 0 else -1)
            return True
        w = self.src if obj is self.src.viewport() else self.ja if obj is self.ja.viewport() else None
        if w is not None and ev.type() in (QEvent.MouseMove, QEvent.MouseButtonRelease):
            pos = ev.position().toPoint()
            ref = self._ref_at(w, pos)
            if ev.type() == QEvent.MouseMove:
                obj.setCursor(Qt.PointingHandCursor if ref else Qt.IBeamCursor)
                if ref:
                    tag, word, tr, where = ref
                    QToolTip.showText(ev.globalPosition().toPoint(),
                                      f'{tag}\n= {word}' + (f'  →  {tr}' if tr else '')
                                      + f'   ({where})\nклік — скопіювати тег', obj)
                else:
                    QToolTip.hideText()
            elif ref and ev.button() == Qt.LeftButton and not w.textCursor().hasSelection():
                QApplication.clipboard().setText(ref[0])
                self.status.setText(f'Скопійовано тег {ref[0]} — встав у переклад, якщо там має бути саме він.')
        return super().eventFilter(obj, ev)

    def _ref_set(self, tag):
        cur = self.refs().user.get(tag, '')
        word, ok = QInputDialog.getText(self, 'Своє слово для тега',
                                        f'Що показувати замість {tag} в оригіналі й японській?\n'
                                        '(порожньо — як було)', text=cur)
        if not ok:
            return
        try:
            self.refs().set_user(tag, word)
        except OSError as ex:
            self.status.setText(f'Не збережено: {ex}')
            return
        self._commit()                       # переклад з поля — у проєкт, тоді показати рядок наново
        self._show_row()

    # ------------------------------------------------------------ картинки до тексту (з 2.11)
    def pins(self):
        if self._pins is None:
            import pins
            self._pins = pins.Pins(self.pr.xl)
        return self._pins

    def _pin_texts(self, k):
        e, ref = self.pr.by_key[k]['e'], self.refs()
        return [ref.plain(e['src']), e['src'], e.get('ja', ''), ref.plain(e.get('ja', ''), 'ja')]

    def _fill_pins(self):
        g = self.pins_grid
        _clear(g)
        k = self.cur
        if not k:
            self.b_pin_hidden.hide()
            return
        try:
            shown, hidden = self.pins().for_row(k, self._pin_texts(k))
        except Exception as ex:                                   # noqa: BLE001
            lab = QLabel(f'картинки не прочитано: {ex}')
            lab.setObjectName('Hint')
            g.addWidget(lab, 0, 0)
            return
        if hidden:
            self.b_pin_hidden.setText('Сховати приховані' if self._pin_hidden_mode
                                      else f'Показати приховані ({len(hidden)})')
            self.b_pin_hidden.show()
        else:
            self.b_pin_hidden.hide()
            self._pin_hidden_mode = False
        items = [(x, False) for x in shown] + ([(x, True) for x in hidden] if self._pin_hidden_mode else [])
        if not items:
            lab = QLabel('—')
            lab.setObjectName('Hint')
            g.addWidget(lab, 0, 0)
            return
        from PIL import Image
        for i, (x, is_hidden) in enumerate(items):
            cell = QWidget()
            v = QVBoxLayout(cell)
            v.setContentsMargins(0, 0, 8, 4)
            pic = _PicLabel(lambda x=x: self._pin_view(x), lambda pos, x=x, w=None: self._pin_menu(x))
            try:
                im = Image.open(self.pins().file(x))
                im.thumbnail((150, 110))
                pic.setPixmap(qtheme.pixmap(im))
                pic.setCursor(Qt.PointingHandCursor)
            except (OSError, AttributeError, TypeError):
                pic.setText('(файл зник)')
                pic.setObjectName('Hint')
            v.addWidget(pic)
            cap = QHBoxLayout()
            t = QLabel(x.get('слово') or 'цей рядок')
            t.setObjectName('Hint')
            cap.addWidget(t, 1)
            b = QPushButton('Повернути' if is_hidden else '×')
            b.setObjectName('Small')         # відступи — у таблиці стилів (свій setStyleSheet ламає висоту)
            b.clicked.connect(lambda _c=False, x=x, on=not is_hidden: self._pin_hide(x, on))
            cap.addWidget(b)
            v.addLayout(cap)
            g.addWidget(cell, i // 3, i % 3)

    def _pin_toggle_hidden(self):
        self._pin_hidden_mode = not self._pin_hidden_mode
        self._fill_pins()

    def _pin_hide(self, x, on):
        """× — не показувати цю картинку в цьому рядку (і лише в ньому)."""
        self.pins().hide(x['id'], self.cur, on)
        self.status.setText('Картинку сховано в цьому рядку («Показати приховані» — повернути).' if on
                            else 'Картинку повернуто в цей рядок.')
        self._fill_pins()

    def _pin_view(self, x):
        p = self.pins().file(x)
        if p:
            from qtui import imageview
            self._pin_viewer = imageview.ImageView(self, p, title=x.get('слово') or 'Картинка', colors=self.t)
            self._pin_viewer.show()

    def _pin_menu(self, x):
        from PySide6.QtGui import QCursor
        m = QMenu(self)
        m.addAction('Відкрити більшою', lambda: self._pin_view(x))
        m.addAction('Змінити слово…', lambda: self._pin_word(x))
        m.addSeparator()
        m.addAction('Видалити картинку', lambda: self._pin_remove(x))
        m.exec(QCursor.pos())

    def _ask_pin_word(self, initial):
        word, ok = QInputDialog.getText(
            self, 'Картинка до слова',
            'Показувати в усіх рядках, де є слово чи фраза (англійською, японською чи словом тега):\n'
            '(порожньо — лише в цьому рядку)', text=initial)
        return word if ok else None

    def _pin_word(self, x):
        word = self._ask_pin_word(x.get('слово', ''))
        if word is not None:
            self.pins().set_word(x['id'], word, self.cur)
            self._fill_pins()

    def _pin_remove(self, x):
        if QMessageBox.question(self, 'Видалити картинку', 'Видалити картинку з усіх рядків?') == QMessageBox.Yes:
            self.pins().remove(x['id'])
            self._fill_pins()

    def _pin_add(self):
        if not self.cur:
            return
        m = QMenu(self)
        m.addAction('З файлу…', lambda: self._pin_add_from(False))
        m.addAction('З буфера обміну (Win+Shift+S, скопійована картинка)', lambda: self._pin_add_from(True))
        m.exec(self.b_pin.mapToGlobal(self.b_pin.rect().bottomLeft()))

    def _pin_add_from(self, clipboard):
        from PySide6.QtWidgets import QFileDialog
        if clipboard:
            from PIL import ImageGrab              # буфер обміну, не знімок екрана
            got = ImageGrab.grabclipboard()
            if isinstance(got, list):
                got = next((p for p in got if p.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp'))),
                           None)
            if got is None:
                QMessageBox.information(self, 'Картинка', 'У буфері обміну немає картинки.')
                return
        else:
            got, _f = QFileDialog.getOpenFileName(self, 'Картинка до тексту', '',
                                                  'Картинки (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;Усі файли (*.*)')
            if not got:
                return
        sel = self.src.textCursor().selectedText().replace('\u2029', '\n').strip()
        word = self._ask_pin_word(sel.split('\n')[0])
        if word is None:
            return
        try:
            self.pins().add(got, word, self.cur)
        except Exception as ex:                                   # noqa: BLE001
            QMessageBox.critical(self, 'Картинка', f'Не вдалося додати: {ex}')
            return
        self.status.setText('Картинку прикріплено' + (f' до «{word}» — покажеться в усіх рядках з цим словом.'
                                                      if word.strip() else ' до цього рядка.'))
        self._fill_pins()

    # ------------------------------------------------------------ пошук ▲▼ (з 2.11)
    def _hit(self, d):
        """До наступного (d=1) / попереднього (-1) рядка зі збігом пошуку — по колу; фокус
        лишається, де був (Enter у пошуку — ще раз далі)."""
        if not self.view:
            return
        self._commit()
        i = self.model.pos.get(self.cur)
        j = ((-1 if d > 0 else 0) if i is None else i) + d
        j %= len(self.view)
        self.table.selectRow(j)
        self.table.scrollTo(self.model.index(j, 0))

    def _hit_info(self):
        """«3 / 232» біля пошуку і підсвітка знайденого в полях рядка."""
        from PySide6.QtGui import QTextCharFormat, QTextDocument
        from PySide6.QtWidgets import QTextEdit
        q = self.search.text().strip()
        if not q or not self.view:
            self.hit_info.setText('')
        else:
            i = self.model.pos.get(self.cur)
            self.hit_info.setText(f'{"–" if i is None else i + 1} / {len(self.view)}')
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(self.t['warn']))
        fmt.setForeground(QColor('#000000'))
        for w in (self.src, self.ja, self.tr_edit):
            sels = []
            if q:
                doc, c = w.document(), None
                c = doc.find(q, 0)
                while not c.isNull():
                    s = QTextEdit.ExtraSelection()
                    s.cursor, s.format = c, fmt
                    sels.append(s)
                    c = doc.find(q, c)
            w.setExtraSelections(sels)

    # ------------------------------------------------------------ схожі перекладені (з 2.11)
    def _fill_similar(self):
        _clear(self.sim_box)
        k = self.cur
        sims = self.pr.similar(k) if k and self.pr.by_key[k]['kind'] != 'key' else []
        self.sim_lab.setVisible(bool(sims))
        self.sim_w.setVisible(bool(sims))
        import html
        link = self.t.get('link', self.t['accent'])
        for ratio, x in sims:
            e = self.pr.by_key[x]['e']
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            pc = QLabel(f'{round(ratio * 100)}%')
            pc.setObjectName('Hint')
            pc.setFixedWidth(40)
            pc.setAlignment(Qt.AlignTop)
            h.addWidget(pc)
            col = QVBoxLayout()
            src = QLabel(one_line(e['src']))
            src.setObjectName('Hint')
            src.setWordWrap(True)
            col.addWidget(src)
            tr = QLabel(f'<a href="take" style="color:{link}; text-decoration:none;">'
                        f'{html.escape(one_line(e["tr"]))}</a>')
            tr.setWordWrap(True)
            tr.setCursor(Qt.PointingHandCursor)
            tr.linkActivated.connect(lambda _l, t=e['tr']: self._take_similar(t))
            col.addWidget(tr)
            h.addLayout(col, 1)
            self.sim_box.addWidget(row)

    def _take_similar(self, text):
        """Переклад схожого рядка — у поле (замість того, що там є; Ctrl+Z — повернути)."""
        self.tr_edit.selectAll()
        self.tr_edit.insertPlainText(text)
        self.tr_edit.setFocus()
        self.status.setText('Взято переклад схожого рядка — виправ різницю (Ctrl+Z — повернути як було).')

    def _fit_all(self):
        _fit(self.src, 2, 12)
        if self.ja.isVisible():
            _fit(self.ja, 1, 14)
        _fit(self.tr_edit, 3, 12)
        if self.mt.isVisible():
            _fit(self.mt, 1, 8)

    def _count_tr(self):
        n = sum(1 for ch in self.tr_edit.toPlainText() if not ch.isspace())
        self.tr_count.setText(f'· {chars_word(n)} без пробілів' if n else '')

    def _levels_state(self):
        r = self.pr.by_key.get(self.cur) if self.cur else None
        self.rev.setChecked(bool(r) and self.pr.reviewed(r))
        self.rev.setEnabled(bool(r) and bool(r['e'].get('tr')))
        m = (r or {}).get('mt') or {}
        mt = self.pr.mt(r) if r else ''
        if mt:
            self.mt_head.setText(f'✦ Машинний · {m.get("рушій", "?")} · {m.get("час", "")}')
        elif m:
            self.mt_head.setText(f'✦ Машинний застарів: оригінал змінився ({m.get("рушій", "?")})')
        else:
            self.mt_head.setText('✦ Машинний — ще немає')
        self.b_take.setEnabled(bool(mt))
        self.b_mt_clear.setEnabled(bool(m))
        self._set_text(self.mt, m.get('t', ''))
        self.mt.setVisible(bool(m))

    def _link_state(self):
        k = self.cur
        twins = len(self.pr.twins(k))
        if twins <= 1:
            self.link_info.setText('Цей рядок у грі один.')
            self.b_link.hide()
            return
        self.b_link.show()
        if self.pr.by_key[k].get('окремо'):
            self.link_info.setText(f'Відв\'язано: такий самий оригінал ще в {twins - 1} місцях, а тут — свій переклад.')
            self.b_link.setText('Прив\'язати до однакових')
        else:
            self.link_info.setText(f'Такий самий оригінал ще в {twins - 1} місцях — переклад спільний '
                                   f'({len(self.pr.linked(k))} пов\'язаних).')
            self.b_link.setText('Відв\'язати тут')

    def _checks(self):
        r = self.pr.by_key.get(self.cur) if self.cur else None
        if r is None or self.ctx is None or not r['e'].get('tr'):
            self.warn.setText('')
            return
        doc = self.pr.docs[r['source']][1]
        msgs = [m for m in sheets.check_entry(doc, r['e'], self.ctx, self.terms)
                if self.approved.get(sheets.approval_key(r['source'], r['e']['id'], m)) != r['e'].get('tr')]
        self.warn.setText('\n'.join('⚠ ' + m for m in msgs))

    def _fill_terms(self):
        _clear(self.terms_box)
        r = self.pr.by_key.get(self.cur)
        # теги-змінні — словами, щоб терміни знаходились і в рядках з <CHARA=…>
        src = self.refs().plain(r['e']['src']) if r else ''
        found = [t for t in glossary.found(src, self.terms) if t.get('ua')] if r else []
        if not found:
            lab = QLabel('—')
            lab.setObjectName('Hint')
            self.terms_box.addWidget(lab)
            return
        for t in found[:12]:
            b = QPushButton(f"{t['en']} → {t['ua']}")
            b.setObjectName('Term')
            b.clicked.connect(lambda _c=False, s=t['ua']: (self.tr_edit.insertPlainText(s), self.tr_edit.setFocus()))
            self.terms_box.addWidget(b)

    # ------------------------------------------------------------ правка
    def _modified(self):
        if self._loading:
            return
        self.t_commit.start()
        self.t_preview.start(300)
        self._count_tr()
        _fit(self.tr_edit, 3, 12)

    def _commit(self):
        self.t_commit.stop()
        k = self.cur
        if not k or self._loading or self._text_for != k:
            return
        text = self.tr_edit.toPlainText()
        if text.strip() == self.pr.tr(k):
            return
        changed = self.pr.set_tr(k, text)
        if changed:
            self.model.changed(changed)
            n = len(changed)
            self.status.setText(f'Переклад записано{f" (разом з однаковими: {n})" if n > 1 else ""} — зберігаю…')
            self._checks()
            self._levels_state()
            self.t_save.start()

    def _note_changed(self, text):
        if self.cur and self.pr.set_note(self.cur, text):
            self.t_save.start()

    def _save(self):
        try:
            self.pr.save()
        except OSError as ex:
            self.status.setText(f'Не вдалося зберегти: {ex}')
            return
        self.status.setText('Збережено.')
        self._count()

    def flush(self):
        self._commit()
        self.t_save.stop()
        if self.pr.pending():
            self._save()

    def closeEvent(self, ev):
        if self.watch_win is not None:
            self.watch_win.close()
        self.flush()
        super().closeEvent(ev)

    def _watch_window(self):
        """«Гра на екрані» (qtui/screenwatch_window.py): що зараз у грі — рядок у редакторі."""
        w = self.watch_win
        if w is not None and w.isVisible():
            w.lift()
            return
        import importlib, sys
        for name in ('screenwatch', 'qtui.screenwatch_window'):
            if name in sys.modules:
                importlib.reload(sys.modules[name])
        from qtui import screenwatch_window
        self.watch_win = screenwatch_window.WatchWindow(self.app, self)

    # ------------------------------------------------------------ Crowdin (Neptunia, з 1.12)
    def _crowdin_ns(self):
        """Параметри для translate_nep (як у кроках «1»/«2» головного вікна)."""
        try:
            gd = self.app.root_dir()
        except RuntimeError as ex:
            QMessageBox.warning(self, 'Crowdin', str(ex))
            return None
        ns = type('a', (), {})()
        ns.game_dir, ns.work_dir, ns.orig_dir = gd, self.pr.work, self.bk
        return ns

    def _from_crowdin(self, as_zip):
        from PySide6.QtWidgets import QFileDialog
        ns = self._crowdin_ns()
        if ns is None:
            return
        if as_zip:
            path, _f = QFileDialog.getOpenFileName(self, 'Архів перекладу з Crowdin', '',
                                                   'Архів Crowdin (*.zip);;Усі файли (*.*)')
        else:
            path = QFileDialog.getExistingDirectory(self, 'Тека з перекладом з Crowdin (у ній SYSTEM00000, GAME00000…)')
        if not path:
            return
        self.flush()
        self.status.setText('Читаю переклад з Crowdin…')

        def job():
            try:
                import translate_nep
                rows, rep = translate_nep.crowdin_read(ns, path)
                st = self.pr.import_rows(rows, dry=True)
                self.bridge.got.emit('call', lambda: self._crowdin_ask(rows, rep, st))
            except Exception as ex:                               # noqa: BLE001
                self.bridge.got.emit('status', f'Не вдалося прочитати переклад з Crowdin: {ex}')
        threading.Thread(target=job, daemon=True).start()

    def _crowdin_ask(self, rows, rep, st):
        log = []
        __import__('translate_nep').crowdin_report(rep, log.append)
        self.app.say('Переклад з Crowdin:\n' + '\n'.join(log), 'dim')
        if not rep['файлів']:
            QMessageBox.warning(self, 'Crowdin', 'Не знайдено жодного файлу Crowdin (SYSTEM00000, GAME00000, '
                                'DLC…). Подробиці — у журналі головного вікна.')
            self.status.setText('З Crowdin нічого не взято.')
            return
        msg = (f'Файлів: {rep["файлів"]}.\n'
               f'Нових перекладів (у програмі порожньо): {st["нових"]}\n'
               f'Уже такі самі: {st["уже так"]}\n'
               f'У програмі перекладено інакше: {st["інакше"]}\n\n')
        if st['інакше']:
            ans = QMessageBox.question(self, 'Забрати переклад з Crowdin',
                                       msg + f'Замінити ці {st["інакше"]} рядків перекладом з Crowdin?\n\n'
                                       '«Так» — узяти з Crowdin; «Ні» — лише заповнити порожні.',
                                       QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if ans == QMessageBox.Cancel:
                self.status.setText('Скасовано.')
                return
            replace = ans == QMessageBox.Yes
        elif st['нових']:
            if QMessageBox.question(self, 'Забрати переклад з Crowdin', msg + 'Заповнити?') != QMessageBox.Yes:
                self.status.setText('Скасовано.')
                return
            replace = False
        else:
            QMessageBox.information(self, 'Crowdin', msg + 'Нового нічого — усе вже є в програмі.')
            self.status.setText('З Crowdin нового немає.')
            return
        got = self.pr.import_rows(rows, replace=replace)
        self._save()
        self._fill_nodes()
        self._refill()
        self._show_row()
        self.status.setText(f'З Crowdin: нових {got["нових"]}, замінено {got["замінено"]}.')

    def _to_crowdin(self):
        from PySide6.QtWidgets import QFileDialog
        ns = self._crowdin_ns()
        if ns is None:
            return
        path, _f = QFileDialog.getSaveFileName(self, 'Зберегти переклад для Crowdin', 'Переклад для Crowdin.zip',
                                               'Архів (*.zip)')
        if not path:
            return
        self.flush()
        self.status.setText('Вивантажую переклад для Crowdin…')
        pr = self.pr

        def job():
            try:
                import translate_nep
                n, t = translate_nep.crowdin_write(ns, path, lambda s, i: pr.tr(f'{s}\t{i}')
                                                   if f'{s}\t{i}' in pr.by_key else '')
                msg = (f'Для Crowdin: файлів {n}, перекладених рядків {t} → {path}. '
                       'У Crowdin: Translations → Upload translations.')
            except Exception as ex:                               # noqa: BLE001
                msg = f'Не вдалося вивантажити для Crowdin: {ex}'
            self.bridge.got.emit('status', msg)
        threading.Thread(target=job, daemon=True).start()

    def _open_atlas(self):
        if self.cur and self.app is not None:
            self.flush()                     # вікно написів читає переклад із проєкту
            self.app.open_pics(goto=self.cur)

    def reload_terms(self):
        """Глосарій змінили у вікні «Терміни» — перечитати й показати терміни рядка заново."""
        self.terms = glossary.load(self.pr.xl)
        self._fill_terms()
        self._checks()

    # те, що головне вікно кличе в редактора (назви — як у Tk-редактора)
    def winfo_exists(self):
        return self.isVisible()

    def lift(self):
        self.raise_()
        self.activateWindow()

    def reload(self, pr):
        """Після «1»: новий проєкт (нові рядки з гри)."""
        self._commit()
        self.pr = self.model.pr = pr
        self._refs = None
        keep = self.cur
        self._fill_nodes()
        self._refill()
        if keep in pr.by_key:
            self.goto(keep)

    # ------------------------------------------------------------ рівні
    def _take_mt(self):
        r = self.pr.by_key.get(self.cur) if self.cur else None
        t = self.pr.mt(r) if r else ''
        if t:
            self.tr_edit.selectAll()
            self.tr_edit.insertPlainText(t)          # так Ctrl+Z повертає як було
            self.tr_edit.setFocus()
            self.status.setText('Взято машинний переклад — вичитай і виправ (Ctrl+Z — повернути як було).')

    def _clear_mt(self, keys):
        changed = []
        for k in keys:
            changed += self.pr.set_mt(k, '', '')
        self._after(changed, f'Машинний переклад прибрано: {len(changed)} рядків.')

    def _set_reviewed(self, on, keys=None):
        self._commit()
        keys = keys or ([self.cur] if self.cur else [])
        changed = self.pr.set_reviewed(keys, on)
        self._after(changed, f'{"Вичитано" if on else "Знято «вичитано»"}: {len(changed)} рядків.')

    def _after(self, changed, msg):
        self.model.changed(changed)
        self._levels_state()
        self._count()
        self.status.setText(msg)
        if changed:
            self.t_save.start()

    def _toggle_link(self):
        k = self.cur
        if not k:
            return
        self._commit()
        if self.pr.by_key[k].get('окремо'):
            changed = self.pr.attach([k])
        else:
            self.pr.detach([k])
            changed = [k]
        self.model.changed(self.pr.twins(k))
        self._show_row()
        self.t_save.start()
        return changed

    # ------------------------------------------------------------ меню списку
    def _list_menu(self, pos):
        keys = self.selected_keys()
        if not keys:
            return
        m = QMenu(self)
        add = m.addMenu(f'Додати в групу ({len(keys)})')
        for g in self.pr.groups():
            add.addAction(g['назва'], lambda n=g['назва']: self._to_group(n))
        add.addSeparator()
        add.addAction('Нова група…', lambda: self._to_group(None))
        if self.node[0] == 'group':
            name = self.node[1]
            m.addAction(f'Прибрати з групи «{name}»', lambda: self._from_group(name, keys))
        m.addSeparator()
        rs = [self.pr.by_key[k] for k in keys]
        if all(r.get('лишити') for r in rs):
            m.addAction('Повернути до перекладу', lambda: self._bulk(lambda: self.pr.set_keep(keys, False), 'Повернуто'))
        else:
            m.addAction('Не перекладати (лишити оригінал)',
                        lambda: self._bulk(lambda: self.pr.set_keep(keys, True), 'Не перекладати'))
        m.addSeparator()
        m.addAction(f'Машинний переклад вибраних ({len(keys)})…', lambda: self._mt_window(keys))
        n_mt = sum(1 for r in rs if not r['e'].get('tr') and self.pr.mt(r))
        if n_mt:
            m.addAction(f'Взяти машинний у переклад ({n_mt})', lambda: self._bulk(
                lambda: [x for k in keys if not self.pr.tr(k) for x in self.pr.take_mt(k)],
                'Машинний узято в переклад (вичитай їх)'))
        if any(r['e'].get('tr') for r in rs):
            if all(self.pr.reviewed(r) for r in rs if r['e'].get('tr')):
                m.addAction('Зняти «вичитано»', lambda: self._set_reviewed(False, keys))
            else:
                m.addAction('Позначити вичитаним  (F8)', lambda: self._set_reviewed(True, keys))
        m.addSeparator()
        m.addAction('Скопіювати оригінал у переклад', lambda: self._bulk(
            lambda: [x for k in keys for x in self.pr.set_tr(k, self.pr.by_key[k]['e']['src'])], 'Скопійовано'))
        m.addAction('Очистити переклад', lambda: self._bulk(
            lambda: [x for k in keys for x in self.pr.set_tr(k, '')], 'Очищено'))
        m.addAction('Замінити всюди…  (Ctrl+H)', lambda: self._replace_all())
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _field_menu(self, w, pos):
        """Права кнопка в полі оригіналу/перекладу: звичайне меню Qt + «Замінити всюди…»."""
        m = w.createStandardContextMenu()
        m.addSeparator()
        if w is not self.ja:
            m.addAction('Замінити всюди…  (Ctrl+H)', lambda: self._replace_all(w))
        tag = self._tag_at(w, pos)
        if tag:
            m.addAction(f'Своє слово для {tag}…', lambda: self._ref_set(tag))
        m.exec(w.viewport().mapToGlobal(pos))
        m.deleteLater()

    def _bulk(self, fn, what):
        self._commit()
        changed = fn() or []
        tw = set(changed)
        for k in list(tw):
            tw.update(self.pr.twins(k))
        self.model.changed(tw)
        self._show_row()
        self._save()
        self.status.setText(f'{what}: {len(set(changed))} рядків.')

    def _tree_menu(self, pos):
        it = self.tree.itemAt(pos)
        kind, val = it.data(0, Qt.UserRole) if it is not None else ('', None)
        m = QMenu(self)
        if kind == 'group':
            m.addAction('Перейменувати…', lambda: self._rename_group(val))
            m.addAction('Видалити групу', lambda: self._delete_group(val))
        elif kind == 'mine':
            m.addAction('Нова група з виділених…', lambda: self._to_group(None))
        else:
            return
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _rename_group(self, old):
        new, ok = QInputDialog.getText(self, 'Перейменувати групу', 'Нова назва:', text=old)
        new = new.strip()
        if not ok or not new or new == old:
            return
        if self.pr.group(new):
            QMessageBox.warning(self, 'Групи', 'Група з такою назвою вже є.')
            return
        self.pr.rename_group(old, new)
        if self.node == ('group', old):
            self.node = ('group', new)
        self._save()
        self._fill_nodes()

    def _delete_group(self, name):
        if QMessageBox.question(self, 'Видалити групу', f'Видалити групу «{name}»?\nРядки й переклад лишаться — '
                                'зникне лише сама група.') != QMessageBox.Yes:
            return
        self.pr.delete_group(name)
        self._save()
        self.node = ('all', None)
        self._fill_nodes()
        self._refill()

    def _from_group(self, name, keys):
        self.pr.remove_from_group(name, keys)
        self._save()
        self._fill_nodes()
        self._refill()

    def _to_group(self, name):
        keys = self.selected_keys()
        if not keys:
            return
        if name is None:
            name, ok = QInputDialog.getText(self, 'Нова група', 'Назва групи:')
            if not ok or not name.strip():
                return
            name = name.strip()
        self.pr.add_to_group(name, keys)
        self._save()
        self._fill_nodes()
        self.status.setText(f'У групі «{name}» — додано {len(keys)} рядків.')

    # ------------------------------------------------------------ вікна: машинний, «Замінити всюди»
    def _replace_all(self, field=None):
        """«Замінити всюди» (qtui/replace_window): фрагмент — виділене в полі оригіналу/перекладу."""
        from qtui import replace_window
        find = ''
        for w in ([field] if field else []) + [self.src, self.tr_edit]:
            # Qt віддає переноси виділеного як U+2029
            find = w.textCursor().selectedText().replace('\u2029', '\n').strip()
            if find:
                break
        old = self._replace_win
        if old is not None:
            try:
                old.close()
            except RuntimeError:                 # уже видалене (WA_DeleteOnClose)
                pass
        self._replace_win = replace_window.ReplaceWindow(self, find.split('\n')[0])
        self._replace_win.show()

    def _mt_window(self, keys=None):
        """Машинний переклад (qtui/mt_window): чернетки сервісу — у поле «Машинний» рядків."""
        from qtui import mt_window
        self._commit()
        old = self._mt_win
        if old is not None and old.isVisible():
            old.raise_()
            old.activateWindow()
            return
        self._mt_win = mt_window.MTWindow(self, keys)
        self._mt_win.show()

    def _after_bulk(self, keys, what):
        """Після масової правки (заміна, скасування): рядки й однакові до них — у списку, зберегти."""
        tw = set(keys)
        for k in list(tw):
            tw.update(self.pr.twins(k))
        self.model.changed(tw)
        self._show_row()
        self._save()
        self.status.setText(f'{what}: {len(set(keys))} рядків.')

    def _mt_changed(self, keys):
        """Прийшов машинний переклад рядків (mt_window): оновити список, поле рядка, зберегти згодом."""
        self.model.changed(keys)
        if self.cur in keys:
            self._levels_state()
        self.t_save.start()

    def _refresh_pcs(self):
        """Лише відсотки в дереві (після правок) — без перебудови."""
        books = self.pr.books()
        all_rows = [r for sc in books.values() for rs in sc.values() for r in rs]
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            kind, val = item.data(0, Qt.UserRole)
            rows = None
            if kind == 'all':
                rows = all_rows
            elif kind == 'dups':
                rows = self.pr.dup_rows(all_rows)
            elif kind == 'group':
                rows = self.pr.group_rows(val)
            elif kind == 'book':
                rows = [r for rs in books.get(val, {}).values() for r in rs]
            elif kind == 'scene':
                rows = books.get(val[0], {}).get(val[1], [])
            if rows is not None:
                item.setText(1, self._pc(rows))
            it += 1

    def node_title(self):
        """Назва вибраного розділу дерева (для вікон: «вибраний розділ дерева («…»)»)."""
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            if it.value().data(0, Qt.UserRole) == self.node:
                return it.value().text(0)
            it += 1
        return ''

    def game_title(self):
        return self.games.get(self.game, {}).get('title', self.game)

    def refs(self):
        """Теги-посилання гри (tagrefs.Refs) — машинному перекладу: тег-змінна -> слово."""
        if self._refs is None:
            import tagrefs
            self._refs = tagrefs.Refs(self.pr)
        return self._refs

    def _save_setting(self, key, val):
        """Запам'ятати в налаштуваннях програми (якщо їх передали)."""
        if self.settings is None:
            return
        self.settings[key] = val
        if self.save_settings:
            try:
                self.save_settings(self.settings)
            except Exception:                                   # noqa: BLE001
                pass

    # ------------------------------------------------------------ прев'ю
    def _preview(self):
        r = self.pr.by_key.get(self.cur) if self.cur else None
        self.rowlim_w.hide()                     # знову покажемо нижче, якщо є прев'ю шрифтом гри
        if r is None:
            self.lim_w.hide()
            self._pics([(None, None, '')])
            return
        e = r['e']
        if e.get('preview') and os.path.exists(e['preview']):
            from PIL import Image
            self.lim_w.hide()
            self._pics([(None, Image.open(e['preview']), '')])
            return
        if self.fonts is None or self.ctx is None or not self.ctx.get('wtab'):
            self._pics([(None, None, 'прев\'ю готується…' if self.fonts is None or self.ctx is None
                         else 'для цієї гри прев\'ю немає')])
            return
        import preview
        text = self.tr_edit.toPlainText().strip()
        self.pv_title.setText('✦ Як у грі' if text else '✦ Як у грі (поки що оригінал — перекладу ще немає)')
        text = text or e['src']
        doc = self.pr.docs[r['source']][1]
        dialog = sheets._width_group(doc, e) == ('діалог',)
        name = self.pr.speaker(r) if dialog else None
        screens = self.ctx.get('screens') if dialog else None
        self.lim_w.setVisible(bool(screens))
        if not dialog:                           # межа окремого рядка (діалогам — межі вікон)
            self.rowlim_w.show()
        items = []
        for sc in screens or [None]:
            font = self.fonts.get(sc['font'] if sc else 'msg')
            if font is None:
                continue
            lim, lines, _w = sheets.width_limit(doc, e, self.ctx, sc)
            box = None
            if not dialog and self._rowlim_key() not in (self.ctx.get('rowlim') or {}):
                # поле гри, де показується цей текст (Crystar: вікно репліки) — ширина з префаба;
                # гра переносить сама, тож і прев'ю переноситься в цю ширину
                box = next((b for b in self.ctx.get('boxes') or [] if re.search(b['re'], r['source'])), None)
                if box:
                    lim, lines = box['px'], None
            if not dialog:
                self._rowlim_show(lim, lines)
                if box:
                    self.rowlim_info.setText(f'Межа — {box["назва"]} гри ({round(lim)} px, взято з самої гри); '
                                             'гра переносить рядки сама. Своя межа — числа нижче чи клік по прев\'ю.')
            try:
                im, _notes = preview.render(font, text, lim, lines, name, dialog)
            except Exception as ex:                               # noqa: BLE001
                items.append((None, None, f'прев\'ю не вдалося: {ex}'))
                continue
            cap = sc['назва'].capitalize() if sc and len(screens) > 1 else None
            items.append((cap, im, '', sc if dialog else 'row'))
        self._pics(items or [(None, None, '')])

    # масштаб прев'ю (з 2.13)
    PV_STEPS = (0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1.0, 1.25, 1.5, 1.75, 2.0)

    def _pv_k(self, im):
        if self.pv_zoom == 'fit':
            avail = max(100, self.pv_scroll.viewport().width() - 4) if self.pv_scroll.isVisible() \
                else max(100, self.pv.width())
            return min(1.0, avail / im.width)
        return float(self.pv_zoom)

    def _pics(self, items):
        """Показати [(підпис | None, PIL | None, текст[, екран | 'row'])]; клік по картинці —
        межа вікна (екран діалогу) чи межа рядка там, де клікнули."""
        from PIL import Image
        _clear(self.pv_lay)
        pics = [it for it in items if it[1] is not None]
        texts = [it[2] for it in items if it[1] is None and it[2]]
        self.pv.setText('\n'.join(texts))
        self.pv.setVisible(bool(texts) or not pics)
        self.pv_scroll.setVisible(bool(pics))
        if not pics:
            self.pv_info.setText('')
            return
        k = self._pv_k(pics[0][1])
        self._pv_scale_now = k
        self.pv_info.setText(f'{round(k * 100)}%')
        w_max = h_sum = 0
        cap_h = self.fontMetrics().height() + 2
        for cap, im, _text, *scr in pics:
            if cap:
                lab = QLabel(cap)
                lab.setObjectName('Hint')
                self.pv_lay.addWidget(lab)
                h_sum += cap_h + self.pv_lay.spacing()
            small = im if abs(k - 1) < 1e-3 else im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))),
                                                          Image.LANCZOS)
            sc = scr[0] if scr else None
            pic = _ClickPic(lambda x, sc=sc: self._lim_click(sc, k, x))
            pic.setPixmap(qtheme.pixmap(small))
            if sc == 'row' or (sc and sc.get('id') in self.lim_spins):
                pic.setCursor(Qt.CrossCursor)
            pic.setFixedSize(small.width, small.height)
            self.pv_lay.addWidget(pic)
            w_max = max(w_max, small.width)
            h_sum += small.height + self.pv_lay.spacing()
        # розмір рахуємо самі: щойно додані віджети Qt ще не розклав (sizeHint тут — 0)
        self.pv_pics.resize(w_max, h_sum)
        bar = self.pv_scroll.horizontalScrollBar().sizeHint().height() if w_max > self.pv_scroll.viewport().width() else 0
        self.pv_scroll.setFixedHeight(h_sum + bar + 2)

    def _pv_set(self, z):
        self.pv_zoom = z
        self._save_setting('editor_preview_zoom', z)
        self._preview()

    def _pv_step(self, d):
        cur = self._pv_scale_now
        steps = self.PV_STEPS
        nxt = next((s for s in steps if s > cur + 1e-3), steps[-1]) if d > 0 else \
            next((s for s in reversed(steps) if s < cur - 1e-3), steps[0])
        self._pv_set(nxt)

    def _lim_click(self, sc, k, x):
        import preview
        if sc == 'row':
            self.rowlim_px.setValue(max(10, round(x / k - preview.PAD)))
        elif sc and sc.get('id') in self.lim_spins:
            self.lim_spins[sc['id']].setValue(max(50, round(x / k - preview.PAD)))

    # ------------------------------------------------------------ межа вікна діалогу (з 2.2)
    def _build_limits(self):
        """Поля «межа вікна» для кожного екрана діалогу (sheets.limits → screens)."""
        from PySide6.QtWidgets import QSpinBox
        _clear(self.lim_lay)
        self.lim_spins = {}
        screens = (self.ctx or {}).get('screens') or []
        if not screens:
            return
        lab = QLabel('Межа вікна — де гра вже не показує текст (px шрифту гри). '
                     'Клік по прев\'ю ставить межу там, де клікнули.')
        lab.setObjectName('Hint')
        lab.setWordWrap(True)
        self.lim_lay.addWidget(lab)
        for sc in screens:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(QLabel(sc['назва'].capitalize() + ':'))
            sp = QSpinBox()
            sp.setRange(50, 4000)
            sp.setValue(int(round(sc['lim'])))
            self.lim_spins[sc['id']] = sp
            h.addWidget(sp)
            info = QLabel('')
            info.setObjectName('Hint')
            h.addWidget(info)
            b = QPushButton('Як виміряно')
            b.clicked.connect(lambda _c=False, sp=sp, sc=sc: sp.setValue(int(round(sc['lim0']))))
            h.addWidget(b)
            h.addStretch(1)

            def show(sc=sc, info=info, b=b):
                own = round(sc['lim']) != round(sc['lim0'])
                info.setText(f'(виміряно: {round(sc["lim0"])})' if own else '(виміряно в грі)')
                b.setEnabled(own)
            show()
            sp.valueChanged.connect(lambda n, sc=sc, show=show: self._lim_changed(sc, n, show))
            self.lim_lay.addWidget(row)

    def _lim_changed(self, sc, n, show):
        if n < 50 or n == round(sc['lim']):
            return
        sc['lim'] = n
        show()
        self.t_preview.start(150)
        if self.cur:
            self._checks()
        self.t_widths.start()

    def _save_widths(self):
        own = {sc['id']: int(round(sc['lim'])) for sc in (self.ctx or {}).get('screens') or []
               if round(sc['lim']) != round(sc['lim0'])}
        try:
            sheets.save_widths(self.pr.xl, own)
        except OSError as ex:
            self.status.setText(f'Межу не збережено: {ex}')
            return
        self.status.setText('Межу вікна збережено — перевірка перекладу тепер рахує з нею.' if own
                            else 'Межа вікна — як виміряно в грі.')

    # ------------------------------------------------------------ межа окремого рядка (з 2.9)
    def _build_rowlim(self, lay):
        """«Межа для цього рядка»: місце на екрані, якого програма сама не знає (поля інтерфейсу
        Crystar…): ширина в px шрифту гри й скільки рядків уміщає. Пишеться в межі.json -> діє і в
        прев'ю, і в перевірці перекладу."""
        from PySide6.QtWidgets import QSpinBox
        self.rowlim_w = QWidget()
        v = QVBoxLayout(self.rowlim_w)
        v.setContentsMargins(0, 4, 0, 0)
        v.setSpacing(2)
        self.rowlim_info = QLabel('')
        self.rowlim_info.setObjectName('Hint')
        self.rowlim_info.setWordWrap(True)
        v.addWidget(self.rowlim_info)
        h = QHBoxLayout()
        h.addWidget(QLabel('Межа рядка: ширина'))
        self.rowlim_px = QSpinBox()
        self.rowlim_px.setRange(0, 4000)
        h.addWidget(self.rowlim_px)
        h.addWidget(QLabel('px, рядків'))
        self.rowlim_lines = QSpinBox()
        self.rowlim_lines.setRange(0, 20)
        h.addWidget(self.rowlim_lines)
        h.addStretch(1)
        v.addLayout(h)
        h = QHBoxLayout()
        self.b_rowlim_sel = QPushButton('Для вибраних рядків')
        self.b_rowlim_sel.clicked.connect(self._rowlim_to_selected)
        h.addWidget(self.b_rowlim_sel)
        self.b_rowlim_reset = QPushButton('Як за оригіналом')
        self.b_rowlim_reset.clicked.connect(self._rowlim_reset)
        h.addWidget(self.b_rowlim_reset)
        h.addStretch(1)
        v.addLayout(h)
        self._rowlim_lock = False
        for sp in (self.rowlim_px, self.rowlim_lines):
            sp.valueChanged.connect(lambda _n: self._rowlim_changed())
        self.rowlim_w.hide()
        lay.addWidget(self.rowlim_w)

    def _rowlim_key(self):
        r = self.pr.by_key.get(self.cur) if self.cur else None
        return r and f'{r["source"]}\t{r["e"]["id"]}'

    def _rowlim_show(self, lim, lines):
        """Показати межу поточного рядка (своя чи за оригіналом) — без запису."""
        own = (self.ctx.get('rowlim') or {}).get(self._rowlim_key())
        self._rowlim_lock = True
        self.rowlim_px.setValue(int(round(lim)))
        self.rowlim_lines.setValue(int(lines or 0))
        self._rowlim_lock = False
        wrap = ' Гра сама переносить слова за цією шириною.' if self.ctx.get('wrap') else ''
        self.rowlim_info.setText(('Своя межа цього рядка.' if own else
                                  'Межа — оцінка за оригіналом. Якщо в грі текст не влазить чи місця більше, '
                                  'задай свою:') + ' Клік по прев\'ю — ширина там, де клікнули; '
                                 '«рядків» 0 — без обмеження.' + wrap)
        self.b_rowlim_reset.setEnabled(bool(own))
        n = len(self.selected_keys())
        self.b_rowlim_sel.setText(f'Для вибраних рядків ({n})' if n > 1 else 'Для вибраних рядків')
        self.b_rowlim_sel.setEnabled(n > 1)

    def _rowlim_value(self):
        px, lines = self.rowlim_px.value(), self.rowlim_lines.value()
        if px < 10:
            return None
        d = {'px': px}
        if lines > 0:
            d['рядків'] = lines
        return d

    def _rowlim_changed(self):
        if self._rowlim_lock or self.ctx is None:
            return
        k, d = self._rowlim_key(), self._rowlim_value()
        if not k or d is None:
            return
        rl = self.ctx.setdefault('rowlim', {})
        if rl.get(k) == d:
            return
        rl[k] = d
        self._rowlim_saved()

    def _rowlim_saved(self):
        self.t_preview.start(150)
        if self.cur:
            self._checks()
        self.t_rowlims.start()

    def _rowlim_to_selected(self):
        d = self._rowlim_value()
        if d is None or self.ctx is None:
            return
        rl = self.ctx.setdefault('rowlim', {})
        keys = self.selected_keys()
        for k in keys:
            r = self.pr.by_key[k]
            rl[f'{r["source"]}\t{r["e"]["id"]}'] = dict(d)
        self._rowlim_saved()
        self.status.setText(f'Межу рядка ({d["px"]} px' + (f', рядків {d["рядків"]}' if 'рядків' in d else '')
                            + f') задано для {len(keys)} рядків.')

    def _rowlim_reset(self):
        if self.ctx is None:
            return
        (self.ctx.get('rowlim') or {}).pop(self._rowlim_key(), None)
        self._rowlim_saved()

    def _save_rowlims(self):
        try:
            sheets.save_row_limits(self.pr.xl, (self.ctx or {}).get('rowlim') or {})
        except OSError as ex:
            self.status.setText(f'Межу рядка не збережено: {ex}')

    # ------------------------------------------------------------ підрахунок
    def _count(self):
        rows = self.view
        n = lambda v: f'{v:,}'.replace(',', ' ')
        pc = lambda a, b: f' ({100 * a // b}%)' if b else ''
        if self.count_mode == 'hidden':
            h = self.pr.hidden(self._node_rows(self.node))
            parts = [f'{lab}: {n(h[w]["rows"])} рядків, {n(h[w]["words"])} слів'
                     for w, lab in (('keep', '«не перекладати»'), ('key', 'службові ключі'),
                                    ('empty', 'порожній оригінал')) if h[w]['rows']]
            self.count.setText('не рахуються — ' + ('; '.join(parts) if parts else 'немає'))
            return
        st = self.pr.stats(rows)
        p = 'uniq_' if self.count_mode == 'uniq' else ''
        lv = ''.join(f' · {lab}: {n(st[p + key])}' for key, lab in (('reviewed', 'вичитано'), ('mt', 'лише машинний'))
                     if st[p + key])
        if self.count_mode == 'uniq':
            self.count.setText(f'без повторів: рядків {n(st["uniq"])} (з {n(st["rows"])}) · перекладено: '
                               f'{n(st["uniq_done"])} · слів перекладено: {n(st["uniq_words_done"])} з '
                               f'{n(st["uniq_words"])}' + pc(st['uniq_words_done'], st['uniq_words']) + lv)
            return
        done = sum(1 for r in rows if r['e'].get('tr'))
        ja = (f' · японською: {n(st["ja"])} знаків, перекладено {n(st["ja_done"])}' + pc(st['ja_done'], st['ja'])
              if st['ja'] else '')
        self.count.setText(f'рядків: {n(len(rows))} · перекладено: {n(done)} · слів перекладено: '
                           f'{n(st["words_done"])} з {n(st["words"])}' + pc(st['words_done'], st['words']) + lv + ja)

    def _count_next(self):
        modes = list(COUNT_MODES)
        self.count_mode = modes[(modes.index(self.count_mode) + 1) % len(modes)]
        self.b_count.setText(COUNT_MODES[self.count_mode])
        self._count()

    def _set_ontop(self, on):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        self.show()

    # ------------------------------------------------------------ Excel
    def _to_excel(self):
        self.flush()
        self.status.setText('Вивантажую книги Excel…')

        def job():
            try:
                made = self.pr.export_excel()
                self.bridge.got.emit('excel_out', len(made))
            except Exception as ex:                               # noqa: BLE001
                self.bridge.got.emit('error', f'Не вдалося вивантажити: {ex}')
        threading.Thread(target=job, daemon=True).start()

    def _from_excel(self):
        if QMessageBox.question(self, 'Завантажити з Excel',
                                'Узяти з книг Excel те, що в них змінили після вивантаження?') != QMessageBox.Yes:
            return
        self.flush()
        self.status.setText('Читаю книги Excel…')

        def job():
            try:
                self.bridge.got.emit('excel_in', self.pr.load_excel())
            except Exception as ex:                               # noqa: BLE001
                self.bridge.got.emit('error', f'Не вдалося прочитати книги: {ex}')
        threading.Thread(target=job, daemon=True).start()
