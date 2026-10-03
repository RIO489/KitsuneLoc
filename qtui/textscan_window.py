# -*- coding: utf-8 -*-
"""«Знайти написи на картинках» на Qt — те саме, що textscan_window.py (Tk), інший вигляд.

Ліворуч — текстури гри, де можуть бути написи; посередині — вибрана текстура:
зелені рамки — уже розмічене, жовті — знайдене розпізнаванням (textscan.scan),
помаранчева — вибрана. Рамку можна перетягнути, змінити за краї й кути,
намалювати нову мишею по порожньому місці (для того, що розпізнавання пропустило)
або відкинути («Не текст», Delete). Праворуч — англійський текст, стиль і тло з
живим прев'ю; «Прийняти» записує кадр у розмітку перекладача (атлас/*.мої.json),
після «1» напис з'являється в книзі написів і в редакторі.

Тут же — спільне з вікном «Написи на картинках» (qtui/atlas_editor.py): вибір стилю за
групами (StylePicker), «Усі стилі» (StyleGallery) і свій стиль перекладача (StyleEditor).
Логіка — та сама, що в Tk-вікні; дані — textscan.py, pics.py, maryskelter/atlas.py.
"""
import collections, copy, os, subprocess, threading

from PIL import Image
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractScrollArea, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
                               QRadioButton, QScrollArea, QSlider, QSplitter, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

import pics
import textscan
from maryskelter import atlas as atl
from qtui import theme as qtheme

ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.1, 8.0, 1.25
HANDLE = 6                      # «ручка» рамки, пікселі екрана
PAD_AREA = 3                    # область стирання = рамка напису + запас
PAD_FRAME = 12                  # кадр = рамка напису + місце під довший переклад
BG = (24, 16, 32, 255)
CANVAS_BG = '#181020'
MODES = {'рядки': 'кнопка / плашка з однорідним тлом', 'прозорий': 'напис на прозорому'}
# налаштування вигляду прийнятого напису, які переживають відкликання й нову рамку
KEEP = ('правки', 'правки до шрифту', 'літери з оригіналу', 'кегль', 'вирівняти', 'ключ', 'приклад')


class Bridge(QObject):
    """Фоновий потік -> вікно (сигнали Qt потокобезпечні; віджети чіпаємо лише у вікні)."""
    got = Signal(str, object)


def pil_pixmap(im, scale=1.0, bg=BG, resample=Image.LANCZOS):
    """PIL RGBA -> QPixmap на темному тлі, у масштабі `scale` (лише в головному потоці)."""
    b = Image.new('RGBA', im.size, bg)
    b.alpha_composite(im.convert('RGBA'))
    if scale != 1.0:
        b = b.resize((max(1, int(b.width * scale)), max(1, int(b.height * scale))), resample)
    return qtheme.pixmap(b)


def color_button(w=34, h=22):
    b = QPushButton()
    b.setFixedSize(w, h)
    b.setCursor(Qt.PointingHandCursor)
    return b


def paint_button(b, color, text=''):
    """Кнопка-зразок кольору (#rrggbb[aa]); color None — без кольору (як тло вікна)."""
    b.setText(text)
    if color:
        c = QColor(color[:7])
        fg = '#000000' if c.lightness() > 140 else '#ffffff'
        b.setStyleSheet(f'QPushButton {{ background: {color[:7]}; color: {fg}; padding: 0; '
                        f'border: 1px solid #888888; border-radius: 4px; }}')
    else:
        b.setStyleSheet('QPushButton { padding: 0; }')


def float_slider(lo, hi, value, steps=1000):
    """Повзунок з дробовими значеннями (QSlider — цілі): .fget() / .fset(v)."""
    s = QSlider(Qt.Horizontal)
    s.setRange(0, steps)
    s.fget = lambda: lo + (hi - lo) * s.value() / steps
    s.fset = lambda v: s.setValue(round((min(hi, max(lo, float(v))) - lo) / (hi - lo) * steps))
    s.fset(value)
    return s


class StylePicker(QPushButton):
    """Вибір стилю: меню з підменю за групами стилів (atl.style_groups); свої — з ★.
    Як Tk-змінна: set() міняє значення й завжди шле changed (вікна стережуться _loading)."""
    changed = Signal(str)

    def __init__(self, parent=None, width=None):
        super().__init__(parent)
        self.value = ''
        self.acts = {}
        self.m = QMenu(self)
        self.setMenu(self.m)
        self.grp = QActionGroup(self)
        self.grp.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        self.setStyleSheet('QPushButton { text-align: left; }')
        if width:
            self.setMinimumWidth(width)

    def refresh(self, styles, names):
        for a in self.grp.actions():
            self.grp.removeAction(a)
        self.m.clear()
        self.acts = {}
        groups = atl.style_groups(styles, names)
        for group, ns in groups:
            sub = self.m if len(groups) == 1 else self.m.addMenu(f'{group}  ({len(ns)})')
            for n in ns:
                a = sub.addAction(n + ('  ★' if styles[n].get('мій') else ''))
                a.setCheckable(True)
                a.setChecked(n == self.value)
                a.triggered.connect(lambda _c=False, n=n: self.set(n))
                self.grp.addAction(a)
                self.acts[n] = a

    def get(self):
        return self.value

    def set(self, name):
        self.value = name or ''
        self.setText(self.value)
        a = self.acts.get(self.value)
        if a is not None:
            a.setChecked(True)
        elif self.grp.checkedAction() is not None:
            self.grp.checkedAction().setChecked(False)
        self.changed.emit(self.value)


# ============================================================ полотно з текстурою
class Canvas(QAbstractScrollArea):
    """Текстура в масштабі win.z з рамками. Малюється лише видима частина (великі
    текстури ×4 — сотні МБ); шматок кешується, доки не зміниться видиме чи масштаб."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.viewport().setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.ClickFocus)
        self._key, self._part = None, None
        self._pan = None
        self.horizontalScrollBar().setSingleStep(24)
        self.verticalScrollBar().setSingleStep(24)

    def offset(self):
        return self.horizontalScrollBar().value(), self.verticalScrollBar().value()

    def sync(self):
        """Межі прокрутки під текстуру в поточному масштабі."""
        w = self.win
        vw, vh = self.viewport().width(), self.viewport().height()
        W = round(w.img.width * w.z) if w.img is not None else 0
        H = round(w.img.height * w.z) if w.img is not None else 0
        for sb, full, page in ((self.horizontalScrollBar(), W, vw), (self.verticalScrollBar(), H, vh)):
            sb.setRange(0, max(0, full - page))
            sb.setPageStep(max(1, page))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.sync()

    def scrollContentsBy(self, _dx, _dy):
        self.viewport().update()

    def drop_cache(self):
        self._key, self._part = None, None

    def paintEvent(self, _ev):
        w = self.win
        p = QPainter(self.viewport())
        p.fillRect(self.viewport().rect(), QColor(CANVAS_BG))
        if w.img is None or w.flat is None:
            return
        z = w.z
        sx, sy = self.offset()
        vw, vh = self.viewport().width(), self.viewport().height()
        ix0, iy0 = max(0, int(sx / z)), max(0, int(sy / z))
        ix1 = min(w.img.width, int((sx + vw) / z) + 2)
        iy1 = min(w.img.height, int((sy + vh) / z) + 2)
        if ix1 > ix0 and iy1 > iy0:
            key = (ix0, iy0, ix1, iy1, z, id(w.flat))
            if key != self._key:
                part = w.flat.crop((ix0, iy0, ix1, iy1))
                size = (max(1, round((ix1 - ix0) * z)), max(1, round((iy1 - iy0) * z)))
                part = part.resize(size, Image.NEAREST if z >= 1 else Image.BOX)
                self._key, self._part = key, qtheme._qimage(part)
            p.drawImage(QPointF(ix0 * z - sx, iy0 * z - sy), self._part)

        def rect(b):
            return QRectF(b[0] * z - sx, b[1] * z - sy, (b[2] - b[0]) * z, (b[3] - b[1]) * z)
        for k, d in enumerate(w.done):
            sel = k == w.sel_done
            p.setPen(QPen(QColor('#4aa8ff' if sel else '#3ddc84'), 3 if sel else 2))
            p.drawRect(rect(d['рамка']))
        for k, cd in enumerate(w.cands):
            b = cd['рамка']
            col = QColor('#ff8a00' if k == w.sel else '#ffd400')
            p.setPen(QPen(col, 3 if k == w.sel else 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(rect(b))
            if k == w.sel:
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                for hx, hy in w._handles(b):
                    p.drawRect(QRectF(hx * z - sx - HANDLE / 2, hy * z - sy - HANDLE / 2, HANDLE, HANDLE))
                p.setBrush(Qt.NoBrush)

    # --- миша: ліва — рамки (у вікні), права — тягнути картинку, коліщатко — прокрутка/масштаб
    def _pt(self, ev):
        sx, sy = self.offset()
        pos = ev.position()
        return (sx + pos.x()) / self.win.z, (sy + pos.y()) / self.win.z

    def mousePressEvent(self, ev):
        if ev.button() == Qt.RightButton:
            self._pan = (ev.position(), self.offset())
            self.viewport().setCursor(Qt.SizeAllCursor)
        elif ev.button() == Qt.LeftButton:
            self.win._press(*self._pt(ev))

    def mouseMoveEvent(self, ev):
        if self._pan is not None:
            (p0, (sx, sy)), pos = self._pan, ev.position()
            self.horizontalScrollBar().setValue(round(sx - (pos.x() - p0.x())))
            self.verticalScrollBar().setValue(round(sy - (pos.y() - p0.y())))
        elif ev.buttons() & Qt.LeftButton:
            self.win._motion(*self._pt(ev))

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.RightButton:
            self._pan = None
            self.viewport().setCursor(Qt.CrossCursor)
        elif ev.button() == Qt.LeftButton:
            self.win._release()

    def wheelEvent(self, ev):
        d = ev.angleDelta().y()
        if ev.modifiers() & Qt.ControlModifier:
            pos = ev.position()
            self.win._zoom_by(ZOOM_STEP if d > 0 else 1 / ZOOM_STEP, (pos.x(), pos.y()))
        elif ev.modifiers() & Qt.ShiftModifier:
            sb = self.horizontalScrollBar()
            sb.setValue(sb.value() - (sb.singleStep() * 3 if d > 0 else -sb.singleStep() * 3))
        else:
            super().wheelEvent(ev)


# ============================================================ вікно
class TextScan(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        _work, self.xl, _out, bk = app.dirs()
        self.game = app.cur['game']
        if self.game not in textscan.MARKS:
            raise RuntimeError('Пошук написів на картинках є для Mary Skelter і Neptunia.')
        self.t = qtheme.palette(getattr(app, 'theme', 'stars'))
        self.name = textscan.MARKS[self.game]
        self.tex = textscan.Textures(self.game, bk, app.root_dir())
        self.styles = atl.load_styles()
        nep = self.game == 'nep'
        self._style_list()
        self.found = textscan.load_found(self.game)
        self.cur = None                 # (джерело, текстура)
        self.cur_iid = None
        self.img = None                 # RGBA поточної текстури
        self.flat = None                # вона ж на темному тлі (RGB) — для показу
        self.frames = {}
        self.multi = False
        self.done = []
        self.cands = []                 # [{текст, рамка, стан}]
        self.sel = None
        self.sel_done = None            # вибраний прийнятий напис (лише перегляд)
        self.drag = None
        self.gen = 0
        self.busy = False
        self.alive = True
        self.z = 1.0                     # масштаб картинки на полотні
        self.mode_val = 'рядки'          # тло під написом (як Tk-змінна: буває й не з MODES)
        self._loading = False
        self.items = {}                  # iid -> рядок списку текстур
        self.bridge = Bridge()
        self.bridge.got.connect(self._got)
        self.t_render = QTimer(self, singleShot=True, interval=250, timeout=self._render)

        self.setWindowTitle('Знайти написи на картинках — ' + ('Mary Skelter' if not nep else 'Neptunia'))
        self.resize(1460, 820)
        self.setMinimumSize(1100, 600)
        self._build()
        self._pic_state()
        self._fill()

    # ------------------------------------------------------------------ вигляд
    def _panel(self):
        f = QFrame()
        f.setObjectName('Panel')
        lay = QVBoxLayout(f)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        self.back.glow(f)
        return f, lay

    def _hint(self, text='', wrap=False):
        lab = QLabel(text)
        lab.setObjectName('Hint')
        lab.setWordWrap(wrap)
        return lab

    def _build(self):
        self.back = qtheme.Backdrop(getattr(self.app, 'theme', 'stars'))
        self.setCentralWidget(self.back)
        root = QVBoxLayout(self.back)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(10)

        top = QHBoxLayout()
        self.b_scan = QPushButton('Шукати написи (розпізнавання тексту)')
        self.b_scan.setObjectName('Accent')
        self.b_scan.clicked.connect(self._scan)
        top.addWidget(self.b_scan)
        top.addSpacing(8)
        self.all_cb = QCheckBox('Показати всі текстури')
        self.all_cb.toggled.connect(lambda _on: self._fill())
        top.addWidget(self.all_cb)
        # арти, новели, портрети тексту для розпізнавання не мають — але свою картинку
        # для них зробити можна
        self.art_cb = QCheckBox('і арти, новели, портрети')
        self.art_cb.toggled.connect(lambda _on: self._fill())
        top.addWidget(self.art_cb)
        self.status = self._hint()
        top.addWidget(self.status, 1)
        self.pbar = QProgressBar()
        self.pbar.setTextVisible(False)
        self.pbar.setFixedSize(200, 8)
        self.pbar.hide()
        top.addWidget(self.pbar)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(12)
        split.splitterMoved.connect(lambda *_a: self.back.update())
        root.addWidget(split, 1)

        left, ll = self._panel()
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(['Текстура', 'Нових', 'Розмічено'])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        hh = self.tree.header()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c, w in ((1, 54), (2, 76)):
            hh.setSectionResizeMode(c, QHeaderView.Fixed)
            self.tree.setColumnWidth(c, w)
        self.tree.currentItemChanged.connect(lambda _c, _p: self._open())
        ll.addWidget(self.tree)
        split.addWidget(left)

        mid, ml = self._panel()
        bar = QHBoxLayout()
        b = QPushButton('−')
        b.setFixedWidth(34)
        b.clicked.connect(lambda: self._zoom_by(1 / ZOOM_STEP))
        bar.addWidget(b)
        self.zoom_lbl = QLabel('100%')
        self.zoom_lbl.setFixedWidth(46)
        self.zoom_lbl.setAlignment(Qt.AlignCenter)
        bar.addWidget(self.zoom_lbl)
        b = QPushButton('+')
        b.setFixedWidth(34)
        b.clicked.connect(lambda: self._zoom_by(ZOOM_STEP))
        bar.addWidget(b)
        b = QPushButton('Вмістити')
        b.clicked.connect(self._fit)
        bar.addWidget(b)
        b = QPushButton('100%')
        b.clicked.connect(lambda: self._zoom_to(1.0))
        bar.addWidget(b)
        bar.addWidget(self._hint('Ctrl + коліщатко — масштаб, права кнопка — тягнути картинку. Ліва по порожньому '
                                 '— нова рамка; Delete / BackSpace — прибрати рамку. Зелене — розмічено, '
                                 'жовте — знайдено.', wrap=True), 1)
        ml.addLayout(bar)
        # своя картинка: вивантажити оригінал у PNG, перемалювати деінде й завантажити назад
        own = QHBoxLayout()
        own.addWidget(QLabel('Своя картинка:'))
        self.b_pic_out = QPushButton('Вивантажити оригінал')
        self.b_pic_out.clicked.connect(self._pic_export)
        own.addWidget(self.b_pic_out)
        self.b_pic_in = QPushButton('Завантажити свою…')
        self.b_pic_in.clicked.connect(self._pic_import)
        own.addWidget(self.b_pic_in)
        self.b_pic_rm = QPushButton('Прибрати свою')
        self.b_pic_rm.clicked.connect(self._pic_remove)
        own.addWidget(self.b_pic_rm)
        self.c_pic_show = QCheckBox('показувати свою')
        self.c_pic_show.toggled.connect(lambda _on: self._pic_show())
        own.addWidget(self.c_pic_show)
        self.pic_state = self._hint()
        own.addWidget(self.pic_state, 1)
        ml.addLayout(own)
        self.canvas = Canvas(self)
        ml.addWidget(self.canvas, 1)
        split.addWidget(mid)

        right, rl = self._panel()
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(4, 2, 8, 2)
        lay.setSpacing(4)
        sa.setWidget(inner)
        rl.addWidget(sa)
        split.addWidget(right)
        lab = QLabel('Вибраний напис')
        lab.setObjectName('Section')
        lay.addWidget(lab)
        lay.addWidget(QLabel('Текст (англійською, як на картинці):'))
        self.e_text = QLineEdit()
        self.e_text.textChanged.connect(lambda _t: self._changed())
        lay.addWidget(self.e_text)
        lay.addWidget(QLabel('Стиль (яким малювати переклад):'))
        row = QHBoxLayout()
        self.style_cb = StylePicker()
        self.style_cb.refresh(self.styles, self.style_names)
        self.style_cb.changed.connect(lambda _n: (self._own_state(), self._changed()))
        row.addWidget(self.style_cb, 1)
        self.b_styles = QPushButton('Усі стилі…')
        self.b_styles.clicked.connect(self._gallery)
        row.addWidget(self.b_styles)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.b_new_style = QPushButton('Новий стиль…')
        self.b_new_style.clicked.connect(lambda: self._edit_style(False))
        row.addWidget(self.b_new_style)
        self.b_edit_style = QPushButton('Змінити свій стиль…')
        self.b_edit_style.clicked.connect(lambda: self._edit_style(True))
        row.addWidget(self.b_edit_style)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel('Тло під написом (як стерти старий):'))
        self.radios = {}
        self.mode_grp = QButtonGroup(self)
        for m, d in MODES.items():
            rb = QRadioButton(f'{m} — {d}')
            rb.clicked.connect(lambda _c=False, m=m: (setattr(self, 'mode_val', m), self._changed()))
            self.mode_grp.addButton(rb)
            self.radios[m] = rb
            lay.addWidget(rb)
        self._set_mode('рядки')
        self.prev = []
        for title in ('Оригінал', 'Стерто', 'Наш напис (англ.)'):
            lay.addWidget(self._hint(title))
            lab = QLabel()
            lab.setStyleSheet(f'background: {CANVAS_BG};')
            lab.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            lab.hide()
            lay.addWidget(lab, 0, Qt.AlignLeft)
            self.prev.append(lab)
        self.warn = QLabel('')
        self.warn.setObjectName('Warn')
        self.warn.setWordWrap(True)
        lay.addWidget(self.warn)
        row = QHBoxLayout()
        self.b_ok = QPushButton('Прийняти напис')
        self.b_ok.setObjectName('Accent')
        self.b_ok.clicked.connect(self._accept)
        row.addWidget(self.b_ok)
        self.b_no = QPushButton('Не текст')
        self.b_no.clicked.connect(self._reject)
        row.addWidget(self.b_no)
        self.b_revoke = QPushButton('Відкликати прийняття')
        self.b_revoke.clicked.connect(self._revoke)
        row.addWidget(self.b_revoke)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addSpacing(8)
        lay.addWidget(self._hint('Прийняте потрапляє в розмітку перекладача (атлас, файл «.мої.json»). '
                                 'Після «1. Дістати текст з гри» напис з\'явиться в книзі написів і в '
                                 'редакторі — там його й перекладай. Надішли цей файл власнику програми, '
                                 'щоб розмітка не загубилась.', wrap=True))
        lay.addStretch(1)
        left.setMinimumWidth(250)
        right.setMinimumWidth(440)
        split.setSizes([330, 680, 440])
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 1)
        self._enable(False)

    def _set_mode(self, m):
        """Тло під написом; значення не з MODES (шаблон, картинка…) — жоден перемикач не вибрано."""
        self.mode_val = m or ''
        self.mode_grp.setExclusive(False)
        for k, rb in self.radios.items():
            rb.setChecked(k == self.mode_val)
        self.mode_grp.setExclusive(True)

    def _enable(self, on):
        for b in (self.b_ok, self.b_no, self.b_styles, self.b_new_style, self.e_text, self.style_cb,
                  *self.radios.values()):
            b.setEnabled(on)
        self.b_revoke.setEnabled(self.sel_done is not None)
        self._own_state()

    def _gallery(self):
        if self.sel is not None:
            StyleGallery(self).show()

    def keyPressEvent(self, ev):
        # Delete / BackSpace — прибрати вибрану рамку; у полі вводу клавіша редагує текст
        # (поле її забирає собі, сюди вона не доходить)
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self._reject()
            return
        super().keyPressEvent(ev)

    def closeEvent(self, ev):
        self.alive = False              # фоновий пошук зупиниться сам
        self.gen += 1
        self.img = self.flat = None     # великі текстури — звільнити одразу
        self.canvas.drop_cache()
        super().closeEvent(ev)

    def winfo_exists(self):
        return self.alive and self.isVisible()

    # ------------------------------------------------------------------ своя картинка
    def _pic_path(self, existing=False):
        """Шлях до своєї картинки поточної текстури (pics.py); existing — None, якщо файла ще немає."""
        if not self.cur:
            return None
        src, stem = self.cur
        p = os.path.join(pics.folder(self.xl), pics.name_of(src, stem if self.multi else ''))
        return p if not existing or os.path.exists(p) else None

    def _pic_state(self):
        on = self.cur is not None
        have = bool(self._pic_path(existing=True))
        self.b_pic_out.setEnabled(on)
        self.b_pic_in.setEnabled(on)
        self.b_pic_rm.setEnabled(have)
        self.c_pic_show.setEnabled(have)
        self.pic_state.setText('є твоя — у грі буде вона (написи — поверх)' if have else
                               ('немає, у грі — оригінал' if on else ''))

    def _set_show_own(self, on):
        self.c_pic_show.blockSignals(True)
        self.c_pic_show.setChecked(on)
        self.c_pic_show.blockSignals(False)

    def _pic_show(self, fit=True):
        """Показувати на полотні свою картинку чи оригінал (розмітка й прев'ю — завжди з оригіналу)."""
        if self.img is None:
            return
        im = self.img
        p = self._pic_path(existing=True)
        if self.c_pic_show.isChecked() and p:
            try:
                im = pics.load(p, self.img)
            except (OSError, ValueError) as ex:
                self.pic_state.setText(f'не вдалося відкрити свою: {ex}')
        bg = Image.new('RGBA', im.size, BG)
        bg.alpha_composite(im)
        self.flat = bg.convert('RGB')
        if fit:
            self._redraw(image=True)

    def _pic_export(self):
        """Чистий оригінал текстури — у PNG у «Свої картинки»; тека відкривається з вибраним файлом."""
        p = self._pic_path()
        if p is None:
            return
        if os.path.exists(p) and QMessageBox.question(
                self, 'Своя картинка', 'Для цієї текстури вже є твоя картинка:\n' + os.path.basename(p) +
                '\n\nПереписати її оригіналом? Твої зміни в ній пропадуть.') != QMessageBox.Yes:
            return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        self.img.save(p)
        self._pic_state()
        self._fill_keep()
        self.status.setText(f'Оригінал вивантажено: {p}. Перемалюй його (розмір не міняй, прозорість '
                            'збережи) і збережи поверх — або «Завантажити свою…».')
        try:
            subprocess.Popen(['explorer', '/select,', os.path.normpath(p)])
        except OSError:
            pass

    def _pic_import(self, fn=None):
        p = self._pic_path()
        if p is None:
            return
        if not fn:
            fn, _f = QFileDialog.getOpenFileName(self, 'Своя картинка для цієї текстури', '',
                                                 'Картинки (*.png *.tga *.bmp *.webp);;Усі (*.*)')
        if not fn:
            return
        try:
            im = pics.load(fn, self.img)
        except (OSError, ValueError) as ex:
            QMessageBox.critical(self, 'Своя картинка', f'Не підходить: {ex}')
            return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.abspath(fn) != os.path.abspath(p):
            im.save(p)
        n = len(pics.changed_rects(self.img, im))
        self._set_show_own(True)
        self._pic_state()
        self._pic_show()
        self._fill_keep()
        self.status.setText('Свою картинку завантажено' + ('' if n else ' — але вона така сама, як оригінал') +
                            '. У гру піде після «2. Залити переклад у гру».')

    def _pic_remove(self):
        p = self._pic_path(existing=True)
        if not p or QMessageBox.question(self, 'Своя картинка', 'Прибрати свою картинку? У грі знову буде '
                                         'оригінал.\n\n' + os.path.basename(p)) != QMessageBox.Yes:
            return
        os.remove(p)
        self._set_show_own(False)
        self._pic_state()
        self._pic_show()
        self._fill_keep()

    def _fill_keep(self):
        """Оновити список (позначка ✎), не втрачаючи вибраної текстури."""
        s = self.cur_iid
        self._fill()
        it = self.items.get(s)
        if it is not None:
            self.tree.blockSignals(True)            # _open ту саму текстуру не перевідкриває
            self.tree.setCurrentItem(it)
            self.tree.blockSignals(False)
            self.tree.scrollToItem(it)

    # ------------------------------------------------------------------ свої стилі
    def _style_list(self):
        """Стилі цієї гри: основні (у Neptunia — з префіксом «неп-») і свої, створені для неї."""
        self.style_names = atl.game_styles(self.styles, self.game)
        if hasattr(self, 'style_cb'):
            self.style_cb.refresh(self.styles, self.style_names)

    def _own_state(self):
        own = bool(self.styles.get(self.style_cb.get(), {}).get('мій')) if hasattr(self, 'style_cb') else False
        if hasattr(self, 'b_edit_style'):
            self.b_edit_style.setEnabled(own and self.sel is not None)

    def _edit_style(self, edit):
        if self.sel is not None:
            StyleEditor(self, self.style_cb.get(), edit).show()

    def style_sample(self):
        """Для редактора стилю: (картинка, кадр, spec, [англ. текст, укр. текст|None]) вибраного
        напису або None."""
        if self.sel is None or self.img is None:
            return None
        key, spec = self._spec()
        return self.img, tuple(atl.box_of(key, spec, self.frames)), spec, [spec['текст'], None]

    def styles_changed(self, chosen=None):
        """Свої стилі змінились (редактор): перечитати й, якщо треба, вибрати `chosen`."""
        self.styles = atl.load_styles()
        self._style_list()
        if chosen is not None:
            self.style_cb.set(chosen if chosen in self.style_names else '')
        self._changed()

    # ------------------------------------------------------------------ список
    def _marks(self):
        return atl.load_marks(self.name)

    def _rejected(self, key):
        return atl.load_user(self.name).get('_відхилено', {}).get(key, [])

    def _marked(self, src, stem, frames, marks=None):
        """Прийняті написи текстури: [{ключ, spec, рамка}] (рамка — кадр в атласі)."""
        mk = (marks if marks is not None else self._marks()).get(src)
        if not mk:
            return []
        if stem and mk.get('текстура') and mk['текстура'].lower() != stem.lower():
            return []
        out = []
        for i, spec in mk.get('кадри', {}).items():
            b = atl.box_of(i, spec, frames)
            if b:
                out.append({'ключ': i, 'spec': spec, 'рамка': list(b)})
        return out

    def _marked_boxes(self, src, stem, frames, marks=None):
        return [d['рамка'] for d in self._marked(src, stem, frames, marks)]

    def _fill(self):
        marks = self._marks()
        srcs = set(self.found)
        every = self.all_cb.isChecked() or self.art_cb.isChecked()
        if every:
            srcs |= set(self._all_srcs())
            srcs |= set(marks)
        own = pics.found(self.xl)
        rows = []
        if not hasattr(self, '_frames'):
            self._frames = self.tex.frames_of([s for s in self.found if self.found[s]])
        for src in srcs:
            for stem, lst in (self.found.get(src) or {'': []}).items():
                key = f'{src}|{stem}'
                rej = self._rejected(key)
                fr = self._frames.get(src, {})
                done_b = (self._marked_boxes(src, stem if len(fr) > 1 else '', fr.get(stem, {}), marks)
                          if lst else [])
                n = sum(1 for c in lst if not any(textscan.overlap(c['рамка'], r) > 0.5 for r in rej)
                        and not any(textscan.overlap(c['рамка'], d) > 0.5 for d in done_b))
                done = len((marks.get(src) or {}).get('кадри', {}))
                if n or every:
                    rows.append((src, stem, n, done))
        rows.sort(key=lambda r: (-r[2], r[0]))
        self.tree.blockSignals(True)
        self.tree.clear()
        self.items = {}
        for src, stem, n, done in rows:
            label = src.split('/', 1)[-1] + (f' [{stem}]' if stem and stem.lower() not in src.lower() else '')
            if src in own:
                label += '  ✎'                   # є своя картинка
            it = QTreeWidgetItem([label, str(n or ''), str(done or '')])
            it.setData(0, Qt.UserRole, f'{src}|{stem}')
            it.setToolTip(0, src)
            for c in (1, 2):
                it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
            self.tree.addTopLevelItem(it)
            self.items[f'{src}|{stem}'] = it
        self.tree.setCurrentItem(None)
        self.tree.blockSignals(False)
        if not rows:
            self.status.setText('Натисни «Шукати написи», щоб програма знайшла кандидатів, або постав '
                                '«Показати всі текстури» й розмічай вручну.')
        else:
            self.status.setText(f'Текстур у списку: {len(rows)}.')

    def _all_srcs(self):
        art = self.art_cb.isChecked()
        if getattr(self, '_srcs_art', None) != art:
            self._srcs, self._srcs_art = self.tex.list(everything=art), art
        return self._srcs

    def select_texture(self, iid):
        """Вибрати текстуру в списку (для тестів і переходів)."""
        it = self.items.get(iid)
        if it is not None:
            self.tree.setCurrentItem(it)
            self.tree.scrollToItem(it)
        return it is not None

    # ------------------------------------------------------------------ пошук
    def _scan(self):
        if self.busy:
            return
        self.busy = True
        self.b_scan.setEnabled(False)
        self.status.setText('Перевіряю, чи вміє Windows розпізнавати англійський текст…')
        br = self.bridge
        threading.Thread(target=lambda: br.got.emit('status', textscan.ocr_status()), daemon=True).start()

    def _after_status(self, st):
        if st == 'nolang':
            if QMessageBox.question(
                    self, 'Розпізнавання тексту',
                    'У Windows не встановлено розпізнавання англійського тексту.\n\n'
                    'Встановити зараз? Windows попросить дозволу адміністратора (вікно UAC), '
                    'далі компонент завантажиться з інтернету (кілька МБ, до хвилини).') == QMessageBox.Yes:
                self.status.setText('Встановлюю компонент розпізнавання… (підтверди запит Windows)')
                br = self.bridge
                threading.Thread(target=lambda: br.got.emit('status2', textscan.install_ocr()),
                                 daemon=True).start()
                return
            self._done_busy('Без розпізнавання можна розмічати вручну: «Показати всі текстури».')
            return
        if st != 'ok':
            self._done_busy('')
            QMessageBox.warning(self, 'Розпізнавання тексту',
                                'Розпізнавання тексту Windows недоступне на цьому комп\'ютері '
                                '(потрібна Windows 10 чи 11). Розмічати можна вручну: '
                                '«Показати всі текстури» і мишею по картинці.')
            return
        self.status.setText('Готую перелік текстур…')
        self.pbar.show()
        br = self.bridge

        def work():
            try:
                srcs = self._all_srcs()
                res = textscan.scan(self.tex, srcs,
                                    progress=lambda i, n, t: br.got.emit('prog', (i, n, t)),
                                    stop=lambda: not self.alive)
            except Exception as ex:                                   # noqa: BLE001
                br.got.emit('scan_err', str(ex))
                return
            br.got.emit('found', res)
        threading.Thread(target=work, daemon=True).start()

    def _done_busy(self, msg):
        self.busy = False
        self.b_scan.setEnabled(True)
        self.pbar.hide()
        if msg:
            self.status.setText(msg)

    def _got(self, kind, val):
        if not self.alive:
            return
        if kind == 'status':
            self._after_status(val)
        elif kind == 'status2':
            if val == 'ok':
                self.status.setText('Розпізнавання встановлено.')
                self._after_status('ok')
            else:
                self._done_busy('Встановити не вдалося (або запит Windows відхилено).')
        elif kind == 'prog':
            i, n, txt = val
            self.pbar.setMaximum(max(1, n))
            self.pbar.setValue(round(i))
            self.status.setText(f'{txt} {round(i)}/{n}')
        elif kind == 'scan_err':
            self._done_busy(f'Пошук не вдався: {val}')
        elif kind == 'found':
            self.found = val
            textscan.save_found(self.game, self.found)
            self.__dict__.pop('_frames', None)
            n = sum(len(v) for s in self.found.values() for v in s.values())
            self._done_busy(f'Знайдено кандидатів: {n}.')
            self._fill()
        elif kind == 'preview':
            self._show_preview(*val)

    # ------------------------------------------------------------------ текстура
    def _open(self):
        it = self.tree.currentItem()
        if it is None:
            return
        iid = it.data(0, Qt.UserRole)
        if iid == self.cur_iid and self.img is not None:
            return
        self.cur_iid = iid
        src, _, stem = iid.rpartition('|')
        try:
            parts = self.tex.load(src)
        except Exception as ex:                                       # noqa: BLE001
            self.status.setText(f'Не вдалося відкрити {src}: {ex}')
            return
        part = next((p for p in parts if p[0] == stem), parts[0] if parts else None)
        if part is None:
            return
        # текстура — як у пошуку (назва .tid у CL3); у розмітку "текстура" пишемо, лише
        # коли пар у атласі кілька
        self.cur = (src, part[0])
        self.multi = len(parts) > 1
        self.img, self.frames = part[1], part[2]
        rej = self._rejected(f'{src}|{part[0]}')
        self.done = self._marked(src, part[0] if self.multi else '', self.frames)
        self.cands = [dict(c) for c in (self.found.get(src) or {}).get(part[0], [])
                      if not any(textscan.overlap(c['рамка'], r) > 0.5 for r in rej)
                      and not any(textscan.overlap(c['рамка'], d['рамка']) > 0.5 for d in self.done)]
        self.sel = None
        self.sel_done = None
        self._select(None)
        bg = Image.new('RGBA', self.img.size, BG)
        bg.alpha_composite(self.img)
        self.flat = bg.convert('RGB')                 # текстура на темному тлі — для показу
        self.canvas.drop_cache()
        self._pic_state()
        if self.c_pic_show.isChecked() and self._pic_path(existing=True):
            self._pic_show(fit=False)
        self._fit()

    # ------------------------------------------------------------------ масштаб
    def _fit(self):
        """Уся текстура у вікні: велика — зменшити, мала — збільшити (до 400%)."""
        if self.img is None:
            return
        vp = self.canvas.viewport()
        w, h = max(50, vp.width() - 4), max(50, vp.height() - 4)
        self._zoom_to(min(4.0, w / self.img.width, h / self.img.height))

    def _zoom_by(self, k, at=None):
        self._zoom_to(self.z * k, at)

    def _zoom_to(self, z, at=None):
        """Новий масштаб; точка під курсором (або центр полотна) лишається на місці."""
        if self.img is None:
            return
        z = max(ZOOM_MIN, min(ZOOM_MAX, z))
        c = self.canvas
        vp = c.viewport()
        ex, ey = at if at is not None else (vp.width() / 2, vp.height() / 2)
        sx, sy = c.offset()
        ix, iy = (sx + ex) / self.z, (sy + ey) / self.z
        self.z = z
        c.sync()
        c.horizontalScrollBar().setValue(max(0, round(ix * z - ex)))
        c.verticalScrollBar().setValue(max(0, round(iy * z - ey)))
        self.zoom_lbl.setText(f'{round(z * 100)}%')
        self._redraw(True)

    def _redraw(self, image=False):
        if image:
            self.canvas.drop_cache()
        self.canvas.viewport().update()

    @staticmethod
    def _handles(b):
        x0, y0, x1, y1 = b
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return [(x0, y0), (mx, y0), (x1, y0), (x1, my), (x1, y1), (mx, y1), (x0, y1), (x0, my)]

    # ------------------------------------------------------------------ миша (координати текстури)
    def _press(self, x, y):
        if self.img is None:
            return
        tol = HANDLE / self.z
        if self.sel is not None:
            b = self.cands[self.sel]['рамка']
            for n, (hx, hy) in enumerate(self._handles(b)):
                if abs(hx - x) <= tol and abs(hy - y) <= tol:
                    self.drag = ('size', n, list(b), x, y)
                    return
        for k in range(len(self.cands) - 1, -1, -1):
            x0, y0, x1, y1 = self.cands[k]['рамка']
            if x0 <= x <= x1 and y0 <= y <= y1:
                self._select(k)
                self.drag = ('move', None, list(self.cands[k]['рамка']), x, y)
                return
        # клік по прийнятому напису — подивитись (змінити — «Відкликати прийняття»);
        # потягнути — нова рамка, як і деінде (кадр буває більшим за сам напис)
        hit = [k for k, d in enumerate(self.done)
               if d['рамка'][0] <= x <= d['рамка'][2] and d['рамка'][1] <= y <= d['рамка'][3]]

        def area(k):
            b = self.done[k]['рамка']
            return (b[2] - b[0]) * (b[3] - b[1])
        self.drag = ('new', min(hit, key=area) if hit else None, None, x, y)

    def _motion(self, x, y):
        if not self.drag or self.img is None:
            return
        kind, n, b0, sx, sy = self.drag
        W, H = self.img.size
        if kind == 'new':
            if abs(x - sx) < 4 and abs(y - sy) < 4:
                return
            self.cands.append({'текст': '', 'рамка': [0, 0, 0, 0], 'ручна': True})
            self._select(len(self.cands) - 1)
            self.drag = kind, n, b0, sx, sy = ('size', 4, [sx, sy, sx, sy], sx, sy)
        b = list(b0)
        if kind == 'move':
            dx, dy = x - sx, y - sy
            dx = max(-b[0], min(W - b[2], dx))
            dy = max(-b[1], min(H - b[3], dy))
            b = [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy]
        else:
            if n in (0, 6, 7):
                b[0] = x
            if n in (2, 3, 4):
                b[2] = x
            if n in (0, 1, 2):
                b[1] = y
            if n in (4, 5, 6):
                b[3] = y
        x0, x1 = sorted((max(0, min(W, b[0])), max(0, min(W, b[2]))))
        y0, y1 = sorted((max(0, min(H, b[1])), max(0, min(H, b[3]))))
        self.cands[self.sel]['рамка'] = [round(x0), round(y0), round(x1), round(y1)]
        self._redraw()

    def _release(self):
        if self.drag and self.drag[0] == 'new':            # клік без рамки кандидата
            k = self.drag[1]
            self.drag = None
            if k is None:
                self._select(None)                         # по порожньому — зняти вибір
            else:
                self._select_done(k)
            return
        if self.drag and self.sel is not None:
            b = self.cands[self.sel]['рамка']
            if b[2] - b[0] < 4 or b[3] - b[1] < 4:        # випадковий клік — не рамка
                del self.cands[self.sel]
                self._select(None)
            else:
                self._guess()
                self._changed()
        self.drag = None
        self._redraw()

    # ------------------------------------------------------------------ вибраний
    def _clear_preview(self):
        for lab in self.prev:
            lab.clear()
            lab.hide()

    def _select(self, k):
        self.sel = k
        self.sel_done = None
        self._enable(k is not None)
        if k is None:
            self.e_text.setText('')
            self._clear_preview()
            self.warn.setText('')
            self._redraw()
            return
        cd = self.cands[k]
        self._loading = True
        self.e_text.setText(cd.get('текст', ''))
        self._loading = False
        self._guess()
        self._redraw()
        self._changed()

    def _select_done(self, k):
        """Прийнятий напис: показати, як його розмічено, без права змінювати."""
        self._select(None)
        self.sel_done = k
        d = self.done[k]
        self._loading = True
        self.e_text.setText(d['spec'].get('текст', ''))
        self.style_cb.set(d['spec'].get('стиль', ''))
        self._set_mode(d['spec'].get('тло', ''))
        self._loading = False
        self._enable(False)
        self.status.setText('Цей напис уже прийнято. Щоб змінити текст, стиль чи рамку — '
                            '«Відкликати прийняття».')
        self._redraw()
        self._render(d['ключ'], d['spec'])

    def _revoke(self):
        """Прийнятий напис → знову кандидат з тими самими текстом, стилем і тлом. У розмітці
        він лишається, доки не натиснеш «Прийняти напис» (замінить) чи «Не текст» (прибере)."""
        if self.sel_done is None:
            return
        d = self.done.pop(self.sel_done)
        spec = d['spec']
        fx0, fy0, fx1, fy1 = d['рамка']
        a = spec.get('область')
        box = ([fx0 + a[0], fy0 + a[1], fx0 + a[2], fy0 + a[3]]
               if a and not spec.get('обертання') else [fx0, fy0, fx1, fy1])
        self.cands.append({'текст': spec.get('текст', ''), 'рамка': box, 'стиль': spec.get('стиль'),
                           'тло': spec.get('тло'), 'ручна': True,
                           'старий': {'ключ': d['ключ'], 'spec': spec, 'рамка0': list(box)}})
        self._select(len(self.cands) - 1)
        self.status.setText('Напис відкликано: зміни, що треба, і «Прийняти напис». Доки не натиснеш, '
                            'у розмітці лишається старий варіант.')

    def _guess(self):
        """Стиль і тло — за кольором літер і прозорістю навколо напису."""
        cd = self.cands[self.sel]
        x0, y0, x1, y1 = cd['рамка']
        if x1 - x0 < 2 or y1 - y0 < 2:
            return
        if cd.get('старий'):                       # відкликаний: тло й стиль — як були
            self._set_mode(cd.get('тло') or 'рядки')
            if cd.get('стиль'):
                self.style_cb.set(cd['стиль'])
            return
        crop = self.img.crop((x0, y0, x1, y1))
        px = [p for p in crop.getdata() if p[3] > 200 and max(p[:3]) > 150]
        ring = self.img.crop((max(0, x0 - 4), max(0, y0 - 4), min(self.img.width, x1 + 4),
                              min(self.img.height, y1 + 4)))
        alphas = [p[3] for p in ring.getdata()]
        self._set_mode('прозорий' if sum(a < 40 for a in alphas) > len(alphas) * 0.3 else 'рядки')
        if cd.get('стиль'):
            self.style_cb.set(cd['стиль'])
            return
        if not self.style_names:
            return
        marks = self._marks()
        used = collections.Counter(s['стиль'] for m in marks.values() for s in m.get('кадри', {}).values()
                                   if s.get('стиль') in self.style_names)
        if not px:
            # світлих літер не видно (рамка ще порожня) — найуживаніший стиль атласу, інакше гри
            here = collections.Counter(s.get('стиль') for s in (marks.get(self.cur[0]) or {}).get('кадри', {}).values()
                                       if s.get('стиль') in self.style_names)
            best = (here or used).most_common(1)
            self.style_cb.set(best[0][0] if best else self.style_names[0])
            return
        med = [sorted(p[c] for p in px)[len(px) // 2] for c in range(3)]

        def dist(name):
            fill = self.styles[name].get('заливка', '#ffffff')
            fill = fill[0] if isinstance(fill, list) else fill
            rgb = [int(fill[i:i + 2], 16) for i in (1, 3, 5)]
            d = sum((a - b) ** 2 for a, b in zip(rgb, med))
            return d + (0 if name in used else 4000)            # уживані стилі — вперед
        self.style_cb.set(min(self.style_names, key=dist))

    def _spec(self):
        """Кадр розмітки для вибраної рамки: (ключ, spec) у форматі написи.json. Відкликаний
        напис: рамка й тло ті самі — старий кадр з новими текстом і стилем; інакше — новий,
        але з тим самим ключем і додатковими налаштуваннями вигляду."""
        cd = self.cands[self.sel]
        old = cd.get('старий')
        if old and cd['рамка'] == old['рамка0'] and self.mode_val == old['spec'].get('тло'):
            return old['ключ'], dict(old['spec'], текст=self.e_text.text().strip() or '?',
                                     стиль=self.style_cb.get())
        key, spec = self._new_spec()
        if old:
            key = old['ключ']
            spec.update({k: v for k, v in old['spec'].items() if k in KEEP})
        return key, spec

    def _new_spec(self):
        cd = self.cands[self.sel]
        x0, y0, x1, y1 = cd['рамка']
        W, H = self.img.size
        # кадр нарізки, у якому лежить напис: ручна рамка має бути всередині нього
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        inside = [f for f in self.frames.values() if f[0] <= cx <= f[2] and f[1] <= cy <= f[3]]
        frame = min(inside, key=lambda f: (f[2] - f[0]) * (f[3] - f[1])) if inside else (0, 0, W, H)
        fb = [max(frame[0], x0 - PAD_FRAME), max(frame[1], y0 - PAD_FRAME // 2),
              min(frame[2], x1 + PAD_FRAME), min(frame[3], y1 + PAD_FRAME // 2)]
        area = [max(0, x0 - PAD_AREA - fb[0]), max(0, y0 - PAD_AREA - fb[1]),
                min(fb[2] - fb[0], x1 + PAD_AREA - fb[0]), min(fb[3] - fb[1], y1 + PAD_AREA - fb[1])]
        mode = self.mode_val
        spec = {'текст': self.e_text.text().strip() or '?', 'стиль': self.style_cb.get(), 'тло': mode,
                'рамка': fb, 'область': area, 'літери': [y0 - fb[1], y1 - fb[1]],
                'поле': atl.suggest_field(area, fb[2] - fb[0], mode), 'вирівняти': 'центр'}
        if not inside and self.game == 'msk':
            spec['без кадру'] = True
        return ','.join(str(v) for v in fb), spec

    def _changed(self):
        if self._loading or self.sel is None:
            return
        self.cands[self.sel]['текст'] = self.e_text.text()
        if self.cands[self.sel].get('старий'):
            self.cands[self.sel]['тло'] = self.mode_val
        if self.style_cb.get():
            self.cands[self.sel]['стиль'] = self.style_cb.get()
        self.t_render.start()

    def _render(self, key=None, spec=None):
        self.t_render.stop()
        if spec is None:
            if self.sel is None or not self.style_cb.get():
                return
            key, spec = self._spec()
        self.gen += 1
        gen, img = self.gen, self.img
        styles = self.styles
        box = tuple(atl.box_of(key, spec, self.frames))       # кадр TI — без "рамка" в розмітці
        br = self.bridge

        def work():
            try:
                a = img.copy()
                atl.erase(a, box, spec)
                b = img.copy()
                warn = atl.draw(b, box, spec, styles, spec['текст'])
                br.got.emit('preview', (gen, img.crop(box), a.crop(box), b.crop(box), warn))
            except Exception as ex:                                   # noqa: BLE001
                br.got.emit('preview', (gen, img.crop(box), None, None, [f'не вдалося: {ex}']))
        threading.Thread(target=work, daemon=True).start()

    def _show_preview(self, gen, orig, erased, drawn, warn):
        if gen != self.gen:
            return
        for lab, im in zip(self.prev, (orig, erased, drawn)):
            if im is None:
                lab.clear()
                lab.hide()
                continue
            lab.setPixmap(pil_pixmap(im, min(3.0, 320 / max(1, im.width))))
            lab.show()
        self.warn.setText('\n'.join(warn))

    # ------------------------------------------------------------------ рішення
    def _accept(self):
        if self.sel is None:
            return
        if not self.e_text.text().strip():
            QMessageBox.information(self, 'Напис', 'Впиши англійський текст напису (як на картинці).')
            return
        src, stem = self.cur
        cd = self.cands[self.sel]
        old = cd.get('старий')
        if old and self.mode_val not in MODES and cd['рамка'] != old['рамка0']:
            QMessageBox.information(self, 'Напис', f'Тло «{self.mode_val}» прив\'язане до старої рамки. '
                                    'Поверни рамку як була або вибери, як стерти старий напис.')
            return
        key, spec = self._spec()
        if old and atl.key_of(old['spec']) != atl.key_of(spec) and QMessageBox.question(
                self, 'Напис', f'Текст змінився: «{old["spec"].get("текст", "")}» → «{spec["текст"]}».\n\n'
                'У книзі написів це буде новий рядок: переклад старого (якщо він був) сюди не '
                'перейде. Прийняти?') != QMessageBox.Yes:
            return
        marks = atl.load_marks(self.name)
        mk = marks.get(src)
        if mk is None:
            mk = marks[src] = {'назва': src.split('/')[-1], 'кадри': {}}
            if self.multi and stem:
                mk['текстура'] = stem
        elif self.multi and stem and (mk.get('текстура') or '').lower() != stem.lower():
            QMessageBox.warning(self, 'Напис', 'У цьому атласі вже розмічено іншу текстуру — '
                                'дві в одному атласі програма поки не підтримує.')
            return
        mk.setdefault('кадри', {})[key] = spec
        atl.save_user_marks(self.name, marks)
        self.done.append({'ключ': key, 'spec': spec,
                          'рамка': list(atl.box_of(key, spec, self.frames) or spec['рамка'])})
        del self.cands[self.sel]
        self._select(None)
        self._update_row()
        self.status.setText(f'Прийнято: «{spec["текст"]}». Після «1» він з\'явиться в книзі написів.')

    def _reject(self):
        if self.sel is None:
            return
        cd = self.cands[self.sel]
        old = cd.get('старий')
        if old:                                          # відкликаний — прибрати з розмітки
            if QMessageBox.question(self, 'Напис', f'Прибрати напис «{old["spec"].get("текст", "")}» '
                                    'з розмітки? У книзі його більше не буде.') != QMessageBox.Yes:
                return
            marks = atl.load_marks(self.name)
            (marks.get(self.cur[0]) or {}).get('кадри', {}).pop(old['ключ'], None)
            atl.save_user_marks(self.name, marks)
        elif not cd.get('ручна'):
            user = atl.load_user(self.name)
            rej = user.setdefault('_відхилено', {})
            rej.setdefault(f'{self.cur[0]}|{self.cur[1]}', []).append(cd['рамка'])
            atl.save_user(self.name, user)
        del self.cands[self.sel]
        self._select(None)
        self._update_row()

    def _update_row(self):
        it = self.tree.currentItem()
        if it is not None:
            done = len((self._marks().get(self.cur[0]) or {}).get('кадри', {}))
            it.setText(1, str(len(self.cands) or ''))
            it.setText(2, str(done or ''))


# ============================================================ галерея стилів
class Card(QFrame):
    """Картка галереї: назва + картинка; клік — вибрати, подвійний клік — взяти."""
    clicked = Signal(int)
    double = Signal(int)

    def __init__(self, n, title, bold=False, color=None, pic_bg=CANVAS_BG):
        super().__init__()
        self.n = n
        self.setObjectName('Card')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 6)
        lay.setSpacing(3)
        self.name = QLabel(title)
        if bold or color:
            self.name.setStyleSheet(('font-weight: bold;' if bold else '') + (f'color: {color};' if color else ''))
        lay.addWidget(self.name)
        self.pic = QLabel('…')
        self.pic.setStyleSheet(f'background: {pic_bg}; color: #999999;')
        self.pic.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lay.addWidget(self.pic, 0, Qt.AlignLeft)
        self.mark(False)

    def mark(self, on, color='#ff8a00'):
        self.setStyleSheet(f'QFrame#Card {{ border: 2px solid {color if on else "transparent"}; '
                           'border-radius: 6px; }}')

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self.n)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.double.emit(self.n)


class StyleGallery(QDialog):
    """«Усі стилі»: вибраний напис, намальований кожним стилем гри (стерте тло + англійський
    текст), — щоб не перебирати стилі по одному. Клік — вибрати, подвійний клік чи «Взяти
    вибраний» — у вікно пошуку. Уживані в грі стилі — першими."""

    COLS = 3
    CARD_W = 300                            # ширина картинки в картці, пікселі екрана

    def __init__(self, scan):
        super().__init__(scan)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.scan = scan
        self.key, self.spec = scan._spec()
        self.img = scan.img
        self.styles = scan.styles
        used = collections.Counter(s.get('стиль') for m in scan._marks().values()
                                   for s in m.get('кадри', {}).values())
        # за групами; у групі — уживані в грі першими
        self.groups = [(g, sorted(ns, key=lambda n: -used.get(n, 0)))
                       for g, ns in atl.style_groups(scan.styles, scan.style_names)]
        self.names = [n for _g, ns in self.groups for n in ns]
        cur = scan.style_cb.get()
        self.sel = self.names.index(cur) if cur in self.names else 0
        self.alive = True
        self.bridge = Bridge()
        self.bridge.got.connect(self._got)
        self.setWindowTitle(f'Усі стилі — «{self.spec["текст"]}»')
        self.resize(1060, 760)
        box = tuple(self.spec['рамка'])
        self.zoom = min(2.0, self.CARD_W / max(1, box[2] - box[0]))

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        top = QHBoxLayout()
        top.addWidget(QLabel('Оригінал:'), 0, Qt.AlignTop)
        orig = QLabel()
        orig.setPixmap(pil_pixmap(self.img.crop(box), self.zoom))
        top.addWidget(orig)
        hint = QLabel('Клік — вибрати, подвійний клік — взяти. Стилі — за групами, у групі '
                      'перші — ті, що вже є в грі.')
        hint.setObjectName('Hint')
        hint.setWordWrap(True)
        top.addWidget(hint, 1)
        root.addLayout(top)

        sa = QScrollArea()
        sa.setWidgetResizable(True)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        sa.setWidget(inner)
        root.addWidget(sa, 1)
        self.cards = []
        row, k = 0, 0                        # заголовок групи — свій рядок
        for group, ns in self.groups:
            if len(self.groups) > 1:
                lab = QLabel(group)
                lab.setObjectName('Section')
                grid.addWidget(lab, row, 0, 1, self.COLS)
                row += 1
            for j, name in enumerate(ns):
                mine = self.styles.get(name, {}).get('мій')
                card = Card(k, name + ('  ★ мій' if mine else ''), color='#ffd970' if mine else None)
                card.clicked.connect(self._select)
                card.double.connect(lambda n: (self._select(n), self._take()))
                grid.addWidget(card, row + j // self.COLS, j % self.COLS)
                self.cards.append(card)
                k += 1
            row += (len(ns) + self.COLS - 1) // self.COLS

        bot = QHBoxLayout()
        b = QPushButton('Новий стиль на основі вибраного…')
        b.clicked.connect(self._new)
        bot.addWidget(b)
        bot.addStretch(1)
        b = QPushButton('Взяти вибраний')
        b.setObjectName('Accent')
        b.clicked.connect(self._take)
        bot.addWidget(b)
        b = QPushButton('Закрити')
        b.clicked.connect(self.close)
        bot.addWidget(b)
        root.addLayout(bot)
        if self.cards:
            self._select(self.sel)
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        box = tuple(self.spec['рамка'])
        br = self.bridge
        for k, name in enumerate(self.names):
            if not self.alive:
                return
            spec = dict(self.spec, стиль=name)
            try:
                im = self.img.copy()
                atl.draw(im, box, spec, self.styles, spec['текст'])
                br.got.emit('card', (k, im.crop(box), None))
            except Exception as ex:                                   # noqa: BLE001
                br.got.emit('card', (k, None, str(ex)))

    def _got(self, _kind, val):
        if not self.alive:
            return
        k, im, err = val
        pic = self.cards[k].pic
        if im is None:
            pic.setText(f'не вдалося: {err}'[:60])
            return
        pic.setPixmap(pil_pixmap(im, self.zoom))

    def _select(self, n):
        self.cards[self.sel].mark(False)
        self.sel = n
        self.cards[n].mark(True)

    def _take(self):
        if self.scan.alive and self.scan.sel is not None:
            self.scan.style_cb.set(self.names[self.sel])
        self.close()

    def _new(self):
        base = self.names[self.sel]
        scan = self.scan
        self.close()
        StyleEditor(scan, base, False).show()

    def closeEvent(self, ev):
        self.alive = False
        super().closeEvent(ev)

    def done(self, r):                      # Esc у діалозі — теж закрити (фоновий прохід зупиниться)
        self.alive = False
        super().done(r)


# ============================================================ свій стиль
class StyleEditor(QDialog):
    """Свій стиль перекладача: за основу — вибраний стиль, змінюються шрифт, товщина,
    колір (чи градієнт), обведення, тінь, нахил, поворот, розрядка й квадратність; решта
    ключів основи (сяйво, друге обведення, розтяг…) переходить як є. Прев'ю — на вибраному
    місці картинки. Зберігається в атлас/стилі.мої.json (atl.save_user_style).

    `scan` — вікно, з якого відкрито («Знайти написи» чи «Написи на картинках»): має
    styles, game, name (файл розмітки), style_sample() і styles_changed(chosen)."""

    SAMPLE = 'Приклад Їжак'

    def __init__(self, scan, base, edit):
        super().__init__(scan)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        from maryskelter import fontlib
        self.scan, self.edit, self.base = scan, edit, base
        self.st = copy.deepcopy(scan.styles.get(base, {}))
        self.st.pop('мій', None)
        self.fonts = fontlib.font_files()
        self.font_dir = {fn: d for d, fn in self.fonts}
        self.gen = 0
        self.alive = True
        self.bridge = Bridge()
        self.bridge.got.connect(self._got)
        self.t_render = QTimer(self, singleShot=True, interval=250, timeout=self._render)
        self.setWindowTitle(('Змінити свій стиль «%s»' if edit else 'Новий стиль на основі «%s»') % base)
        self.resize(980, 720)
        self._build()
        self._changed(0)

    # ------------------------------------------------------------------ вигляд
    def _build(self):
        st = self.st
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        left = QWidget()
        g = QGridLayout(left)
        g.setContentsMargins(0, 0, 0, 0)
        g.setVerticalSpacing(6)
        root.addWidget(left, 0, Qt.AlignTop)
        r = 0

        def row(label, widget):
            nonlocal r
            g.addWidget(QLabel(label), r, 0)
            g.addWidget(widget, r, 1, Qt.AlignLeft)
            r += 1

        def hbox(*ws):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            for x in ws:
                h.addWidget(x) if not isinstance(x, str) else h.addWidget(QLabel(x))
            return w

        self.e_name = QLineEdit(self.base if self.edit else '')
        self.e_name.setMinimumWidth(240)
        self.e_name.setEnabled(not self.edit)
        row('Назва:', self.e_name)
        # опис основи для нового стилю не годиться («Назва району на карті…» — вже не про нього)
        self.e_desc = QLineEdit(st.get('опис', '') if self.edit else '')
        self.e_desc.setMinimumWidth(240)
        if not self.edit:
            self.st.pop('опис', None)
        row('Опис:', self.e_desc)
        # група — щоб свої стилі не губились у списку; можна вписати нову
        groups = [gr for gr, _ns in atl.style_groups(self.scan.styles,
                                                     atl.game_styles(self.scan.styles, self.scan.game))]
        if atl.MY_GROUP not in groups:
            groups.append(atl.MY_GROUP)
        self.c_group = QComboBox()
        self.c_group.setEditable(True)
        self.c_group.addItems(groups)
        self.c_group.setCurrentText((st.get('група') or atl.MY_GROUP) if self.edit else atl.MY_GROUP)
        self.c_group.setMinimumWidth(240)
        row('Група:', self.c_group)

        self.c_font = QComboBox()
        names = [fn for _d, fn in self.fonts]
        if st.get('шрифт', '') not in names:
            names.insert(0, st.get('шрифт', ''))
        self.c_font.addItems(names)
        self.c_font.setCurrentText(st.get('шрифт', ''))
        self.c_font.setMinimumWidth(280)
        self.c_font.setMaxVisibleItems(25)
        self.c_font.currentTextChanged.connect(lambda _t: self._font_changed())
        row('Шрифт:', self.c_font)
        var = st.get('варіація')
        self.weight_touched = False
        self.w_scale = float_slider(100, 900, var.get('wght', 700) if isinstance(var, dict) else 700)
        self.w_scale.setFixedWidth(200)
        self.w_scale.valueChanged.connect(lambda _v: self._touch_weight())
        row('Товщина:', self.w_scale)

        fill = st.get('заливка', '#ffffff')
        self.colors = {'fill1': fill[0] if isinstance(fill, list) else fill,
                       'fill2': fill[-1] if isinstance(fill, list) else fill}
        self.grad = QCheckBox('градієнт до')
        self.grad.setChecked(isinstance(fill, list))
        self.grad.toggled.connect(lambda _on: self._changed())
        row('Колір літер:', hbox(self._color_btn('fill1'), self.grad, self._color_btn('fill2')))

        ob = st.get('обведення') or {}
        self.ob_on = QCheckBox()
        self.ob_on.setChecked(bool(ob))
        self.ob_on.toggled.connect(lambda _on: self._changed())
        self.colors['ob'] = ob.get('колір', '#2e0010')
        self.ob_w = self._spin(0.5, 10, ob.get('товщина', 2))
        row('Обведення:', hbox(self.ob_on, self._color_btn('ob'), ' товщина', self.ob_w))

        sh = st.get('тінь') or {}
        self.sh_on = QCheckBox()
        self.sh_on.setChecked(bool(sh))
        self.sh_on.toggled.connect(lambda _on: self._changed())
        self.colors['sh'] = sh.get('колір') or '#000000c0'
        dx, dy = (sh.get('зсув') or [2, 3])[:2]
        self.sh_dx, self.sh_dy = self._spin(-10, 10, dx), self._spin(-10, 10, dy)
        self.sh_blur = self._spin(0, 6, sh.get('розмиття', 0.5))
        row('Тінь:', hbox(self.sh_on, self._color_btn('sh'), ' зсув x', self.sh_dx, ' y', self.sh_dy,
                          ' розмиття', self.sh_blur))

        self.sliders = {}
        for key, label, lo, hi, dflt in (('нахил', 'Нахил:', -0.2, 0.5, 0), ('поворот', 'Поворот:', -30, 30, 0),
                                         ('розрядка', 'Розрядка:', -0.1, 0.4, 0),
                                         ('квадратність', 'Квадратність:', 0, 1, 0)):
            s = float_slider(lo, hi, st.get(key, dflt))
            s.setFixedWidth(180)
            lab = QLabel(f'{s.fget():.2f}')
            lab.setFixedWidth(46)
            s.valueChanged.connect(lambda _v, s=s, lab=lab: (lab.setText(f'{s.fget():.2f}'), self._changed()))
            row(label, hbox(s, lab))
            self.sliders[key] = s

        bt = QHBoxLayout()
        b = QPushButton('Зберегти стиль')
        b.setObjectName('Accent')
        b.clicked.connect(self._save)
        bt.addWidget(b)
        if self.edit:
            b = QPushButton('Видалити стиль')
            b.clicked.connect(self._delete)
            bt.addWidget(b)
        b = QPushButton('Скасувати')
        b.clicked.connect(self.close)
        bt.addWidget(b)
        bt.addStretch(1)
        g.addLayout(bt, r, 0, 1, 2)
        self.msg = QLabel('')
        self.msg.setObjectName('Warn')
        self.msg.setWordWrap(True)
        self.msg.setMaximumWidth(420)
        g.addWidget(self.msg, r + 1, 0, 1, 2)
        self._font_changed(init=True)

        pv = QVBoxLayout()
        self.prev = []
        for title in ('Оригінал', 'Цим стилем — англійською', 'Цим стилем — українською'):
            lab = QLabel(title)
            lab.setObjectName('Hint')
            pv.addWidget(lab)
            pic = QLabel()
            pic.setStyleSheet(f'background: {CANVAS_BG};')
            pic.hide()
            pv.addWidget(pic, 0, Qt.AlignLeft)
            pv.addSpacing(8)
            self.prev.append(pic)
        pv.addStretch(1)
        root.addLayout(pv, 1)

    def _spin(self, lo, hi, v):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setSingleStep(0.5)
        s.setDecimals(1)
        s.setValue(float(v))
        s.setFixedWidth(84)
        s.valueChanged.connect(lambda _v: self._changed())
        return s

    def _color_btn(self, key):
        b = color_button()
        paint_button(b, self.colors[key])

        def pick():
            old = self.colors[key]
            c = QColorDialog.getColor(QColor(old[:7]), self, 'Колір')
            if c.isValid():
                self.colors[key] = c.name() + old[7:9]           # прозорість (#rrggbbaa) — як була
                paint_button(b, self.colors[key])
                self._changed()
        b.clicked.connect(pick)
        return b

    def _font_changed(self, init=False):
        from maryskelter import fontlib
        fn = self.c_font.currentText()
        d = self.font_dir.get(fn)
        has_w = bool(d and 'Weight' in fontlib.axes(os.path.join(d, fn)))
        self.w_scale.setEnabled(has_w)
        if not init:
            self.weight_touched = True
            self._changed()

    def _touch_weight(self):
        self.weight_touched = True
        self._changed()

    # ------------------------------------------------------------------ стиль
    def style(self):
        from maryskelter import fontlib
        st = dict(self.st)
        fn = self.c_font.currentText()
        d = self.font_dir.get(fn)
        if d:
            fontlib.register(d, fn)
        st['шрифт'] = fn
        if self.weight_touched:
            var = fontlib.variation(os.path.join(d, fn), self.w_scale.fget()) if d else None
            if var:
                st['варіація'] = var
            else:
                st.pop('варіація', None)
        st['заливка'] = [self.colors['fill1'], self.colors['fill2']] if self.grad.isChecked() else self.colors['fill1']
        if self.ob_on.isChecked():
            st['обведення'] = {'колір': self.colors['ob'], 'товщина': float(self.ob_w.value())}
        else:
            st.pop('обведення', None)
        if self.sh_on.isChecked():
            st['тінь'] = {'колір': self.colors['sh'],
                          'зсув': [float(self.sh_dx.value()), float(self.sh_dy.value())],
                          'розмиття': float(self.sh_blur.value())}
        else:
            st.pop('тінь', None)
        for key, s in self.sliders.items():
            val = round(s.fget(), 3)
            if abs(val) < 1e-3 and key not in self.st:
                st.pop(key, None)
            else:
                st[key] = val
        if self.e_desc.text().strip():
            st['опис'] = self.e_desc.text().strip()
        st['група'] = self.c_group.currentText().strip() or atl.MY_GROUP
        st['гра'] = self.scan.game
        return st

    def _changed(self, delay=250):
        self.t_render.start(delay)

    def _render(self):
        self.t_render.stop()
        sample = self.scan.style_sample()
        if sample is None:
            return
        self.gen += 1
        img, box, spec, texts = sample
        gen, st = self.gen, self.style()
        styles = dict(self.scan.styles, __пробний=st)
        spec = dict(spec, стиль='__пробний')
        texts = [t or self.SAMPLE for t in texts]
        br = self.bridge

        def work():
            out = [img.crop(box)]
            warn = []
            for text in texts:
                try:
                    im = img.copy()
                    warn += atl.draw(im, box, spec, styles, text)
                    out.append(im.crop(box))
                except Exception as ex:                               # noqa: BLE001
                    out.append(None)
                    warn.append(f'не вдалося: {ex}')
            br.got.emit('prev', (gen, out, warn))
        threading.Thread(target=work, daemon=True).start()

    def _got(self, _kind, val):
        if not self.alive:
            return
        gen, ims, warn = val
        if gen != self.gen:
            return
        for lab, im in zip(self.prev, ims):
            if im is None:
                lab.clear()
                lab.hide()
                continue
            lab.setPixmap(pil_pixmap(im, min(3.0, 460 / max(1, im.width))))
            lab.show()
        self.msg.setText('\n'.join(dict.fromkeys(w for w in warn if 'не вдалося' in w)))

    # ------------------------------------------------------------------ збереження
    def _save(self):
        name = self.e_name.text().strip()
        if not name:
            self.msg.setText('Дай стилю назву.')
            return
        if not self.edit and name in self.scan.styles:
            self.msg.setText('Стиль з такою назвою вже є — вибери іншу назву.')
            return
        if name.startswith('_'):
            self.msg.setText('Назва не може починатися з «_».')
            return
        from maryskelter import fontlib
        st = self.style()
        fontlib.adopt(st['шрифт'])                   # шрифт з бібліотеки — у атлас/шрифти/
        atl.save_user_style(name, st)
        self.scan.styles_changed(chosen=name)
        self.close()

    def _users(self, name):
        return sum(1 for m in atl.load_marks(self.scan.name).values()
                   for s in m.get('кадри', {}).values() if s.get('стиль') == name)

    def _delete(self):
        name = self.base
        n = self._users(name)
        if n:
            self.msg.setText(f'Стиль ужито в написах: {n}. Спершу вибери для них інший стиль.')
            return
        if QMessageBox.question(self, 'Видалити стиль', f'Видалити свій стиль «{name}»?') != QMessageBox.Yes:
            return
        atl.save_user_style(name, None)
        self.scan.styles_changed(chosen='')
        self.close()

    def closeEvent(self, ev):
        self.alive = False
        super().closeEvent(ev)

    def done(self, r):                      # Esc у діалозі — теж закрити (фоновий прохід зупиниться)
        self.alive = False
        super().done(r)
