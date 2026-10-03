# -*- coding: utf-8 -*-
"""Перегляд знімка з наближенням (Qt) — те саме, що imageview.py (Tk).

Спільне вікно для «Нагадувань» і «Гри на екрані»: коліщатко — наближення відносно
курсора, перетягування лівою кнопкою — зсув, «Вписати» / «100%», «Відкрити у Windows»
(програма перегляду фото) і «Показати в теці». Малюється лише видима частина
(обрізати -> масштабувати під полотно), тож і ×8 знімка 1920×1080 не займає сотні МБ.
"""
import os, subprocess

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from PIL import Image


def qimage(pil):
    """PIL -> QImage (копія: дані PIL можуть зникнути)."""
    pil = pil.convert('RGBA')
    return QImage(pil.tobytes('raw', 'RGBA'), pil.width, pil.height, QImage.Format_RGBA8888).copy()


class Canvas(QWidget):
    """Полотно: видима частина знімка в масштабі z, зсув (ox, oy) — у пікселях знімка."""

    def __init__(self, view):
        super().__init__()
        self.v = view
        self.setCursor(Qt.SizeAllCursor)
        self.setMinimumSize(200, 150)
        self._from = None

    def paintEvent(self, ev):
        v = self.v
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(v.c.get('logbg', '#202020')))
        cw, ch = max(1, self.width()), max(1, self.height())
        W, H = v.img.width(), v.img.height()
        # видима частина знімка (у його пікселях), обрізана по краях
        x0, y0 = max(0.0, v.ox), max(0.0, v.oy)
        x1, y1 = min(W, v.ox + cw / v.z), min(H, v.oy + ch / v.z)
        if x1 <= x0 or y1 <= y0:
            return
        src = QRectF(int(x0), int(y0), int(x1 + 0.999) - int(x0), int(y1 + 0.999) - int(y0))
        dst = QRectF((src.x() - v.ox) * v.z, (src.y() - v.oy) * v.z, src.width() * v.z, src.height() * v.z)
        # від ×2 — чіткі пікселі (як NEAREST у Tk), менше — згладжено
        p.setRenderHint(QPainter.SmoothPixmapTransform, v.z < 2)
        p.drawImage(dst, v.img, src)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if not self.v._fitted:
            self.v.fit()

    def wheelEvent(self, ev):
        pos = ev.position()
        self.v._zoom_at(1.25 if ev.angleDelta().y() > 0 else 0.8, pos.x(), pos.y())

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            p = ev.position()
            self._from = (p.x(), p.y(), self.v.ox, self.v.oy)

    def mouseMoveEvent(self, ev):
        if self._from and ev.buttons() & Qt.LeftButton:
            x, y, ox, oy = self._from
            p = ev.position()
            self.v.ox, self.v.oy = ox - (p.x() - x) / self.v.z, oy - (p.y() - y) / self.v.z
            self.v.draw()

    def mouseReleaseEvent(self, ev):
        self._from = None

    def mouseDoubleClickEvent(self, ev):
        self.v.fit()


class ImageView(QWidget):
    def __init__(self, parent, img, title='Знімок', path=None, boxes=(), colors=None):
        """img — PIL Image (або шлях); boxes — [(x0, y0, x1, y1, колір)] у пікселях знімка."""
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        if isinstance(img, str):
            path = path or img
            img = Image.open(img)
        self.img = qimage(img.convert('RGB'))
        if boxes:
            p = QPainter(self.img)
            for x0, y0, x1, y1, col in boxes:
                p.setPen(QPen(QColor(col), 3))
                p.drawRect(QRectF(QPointF(x0 - 3, y0 - 3), QPointF(x1 + 3, y1 + 3)))
            p.end()
        self.path = path
        self.c = colors or {}
        self.setObjectName('Viewer')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f'#Viewer {{ background: {self.c.get("bg", "#1c1c1c")}; }}')
        self.setWindowTitle(title)
        scr = self.screen().availableGeometry() if self.screen() else None
        sw, sh = (scr.width(), scr.height()) if scr else (1600, 1000)
        self.resize(min(1400, sw - 120), min(900, sh - 160))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 8)
        bar = QHBoxLayout()
        b = QPushButton('Вписати')
        b.clicked.connect(self.fit)
        bar.addWidget(b)
        b = QPushButton('100%')
        b.clicked.connect(lambda: self.zoom_to(1.0))
        bar.addWidget(b)
        self.info = QLabel('')
        self.info.setObjectName('Hint')
        bar.addWidget(self.info, 1)
        if path:
            b = QPushButton('Відкрити у Windows')
            b.clicked.connect(self.open_ext)
            bar.addWidget(b)
            b = QPushButton('Показати в теці')
            b.clicked.connect(self.reveal)
            bar.addWidget(b)
        lay.addLayout(bar)
        self.cv = Canvas(self)
        lay.addWidget(self.cv, 1)
        self.z, self.ox, self.oy = 1.0, 0.0, 0.0
        self._fitted = False
        for keys, f in ((('+', '=', 'Ctrl++'), 1.25), (('-', 'Ctrl+-'), 0.8)):
            for k in keys:
                QShortcut(QKeySequence(k), self,
                          activated=lambda f=f: self._zoom_at(f, self.cv.width() / 2, self.cv.height() / 2))
        QShortcut(QKeySequence('Escape'), self, activated=self.close)
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ масштаб і зсув
    def fit(self):
        cw, ch = max(1, self.cv.width()), max(1, self.cv.height())
        W, H = self.img.width(), self.img.height()
        self.z = min(cw / W, ch / H)
        self.ox = (W - cw / self.z) / 2
        self.oy = (H - ch / self.z) / 2
        self._fitted = True
        self.draw()

    def zoom_to(self, z):
        self._zoom_at(z / self.z, self.cv.width() / 2, self.cv.height() / 2)

    def _zoom_at(self, f, x, y):
        z = max(0.05, min(8.0, self.z * f))
        ix, iy = self.ox + x / self.z, self.oy + y / self.z     # точка знімка під курсором лишається на місці
        self.z = z
        self.ox, self.oy = ix - x / z, iy - y / z
        self.draw()

    def draw(self):
        self.info.setText(f'{round(self.z * 100)}%  ·  коліщатко — ближче/далі, тягни мишею, подвійний клік — вписати')
        self.cv.update()

    # ------------------------------------------------------------ файл
    def open_ext(self):
        try:
            os.startfile(self.path)
        except OSError:
            pass

    def reveal(self):
        try:
            subprocess.Popen(['explorer', '/select,', os.path.normpath(self.path)])
        except OSError:
            pass
