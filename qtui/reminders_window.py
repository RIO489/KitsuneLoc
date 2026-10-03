# -*- coding: utf-8 -*-
"""Вікно «Нагадування» на Qt — те саме, що reminders_window.py (Tk), інший вигляд.

Ліворуч — розділи (reminders.SECTIONS) і записи (новіші вгорі), праворуч — вибраний запис:
знімок з рамкою навколо тексту (якщо є; подвійний клік — перегляд з наближенням), що це за
рядок, примітка (зберігається сама), «Відкрити рядок у редакторі», «Видалити». Нагадування —
окремо для кожної гри (Переклад\\<гра>\\нагадування).
"""
import os

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

import reminders
from qtui import theme as qtheme

PIC_W = 640
NOTE_MS = 700
BOX = '#ffcc33'


class PicLabel(QLabel):
    """Знімок запису; подвійний клік — перегляд з наближенням."""

    def __init__(self, on_double):
        super().__init__()
        self.on_double = on_double

    def mouseDoubleClickEvent(self, ev):
        self.on_double()


class RemindersWindow(QMainWindow):
    def __init__(self, app, xl, title):
        super().__init__()
        self.app, self.xl = app, xl
        self.theme_name = getattr(app, 'theme', 'stars')
        self.setWindowTitle(f'Нагадування — {title}')
        self.resize(1100, 680)
        self.setMinimumSize(760, 440)
        self.cur = None
        self.store = None
        self._loading = False
        self.pic_path, self.pic_box = None, None
        self.t_note = QTimer(self, singleShot=True, interval=NOTE_MS, timeout=self._flush_note)
        self._build()
        self.reload()
        self.show()

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
        root.setContentsMargins(14, 12, 14, 8)
        root.setSpacing(8)
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(12)
        split.splitterMoved.connect(lambda *_a: self.back.update())
        root.addWidget(split, 1)

        left, ll = self._panel()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(lambda *_a: self._select())
        QShortcut(QKeySequence('Delete'), self.tree, activated=self._delete, context=Qt.WidgetShortcut)
        ll.addWidget(self.tree)
        split.addWidget(left)

        right, rl = self._panel()
        self.head = QLabel('')
        self.head.setObjectName('Head')
        self.head.setWordWrap(True)
        self.head.setMaximumWidth(PIC_W + 40)
        rl.addWidget(self.head)
        self.info = QLabel('')
        self.info.setObjectName('Hint')
        rl.addWidget(self.info)
        self.pic = PicLabel(self._zoom)
        self.pic.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        rl.addWidget(self.pic)
        row = QHBoxLayout()
        row.addWidget(QLabel('Примітка:'))
        self.note = QLineEdit()
        self.note.setMaximumWidth(PIC_W - 60)
        self.note.textEdited.connect(lambda _t: self._note_changed())
        row.addWidget(self.note, 1)
        row.addStretch(0)
        rl.addLayout(row)
        row = QHBoxLayout()
        self.b_open = QPushButton('Відкрити рядок у редакторі')
        self.b_open.clicked.connect(self._open)
        row.addWidget(self.b_open)
        self.b_del = QPushButton('Видалити нагадування')
        self.b_del.clicked.connect(self._delete)
        row.addWidget(self.b_del)
        row.addStretch(1)
        rl.addLayout(row)
        rl.addStretch(1)
        split.addWidget(right)
        split.setSizes([360, 720])
        split.setStretchFactor(1, 2)

        self.hint = QLabel('')
        self.hint.setObjectName('Hint')
        root.addWidget(self.hint)

    # ------------------------------------------------------------ дані
    def reload(self, xl=None, title=None):
        """Перечитати нагадування (нове додано з іншого вікна, або перемкнули гру)."""
        if xl:
            self.xl = xl
        if title:
            self.setWindowTitle(f'Нагадування — {title}')
        self._flush_note()
        keep = self.cur
        self.store = reminders.Store(self.xl)
        self.tree.blockSignals(True)
        self.tree.clear()
        found = None
        for sec, items in self.store.by_section().items():
            name = reminders.SECTIONS.get(sec, sec or 'Інше')
            node = QTreeWidgetItem(self.tree, [f'{name} ({len(items)})'])
            node.setData(0, Qt.UserRole, None)
            for x in items:
                when = x.get('час', '')
                when = f'{when[8:10]}.{when[5:7]} {when[11:16]}' if len(when) >= 16 else when
                it = QTreeWidgetItem(node, [f'{when} · {" ".join(x.get("текст", "").split())[:70]}'])
                it.setData(0, Qt.UserRole, x['id'])
                if x['id'] == keep:
                    found = it
            node.setExpanded(True)
        self.tree.blockSignals(False)
        n = len(self.store.items)
        self.hint.setText('Нагадувань: %d. Додати: «Гра на екрані» → права кнопка по рядку журналу → '
                          '«Зберегти в нагадування».' % n)
        if found is not None:
            self.tree.setCurrentItem(found)
            self._show(self.store.get(keep))
        else:
            self._show(None)

    def _select(self):
        it = self.tree.currentItem()
        self._flush_note()
        rid = it.data(0, Qt.UserRole) if it is not None else None
        self._show(self.store.get(rid) if rid else None)

    def _show(self, item):
        self.cur = item['id'] if item else None
        for w in (self.b_del, self.note):
            w.setEnabled(bool(item))
        self.b_open.setEnabled(bool(item and item.get('ключ')))
        self._loading = True
        self.note.setText(item.get('примітка', '') if item else '')
        self._loading = False
        self.pic.setCursor(Qt.ArrowCursor)
        if not item:
            self.head.setText('')
            self.info.setText('')
            self.pic_path, self.pic_box = None, None
            self.pic.setPixmap(QPixmap())
            self.pic.setText('Вибери нагадування ліворуч.')
            return
        self.head.setText(' '.join(item.get('текст', '').split()))
        when = item.get('час', '').replace('T', ' ')
        self.info.setText(' · '.join(x for x in (item.get('стан'), item.get('хто'), when) if x))
        p = self.store.file(item)
        self.pic_path, self.pic_box = p, item.get('рамка')
        self.pic.setPixmap(QPixmap())
        if not p:
            self.pic.setText('(без знімка)')
            return
        img = QPixmap(p)
        if img.isNull():
            self.pic.setText('(знімок не читається)')
            return
        k = min(1.0, PIC_W / img.width())
        im = img.scaled(max(1, round(img.width() * k)), max(1, round(img.height() * k)),
                        Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        box = item.get('рамка')
        if box:
            pt = QPainter(im)
            pt.setPen(QPen(QColor(BOX), 3))
            pt.drawRect(QRectF(QPointF(box[0] * k - 3, box[1] * k - 3), QPointF(box[2] * k + 3, box[3] * k + 3)))
            pt.end()
        self.pic.setText('')
        self.pic.setPixmap(im)
        self.pic.setCursor(Qt.PointingHandCursor)

    def _zoom(self):
        """Знімок у вікні перегляду: наблизити, розгледіти напис."""
        if not self.pic_path or not os.path.exists(self.pic_path):
            return
        from qtui import imageview
        b = self.pic_box
        colors = self.app.colors() if hasattr(self.app, 'colors') else qtheme.palette(self.theme_name)
        self.viewer = imageview.ImageView(self, self.pic_path, title=self.head.text()[:80] or 'Знімок',
                                          boxes=[(*b, BOX)] if b else (), colors=colors)

    # ------------------------------------------------------------ дії
    def _note_changed(self):
        if self._loading or not self.cur:
            return
        self.t_note.start()                     # (пере)запуск: пишемо, коли перестали друкувати

    def _flush_note(self):
        self.t_note.stop()
        if self.cur and self.store is not None:
            try:
                self.store.set_note(self.cur, self.note.text())
            except OSError:
                pass

    def _open(self):
        item = self.cur and self.store.get(self.cur)
        if item and item.get('ключ'):
            opener = getattr(self.app, 'open_editor', None)
            if opener:
                opener(goto=item['ключ'])

    def _delete(self):
        item = self.cur and self.store.get(self.cur)
        if not item:
            return
        if QMessageBox.question(self, 'Нагадування', 'Видалити це нагадування (разом зі знімком)?') \
                != QMessageBox.Yes:
            return
        self.store.delete(item['id'])
        self.cur = None
        self.reload()

    # для головного вікна (назви — як у Tk-вікна)
    def winfo_exists(self):
        return self.isVisible()

    def lift(self):
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, ev):
        self._flush_note()
        if getattr(self.app, 'reminders_win', None) is self:
            self.app.reminders_win = None
        super().closeEvent(ev)
