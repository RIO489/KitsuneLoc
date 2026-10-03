# -*- coding: utf-8 -*-
"""Теми для Qt: ті самі дані, що й для Tk (themes.THEMES), тут — як їх показати в Qt.

На відміну від Tk, Qt уміє прозорість: панелі напівпрозорі, під ними — тло вікна
(Backdrop): нічне небо (themes.sky) і сяйво навколо панелей; персонажі гри (Overlay) —
поверх панелей, клацання проходять крізь них.
"""
import themes
from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QWidget


def palette(name):
    return themes.get(name)


def rgba(hexcolor, alpha):
    c = QColor(hexcolor)
    return f'rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})'


def qss(name):
    """Таблиця стилів Qt для теми. З прикрасами — панелі й поля напівпрозорі."""
    t = palette(name)
    deco = bool(t.get('deco'))
    bg, fg, panel, dim = t['bg'], t['fg'], t['panel'], t['dim']
    field = t.get('field', panel)
    btn, hi = t.get('button', panel), t.get('button_hi', t.get('button', panel))
    border, sel, acc = t.get('border', '#888888'), t.get('sel', t['accent']), t['accent']
    head, link = t.get('head', btn), t.get('link', acc)
    a = (lambda c, al: rgba(c, al)) if deco else (lambda c, _al: c)
    glow = t['deco']['glow'] if deco else border
    return f"""
* {{ font-family: 'Segoe UI'; font-size: 10pt; color: {fg}; }}
QMainWindow, QDialog {{ background: {bg}; }}
#Backdrop {{ background: {'transparent' if deco else bg}; }}
#Panel {{ background: {a(panel, 175)}; border: 1px solid {a(glow if deco else border, 210)};
          border-radius: {10 if deco else 4}px; }}
#Bar {{ background: transparent; }}
QLabel {{ background: transparent; }}
QLabel#Hint {{ color: {dim}; font-size: 8.5pt; }}
QLabel#Head {{ font-size: 12pt; font-weight: bold; color: {link if deco else fg}; }}
QLabel#Section {{ color: {link if deco else fg}; font-weight: bold; }}
QLabel#Warn {{ color: {t['warn']}; }}
QTreeView, QTableView, QListView {{ background: {a(panel, 120)}; alternate-background-color: {a(field, 120)};
    border: none; selection-background-color: {sel}; selection-color: #ffffff; outline: 0; }}
QTreeView::item, QTableView::item {{ padding: 2px 4px; }}
QTreeView::item:selected, QTableView::item:selected {{ background: {a(sel, 230)}; color: #ffffff; }}
QTreeView::item:hover, QTableView::item:hover {{ background: {a(hi, 140)}; }}
QHeaderView::section {{ background: {a(head, 200)}; color: {link}; border: none;
    border-bottom: 1px solid {a(border, 200)}; padding: 4px 6px; font-weight: bold; }}
QTableCornerButton::section {{ background: transparent; border: none; }}
QPlainTextEdit, QTextEdit, QLineEdit, QSpinBox, QComboBox {{ background: {a(field, 190)};
    border: 1px solid {a(border, 160)}; border-radius: 6px; padding: 4px; selection-background-color: {sel}; }}
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus {{ border: 1px solid {acc}; }}
QPlainTextEdit[readOnly="true"] {{ background: {a(field, 90)}; border: 1px solid transparent; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {field}; selection-background-color: {sel}; border: 1px solid {border}; }}
QPushButton {{ background: {a(btn, 210)}; border: 1px solid {a(border, 220)}; border-radius: 7px;
    padding: 5px 14px; }}
QPushButton:hover {{ background: {a(hi, 230)}; border: 1px solid {acc}; }}
QPushButton:pressed {{ background: {sel}; }}
QPushButton:disabled {{ color: {dim}; background: {a(btn, 110)}; border: 1px solid {a(border, 90)}; }}
QPushButton#Term {{ text-align: left; padding: 3px 10px; }}
QPushButton#Small {{ padding: 1px 8px; }}
QPushButton#Accent {{ background: {a(sel, 230)}; border: 1px solid {acc}; color: #ffffff; }}
QPushButton#Accent:hover {{ background: {acc}; }}
QCheckBox {{ spacing: 6px; background: transparent; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {border}; border-radius: 4px;
    background: {a(field, 200)}; }}
QCheckBox::indicator:checked {{ background: {acc}; border: 1px solid {acc}; }}
QRadioButton {{ spacing: 6px; background: transparent; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {border}; border-radius: 8px;
    background: {a(field, 200)}; }}
QRadioButton::indicator:checked {{ background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
    stop:0 #ffffff, stop:0.35 {acc}, stop:0.45 {acc}, stop:0.55 {a(field, 200)}); border: 1px solid {acc}; }}
QRadioButton:disabled, QCheckBox:disabled {{ color: {dim}; }}
QProgressBar {{ background: {a(field, 160)}; border: 1px solid {a(border, 140)}; border-radius: 4px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {sel}, stop:1 {acc});
    border-radius: 3px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {a(border, 200)}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {acc}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {a(border, 200)}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}
QMenu {{ background: {field}; border: 1px solid {border}; padding: 4px; }}
QMenu::item {{ padding: 4px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {sel}; color: #ffffff; }}
QToolTip {{ background: {field}; color: {fg}; border: 1px solid {acc}; }}
"""


def _qimage(pil):
    pil = pil.convert('RGBA')
    data = pil.tobytes('raw', 'RGBA')
    return QImage(data, pil.width, pil.height, QImage.Format_RGBA8888).copy()


def pixmap(pil):
    return QPixmap.fromImage(_qimage(pil))


class Backdrop(QWidget):
    """Тло вікна: небо + сяйво навколо зареєстрованих панелей (glow(w)). Без прикрас — колір."""

    def __init__(self, theme_name, parent=None):
        super().__init__(parent)
        self.setObjectName('Backdrop')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.t = palette(theme_name)
        self.deco = self.t.get('deco')
        self.panels = []
        self._sky = None
        self._timer = QTimer(self, singleShot=True, interval=120, timeout=self._render_sky)

    def glow(self, w):
        self.panels.append(w)

    def retheme(self, theme_name):
        """Інша тема на ходу (Qt це вміє — на відміну від Tk): нове небо або суцільний колір."""
        self.t = palette(theme_name)
        self.deco = self.t.get('deco')
        self._sky = None
        if self.deco:
            self._timer.start()
        self.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.deco:
            self._timer.start()             # небо — коли зміна розміру зупинилась

    def _render_sky(self):
        if self.width() > 20 and self.height() > 20:
            self._sky = pixmap(themes.sky(self.width(), self.height(), self.deco))
            self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        if not self.deco:
            p.fillRect(self.rect(), QColor(self.t['bg']))
            return
        if self._sky is not None:
            p.drawPixmap(0, 0, self._sky)   # (поки тягнуть край — старе небо, розтягувати не треба)
        else:
            p.fillRect(self.rect(), QColor(self.deco['sky'][0]))
        p.setRenderHint(QPainter.Antialiasing)
        glow = QColor(self.deco['glow'])
        for w in self.panels:
            if not w.isVisible():
                continue
            r = QRectF(w.mapTo(self, w.rect().topLeft()), w.size())
            for i, alpha in ((7, 18), (5, 30), (3, 55), (1.5, 90)):   # сяйво — кілька прозорих обвідок
                glow.setAlpha(alpha)
                p.setPen(QPen(glow, i * 2))
                p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 11, 11)


class Overlay(QLabel):
    """Персонаж гри поверх панелей: прозорий для миші (клацання проходять крізь нього), у куті
    вікна. Курсор над самим персонажем (не над порожнім тлом картинки) — плавно стає
    напівпрозорим, щоб було видно, що під ним (прохання перекладача)."""
    FADED = 0.18

    def __init__(self, parent, pil, corner):
        from PySide6.QtCore import QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        super().__init__(parent)
        self.corner = corner
        self.img = _qimage(pil)
        self.setPixmap(QPixmap.fromImage(self.img))
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(pil.width, pil.height)
        self.fx = QGraphicsOpacityEffect(self)
        self.fx.setOpacity(1.0)
        self.setGraphicsEffect(self.fx)
        self.anim = QPropertyAnimation(self.fx, b'opacity', self)
        self.anim.setDuration(180)
        self.faded = False
        # подій миші персонаж не отримує (прозорий для неї) — дивимось, де курсор, самі
        self.timer = QTimer(self, interval=90, timeout=self._watch)
        self.timer.start()
        self.raise_()

    def _over(self):
        from PySide6.QtGui import QCursor
        if not self.isVisible() or not self.window().isActiveWindow() and not self.window().underMouse():
            return False
        p = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(p):
            return False
        return self.img.pixelColor(p.x(), p.y()).alpha() > 24

    def _watch(self):
        over = self._over()
        if over != self.faded:
            self.faded = over
            self.anim.stop()
            self.anim.setStartValue(self.fx.opacity())
            self.anim.setEndValue(self.FADED if over else 1.0)
            self.anim.start()

    def place(self, x, y):
        self.move(int(x), int(y))
        self.raise_()


def portraits(game, n=2, height=210):
    """До n різних персонажів гри (з кешу чібі, chibi.py) -> [PIL RGBA]."""
    import os, random
    try:
        import chibi
        from PIL import Image
        d = os.path.join(chibi.DIR, game)
        files = sorted(f for f in os.listdir(d) if f.lower().endswith('.png')) if os.path.isdir(d) else []
    except Exception:                                   # noqa: BLE001
        return []
    out = []
    for f in random.sample(files, min(n, len(files))):
        im = Image.open(os.path.join(d, f)).convert('RGBA')
        k = height / im.height
        out.append(im.resize((max(1, int(im.width * k)), height), Image.LANCZOS))
    return out
