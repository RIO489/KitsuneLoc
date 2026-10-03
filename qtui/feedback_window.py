# -*- coding: utf-8 -*-
"""Вікно «Повідомити про проблему» на Qt — те саме, що feedback_window.py (Tk), інший вигляд.

Програма сама додає версію, кінець логу й рядок, з якого відкрито вікно (редактор,
попередження в лозі, вікно написів). Скриншоти — клацнути знімок зі Steam / Win+PrtSc,
вставити з буфера (Ctrl+V за будь-якої розкладки) чи вибрати файл. Надсилання — feedback.py
(черга: без інтернету звернення піде пізніше).

Від головного вікна: GAMES, VERSION, game.get(), theme, colors(), say(), q (('call', fn) —
результат надсилання з потоку), steam_libraries() або L['steam_libraries'], say_sent(link)
(якщо немає — те саме робить _say_sent нижче через q ('act', …)).
"""
import os, threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal, QEvent
from PySide6.QtGui import QFont, QPainter, QColor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QVBoxLayout,
                               QWidget)

import feedback
from qtui import theme as qtheme

THUMB = 150


class Bridge(QObject):
    """Фоновий потік -> вікно (сигнали Qt потокобезпечні)."""
    got = Signal(str, object)


def _alive(w):
    try:
        import shiboken6
        return shiboken6.isValid(w)
    except Exception:                                       # noqa: BLE001
        return True


def _steam_libraries(app):
    f = getattr(app, 'steam_libraries', None)
    if f is None:
        f = getattr(app, 'L', {}).get('steam_libraries')
    try:
        return f() if f else []
    except Exception:                                       # noqa: BLE001
        return []


def _say_sent(app, link):
    """Те саме, що App.say_sent у Tk: посилання на тему — клікабельним рядком журналу."""
    if hasattr(app, 'say_sent'):
        app.say_sent(link)
    elif link:
        import webbrowser
        app.q.put(('act', ('Звернення надіслано ✓ — відкрити тему в Telegram', 'ok',
                           lambda: webbrowser.open(link))))
    else:
        app.say('Звернення надіслано ✓', 'ok')


def _is_paste(ev):
    """Ctrl+V за будь-якої розкладки (віртуальна клавіша V = 0x56)."""
    return (ev.type() == QEvent.KeyPress and ev.modifiers() & Qt.ControlModifier
            and (ev.nativeVirtualKey() == 0x56 or ev.key() == Qt.Key_V))


class Grip(QWidget):
    """Ручка під полем (прохання перекладача): тягни — вище/нижче поле, а вікно росте разом
    з ним, щоб знімки й кнопки не сховались за край екрана."""

    def __init__(self, win, edit, lines, color):
        super().__init__()
        self.win, self.edit, self.color = win, edit, QColor(color)
        self.lines = lines
        self.setFixedHeight(9)
        self.setCursor(Qt.SizeVerCursor)
        self.st = None
        self.apply(lines)

    def line(self):
        return max(8, self.edit.fontMetrics().lineSpacing())

    def apply(self, lines):
        self.lines = lines
        e = self.edit
        e.setFixedHeight(int(lines * self.line() + 2 * e.frameWidth() + e.document().documentMargin() * 2 + 10))

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect((self.width() - 70) // 2, 3, 70, 3, self.color)

    def mousePressEvent(self, ev):
        self.st = (ev.globalPosition().y(), self.lines)

    def mouseMoveEvent(self, ev):
        if self.st is None:
            return
        y0, h0 = self.st
        h = max(2, min(40, h0 + round((ev.globalPosition().y() - y0) / self.line())))
        if h == self.lines:
            return
        old = self.lines
        self.apply(h)
        # вікно — на ту саму різницю, але не вище за екран
        scr = self.win.screen().availableGeometry().height() if self.win.screen() else 1000
        H = max(self.win.minimumHeight(), min(scr - 80, self.win.height() + (h - old) * self.line()))
        self.win.resize(self.win.width(), int(H))

    def mouseReleaseEvent(self, _ev):
        self.st = None


class Shot(QFrame):
    """Мініатюра знімка: клік — вибрати/зняти, правий клік — меню."""

    def __init__(self, win, src, on):
        super().__init__()
        from PIL import Image
        im = src if not isinstance(src, str) else Image.open(src)
        th = im.convert('RGB')
        th.thumbnail((THUMB * 16 // 9, THUMB))
        self.win, self.src, self.on = win, src, on
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(1)
        pic = QLabel()
        pic.setPixmap(qtheme.pixmap(th))
        lay.addWidget(pic)
        name = os.path.basename(src) if isinstance(src, str) else 'з буфера / прев\'ю'
        cap = QLabel(name[:28])
        cap.setObjectName('Hint')
        lay.addWidget(cap)
        self.setCursor(Qt.PointingHandCursor)
        self.mark()

    def mark(self):
        c = self.win.c
        self.setStyleSheet(f'Shot {{ border: 3px solid {c["accent"] if self.on else "transparent"}; '
                           'border-radius: 4px; background: transparent; }')

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.win._toggle(self)
        elif ev.button() == Qt.RightButton:
            self.win._shot_menu(ev.globalPosition().toPoint(), self)


class Strip(QScrollArea):
    """Смуга знімків: коліщатко крутить вбік; правий клік по порожньому — меню."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(THUMB + 46)
        self.inner = QWidget()
        self.row = QHBoxLayout(self.inner)
        self.row.setContentsMargins(4, 4, 4, 4)
        self.row.setSpacing(8)
        self.row.addStretch(1)
        self.setWidget(self.inner)

    def wheelEvent(self, ev):
        sb = self.horizontalScrollBar()
        sb.setValue(sb.value() - ev.angleDelta().y())

    def mousePressEvent(self, ev):
        if ev.button() == Qt.RightButton:
            self.win._shot_menu(ev.globalPosition().toPoint())


class FeedbackWindow(QDialog):
    """ctx (усе опційне): game, kind, row = {source, id, src, tr, warn, where}, where,
    image — PIL-картинка (прев'ю напису), яку додати до знімків."""

    def __init__(self, app, ctx=None):
        super().__init__(app)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.app, self.ctx = app, ctx or {}
        self.c = app.colors()
        self.setWindowTitle('Повідомити про проблему')
        self.resize(760, 780)
        self.setMinimumSize(640, 600)
        self.shots = []          # [Shot] — src, on
        self.sending = False
        self._sent = False
        self._paste_due = False
        self._build()
        self._fill_shots()
        self._restore_draft()
        self._key_state()
        self._validate()
        # Ctrl+V за будь-якої розкладки: картинку з буфера — у знімки; текст у полі вставляється, як завжди
        for w in [self] + self.findChildren(QWidget):
            w.installEventFilter(self)
        self.what.setFocus()

    def eventFilter(self, obj, ev):
        # неприйняте натискання йде й батькам (у кожного той самий фільтр) — вставляємо раз
        if _is_paste(ev) and not self._paste_due:
            self._paste_due = True
            QTimer.singleShot(0, self._paste)
        return False

    # ------------------------------------------------------------------ вигляд
    def _hint(self, text):
        h = QLabel(text)
        h.setObjectName('Hint')
        h.setWordWrap(True)
        return h

    def _build(self):
        games = self.app.GAMES
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.back = qtheme.Backdrop(getattr(self.app, 'theme', 'stars'))
        outer.addWidget(self.back)
        root = QVBoxLayout(self.back)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)
        panel = QFrame()
        panel.setObjectName('Panel')
        self.back.glow(panel)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(6)
        root.addWidget(panel, 1)

        lay.addWidget(self._hint('Пиши як є — версію програми, лог і рядок програма додасть сама.'))

        row = QHBoxLayout()
        row.addWidget(QLabel('Що сталося:'))
        self.kind_btns = {}
        grp = QButtonGroup(self)
        k0 = self.ctx.get('kind', 'текст')
        for k, (label, _c) in feedback.KINDS.items():
            rb = QRadioButton(label)
            rb.setChecked(k == k0)
            grp.addButton(rb)
            self.kind_btns[k] = rb
            row.addWidget(rb)
        row.addStretch(1)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel('Гра:'))
        self.game_names = {g['title']: k for k, g in games.items()}
        self.game_names['Програма загалом'] = ''
        g0 = self.ctx.get('game', self.app.game.get())
        self.game = QComboBox()
        self.game.addItems(list(self.game_names))
        self.game.setCurrentText(games[g0]['title'] if g0 in games else 'Програма загалом')
        self.game.setMinimumWidth(240)
        self.game.currentTextChanged.connect(lambda _t: self._fill_shots())
        row.addWidget(self.game)
        row.addStretch(1)
        lay.addLayout(row)

        rw = self.ctx.get('row')
        self.with_row = QCheckBox('Додати цей рядок до звернення')
        self.with_row.setChecked(bool(rw))
        if rw:
            sec = QLabel('Рядок')
            sec.setObjectName('Section')
            lay.addWidget(sec)
            txt = f'{rw.get("source", "")} [{rw.get("id", "")}]'
            for label, key in (('Оригінал', 'src'), ('Переклад', 'tr'), ('Попередження', 'warn')):
                if rw.get(key):
                    v = ' '.join(str(rw[key]).split())
                    txt += f'\n{label}: {v[:200]}{"…" if len(v) > 200 else ""}'
            lab = QLabel(txt)
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(lab)
            lay.addWidget(self.with_row)

        row = QHBoxLayout()
        row.addWidget(QLabel('Твоє ім\'я або нік:'))
        self.nick = QLineEdit(feedback.load_nick())
        self.nick.setFixedWidth(200)
        row.addWidget(self.nick)
        h = QLabel('(необов\'язково — щоб знати, кому дякувати)')
        h.setObjectName('Hint')
        row.addWidget(h)
        row.addStretch(1)
        lay.addLayout(row)

        self.what = self._text(lay, 'Що не так? (обов\'язково)', 5)
        self.what.textChanged.connect(self._validate)
        self.how = self._text(lay, 'Як мало бути? (якщо знаєш)', 3)

        row = QHBoxLayout()
        row.addWidget(QLabel('Де в грі (екран, меню, сцена):'))
        self.where = QLineEdit(self.ctx.get('where', ''))
        row.addWidget(self.where, 1)
        lay.addLayout(row)

        # --- знімки ---------------------------------------------------------
        sec = QLabel('Скриншоти — клацни, щоб додати')
        sec.setObjectName('Section')
        lay.addWidget(sec)
        row = QHBoxLayout()
        for text, fn in (('Вставити з буфера (Ctrl+V)', self._paste), ('Вибрати файл…', self._pick),
                         ('Оновити', self._fill_shots)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, fn=fn: fn())
            row.addWidget(b)
        row.addStretch(1)
        self.shot_info = self._hint('')
        self.shot_info.setWordWrap(False)
        row.addWidget(self.shot_info)
        lay.addLayout(row)
        self.strip = Strip(self)
        lay.addWidget(self.strip)
        lay.addStretch(1)

        # --- низ ------------------------------------------------------------
        row = QHBoxLayout()
        self.tech = QCheckBox('Додати технічне (версія, Windows, кінець логу)')
        self.tech.setChecked(True)
        row.addWidget(self.tech)
        b = QPushButton('Що саме піде?')
        b.clicked.connect(self._show_details)
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        row = QHBoxLayout()
        self.key_info = self._hint('')
        row.addWidget(self.key_info, 1)
        self.b_key = QPushButton('Підключити…')
        self.b_key.clicked.connect(self._connect)
        row.addWidget(self.b_key)
        lay.addLayout(row)

        row = QHBoxLayout()
        self.status = QLabel('')
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        self.b_send = QPushButton('Надіслати')
        self.b_send.setObjectName('Accent')
        self.b_send.clicked.connect(self._send)
        row.addWidget(self.b_send)
        b = QPushButton('Скасувати')
        b.clicked.connect(self.close)
        row.addWidget(b)
        root.addLayout(row)

    def _text(self, lay, label, lines):
        lay.addWidget(QLabel(label))
        t = QPlainTextEdit()
        t.setTabChangesFocus(True)                  # Tab — до наступного поля, а не символ табуляції
        t.setFont(QFont('Segoe UI', 10))
        lay.addWidget(t)
        lay.addWidget(Grip(self, t, lines, self.c['dim']))
        return t

    def kind(self):
        return next((k for k, b in self.kind_btns.items() if b.isChecked()), 'текст')

    # ------------------------------------------------------------------ знімки
    def _fill_shots(self):
        keep = [(s.src, s.on) for s in self.shots if not isinstance(s.src, str) or s.on]
        for s in self.shots:
            s.deleteLater()
        self.shots = []
        for src, on in keep:
            self._add_shot(src, on)
        if self.ctx.get('image') is not None and not any(s.src is self.ctx['image'] for s in self.shots):
            self._add_shot(self.ctx['image'], True)
        have = {s.src for s in self.shots if isinstance(s.src, str)}
        g = self.game_names.get(self.game.currentText(), '')
        steam = self.app.GAMES[g]['steam'] if g else ''
        dirs = feedback.shot_dirs(_steam_libraries(self.app), steam, g)
        for p in feedback.recent_shots(dirs):
            if p not in have:
                self._add_shot(p, False)
        self._count()

    def _add_shot(self, src, on, first=False):
        try:
            s = Shot(self, src, on)
        except Exception:                                   # noqa: BLE001
            return
        s.installEventFilter(self)
        if first and self.shots:
            self.strip.row.insertWidget(0, s)
            self.shots.insert(0, s)
        else:
            self.strip.row.insertWidget(self.strip.row.count() - 1, s)     # перед розпіркою
            self.shots.append(s)

    def _toggle(self, s):
        s.on = not s.on
        s.mark()
        self._count()

    def _count(self):
        n = sum(1 for s in self.shots if s.on)
        self.shot_info.setText(f'вибрано: {n}' if n else 'нічого не вибрано')

    def _shot_menu(self, gpos, item=None):
        """Правий клік по знімку: прибрати зі списку / у Кошик. Якщо клацнуто по
        вибраному, а вибрано кілька, — дія над усіма вибраними."""
        chosen = [s for s in self.shots if s.on]
        items = chosen if item is not None and item in chosen and len(chosen) > 1 else \
            ([item] if item is not None else [])
        files = [s for s in items if isinstance(s.src, str)]
        n = len(items)
        m = QMenu(self)
        if items:
            what = 'знімок' if n == 1 else f'вибрані знімки ({n})'
            m.addAction(f'Прибрати {what} зі списку', lambda: self._hide(items))
            if files:
                m.addAction(f'Видалити з комп\'ютера (у Кошик)… ({len(files)})', lambda: self._recycle(files))
            m.addSeparator()
        rest = [s for s in self.shots if not s.on]
        if rest:
            m.addAction(f'Прибрати всі невибрані ({len(rest)})', lambda: self._hide(rest))
        hidden = len(feedback.hidden_shots())
        if hidden:
            m.addAction(f'Повернути прибрані зі списку ({hidden})', self._unhide)
        if not any(not a.isSeparator() for a in m.actions()):
            return
        m.exec(gpos)

    def _drop(self, items):
        for s in items:
            s.deleteLater()
        self.shots = [s for s in self.shots if s not in items]
        if any(s.src is self.ctx.get('image') for s in items):
            self.ctx['image'] = None            # прев'ю напису не повертати при «Оновити»
        self._count()

    def _hide(self, items):
        files = [s.src for s in items if isinstance(s.src, str)]
        if files:
            try:
                feedback.hide_shots(files)
            except OSError as ex:
                self.status.setText(f'Не вдалося запам\'ятати: {ex}')
        self._drop(items)
        self._fill_shots()                      # на місце прибраних — старші знімки
        self.status.setText(f'Прибрано зі списку: {len(items)} (файли на диску не чіпали).')

    def _unhide(self):
        try:
            feedback.hide_shots(None, on=False)
        except OSError:
            pass
        self._fill_shots()
        self.status.setText('Прибрані знімки знову в списку.')

    def _recycle(self, items):
        names = '\n'.join(os.path.basename(s.src) for s in items[:10])
        more = f'\n…і ще {len(items) - 10}' if len(items) > 10 else ''
        if QMessageBox.question(self, 'Видалити знімки',
                                f'Перемістити в Кошик ({len(items)}):\n\n{names}{more}\n\n'
                                'Їх можна буде відновити з Кошика.') != QMessageBox.Yes:
            return
        left = feedback.to_recycle_bin([s.src for s in items])
        left = {os.path.normcase(p) for p in left}
        gone = [s for s in items if os.path.normcase(os.path.abspath(s.src)) not in left]
        self._drop(gone)
        self._fill_shots()
        self.status.setText(f'У Кошику: {len(gone)}.' +
                            (f' Не вдалося: {len(left)} (файл зайнятий?).' if left else ''))

    def _paste(self):
        if not _alive(self):
            return
        self._paste_due = False
        ims = feedback.clipboard_images()
        for im in ims:
            self._add_shot(im, True, first=True)
        if ims:
            self.strip.horizontalScrollBar().setValue(0)
            self._count()
            self.status.setText(f'Додано з буфера: {len(ims)}.')

    def _pick(self, paths=None):
        if paths is None:
            paths, _f = QFileDialog.getOpenFileNames(self, 'Скриншоти', '',
                                                     'Картинки (*.png *.jpg *.jpeg *.bmp *.webp);;Усі файли (*.*)')
        for p in paths:
            self._add_shot(os.path.normpath(p), True, first=True)
        self.strip.horizontalScrollBar().setValue(0)
        self._count()

    # ------------------------------------------------------------------ звернення
    def _report(self):
        g = self.game_names.get(self.game.currentText(), '')
        r = {'тип': self.kind(), 'гра': g, 'назва гри': self.game.currentText() if g else '',
             'нік': ' '.join(self.nick.text().split())[:feedback.NICK_MAX],
             'що': self.what.toPlainText().strip(),
             'як': self.how.toPlainText().strip(), 'де': self.where.text().strip()}
        if self.with_row.isChecked() and self.ctx.get('row'):
            r['рядок'] = self.ctx['row']
        if self.tech.isChecked():
            r['технічне'] = feedback.technical(self.app.VERSION, self.game.currentText())
        return r

    def _show_details(self):
        r = dict(self._report(), створено='(час надсилання)')
        w = QDialog(self)
        w.setAttribute(Qt.WA_DeleteOnClose, True)
        w.setWindowTitle('Що піде в звернення')
        w.resize(720, 520)
        lay = QVBoxLayout(w)
        t = QPlainTextEdit()
        t.setFont(QFont('Consolas', 9))
        n = sum(1 for s in self.shots if s.on)
        t.setPlainText(feedback.details_text(r) + f'\n\n+ скриншотів: {n}')
        t.setReadOnly(True)
        lay.addWidget(t)
        w.show()
        self.details = w

    def _send(self):
        if self.sending:
            return
        r = self._report()
        why = feedback.problem(r)
        if why:
            self.status.setText(why)
            self.what.setFocus()
            return
        left = feedback.wait_left()
        if left:
            self.status.setText(f'Попереднє звернення щойно пішло — зачекай ще {left} с.')
            return
        feedback.save_nick(r['нік'])
        images = [s.src for s in self.shots if s.on]
        try:
            d = feedback.enqueue(r, images)
        except Exception as ex:                             # noqa: BLE001
            QMessageBox.critical(self, 'Звернення', f'Не вдалося зберегти звернення: {ex}')
            return
        feedback.drop_draft()
        feedback.mark_sent_now()
        self._sent = True
        self.queued = d
        if not feedback.load_key():
            self.app.say('Звернення збережено; надішлеться, щойно підключиш відправку '
                         '(«Повідомити про проблему…» → «Підключити…»).', 'warn')
            self.close()
            return
        self.sending = True
        self.b_send.setEnabled(False)
        self.status.setText('Надсилаю…')
        app = self.app

        def job():
            key = feedback.load_key()
            try:
                link, err = feedback.send(d, key), None
            except feedback.Offline as ex:
                link, err = None, ('offline', str(ex))
            except feedback.TgError as ex:
                link, err = None, ('tg', str(ex))
            except Exception as ex:                         # noqa: BLE001
                link, err = None, ('tg', str(ex))
            app.q.put(('call', lambda: self._sent_done(link, err)))

        threading.Thread(target=job, daemon=True).start()

    def _sent_done(self, link, err):
        """Головний потік (через app.q). Вікно могли вже закрити — тоді лише повідомлення."""
        alive = _alive(self)
        if alive:
            self.sending = False
        if err is None:
            _say_sent(self.app, link)
        elif err[0] == 'offline':
            self.app.say(f'Немає зв\'язку з Telegram ({err[1]}). Звернення збережено — '
                         'надішлю сам, щойно з\'явиться інтернет.', 'warn')
        else:
            self.app.say(f'Telegram не прийняв звернення: {err[1]}. Воно лишилось у черзі — '
                         'надішлю ще раз при наступному запуску.', 'err')
        if alive:
            self.close()

    def _validate(self):
        """«Надіслати» доступна, лише коли в «Що не так» є що читати."""
        if self.sending:
            return
        why = feedback.problem({'що': self.what.toPlainText()})
        self.b_send.setEnabled(not why)
        if why:
            self.status.setText(why)
        elif self.status.text() in (feedback.problem({'що': ''}), feedback.problem({'що': 'а'})):
            self.status.setText('')

    # ------------------------------------------------------------------ ключ
    def _key_state(self):
        key = feedback.load_key()
        if key:
            name = key.get('назва')
            self.key_info.setText(f'Надсилається в групу Telegram «{name}».' if name else
                                  'Надсилається в групу звернень у Telegram.')
            self.b_key.hide()
            return
        found = feedback.find_key_file()
        if found:
            try:
                feedback.import_key(found)
                self.app.say(f'Підключив відправку звернень (знайшов {found}).', 'ok')
                return self._key_state()
            except ValueError:
                pass
        self.key_info.setText('Відправку ще не підключено: попроси у власника файл «звернення.key» і '
                              'вибери його кнопкою праворуч. Поки що звернення збережеться й піде пізніше.')
        self.b_key.show()

    def _connect(self):
        p, _f = QFileDialog.getOpenFileName(self, 'Файл звернення.key від власника', '',
                                            'Ключ звернень (*.key);;Усі файли (*.*)')
        if p:
            try:
                feedback.import_key(p)
            except (ValueError, OSError) as ex:
                QMessageBox.critical(self, 'Звернення', str(ex))
                return
            self._key_state()
            return
        # власник: ключа ще немає зовсім — створити з токена бота
        if QMessageBox.question(self, 'Звернення', 'Файлу ключа немає — створити його з токена бота?\n'
                                                   '(це робить власник програми)') == QMessageBox.Yes:
            self.setup = SetupDialog(self, on_done=self._key_state)
            self.setup.show()

    # ------------------------------------------------------------------ чернетка
    def _restore_draft(self):
        if self.ctx.get('row'):
            return                       # відкрито з рядка — інше звернення
        d = feedback.load_draft()
        if not d:
            return
        b = self.kind_btns.get(d.get('тип'))
        if b is not None:
            b.setChecked(True)
        self.what.setPlainText(d.get('що', ''))
        self.how.setPlainText(d.get('як', ''))
        self.where.setText(d.get('де', ''))
        self.status.setText('Відновлено недописане звернення.')

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:   # Esc — як «Скасувати» (з чернеткою), а не reject()
            self.close()
            return
        super().keyPressEvent(ev)

    def closeEvent(self, ev):
        if not self._sent:
            what = self.what.toPlainText().strip()
            how = self.how.toPlainText().strip()
            if what or how:
                try:
                    feedback.save_draft({'тип': self.kind(), 'що': what, 'як': how,
                                         'де': self.where.text().strip()})
                except OSError:
                    pass
        ev.accept()                     # (QDialog.closeEvent кличе reject() — не треба)


class SetupDialog(QDialog):
    """Для власника: токен бота → група → файл звернення.key (передати перекладачеві)."""

    def __init__(self, parent, on_done=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.on_done = on_done
        self.setWindowTitle('Підключити бота')
        self.resize(620, 420)
        self.bridge = Bridge()
        self.bridge.got.connect(self._found)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 10)
        lay.addWidget(QLabel('1. Додай бота в групу адміністратором (право «Керувати темами»).\n'
                             '2. Напиши в групу будь-що (бот бачить лише свіжі повідомлення).\n'
                             '3. Встав токен від @BotFather і натисни «Знайти групи».'))
        row = QHBoxLayout()
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.Password)
        row.addWidget(self.token, 1)
        b = QPushButton('Знайти групи')
        b.clicked.connect(self._find)
        row.addWidget(b)
        lay.addLayout(row)
        self.lb = QListWidget()
        lay.addWidget(self.lb, 1)
        self.info = QLabel('')
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        row = QHBoxLayout()
        row.addStretch(1)
        self.b_ok = QPushButton('Зберегти ключ')
        self.b_ok.setObjectName('Accent')
        self.b_ok.clicked.connect(self._save)
        row.addWidget(self.b_ok)
        b = QPushButton('Скасувати')
        b.clicked.connect(self.close)
        row.addWidget(b)
        lay.addLayout(row)
        self.chats = []

    def _find(self):
        tok = self.token.text().strip()
        if not tok:
            return
        self.info.setText('Питаю Telegram…')
        bridge = self.bridge

        def job():
            try:
                res, err = feedback.find_chats(tok), None
            except Exception as ex:                         # noqa: BLE001
                res, err = None, str(ex)
            try:
                bridge.got.emit('found', (res, err))
            except RuntimeError:                            # вікно вже закрили
                pass
        threading.Thread(target=job, daemon=True).start()

    def _found(self, _kind, val):
        res, err = val
        if err:
            self.info.setText(f'Не вдалося: {err}')
            return
        bot, chats = res
        self.chats = chats
        self.lb.clear()
        for cid, title, forum, topics in chats:
            notes = []
            if not forum:
                notes.append('теми вимкнено')
            if not topics:
                notes.append('бот не може створювати теми')
            self.lb.addItem(f'{title}  ({cid})' + (f' — {", ".join(notes)}' if notes else ' — готово'))
        if chats:
            self.lb.setCurrentRow(0)
            self.info.setText(f'Бот @{bot}. Вибери групу й натисни «Зберегти ключ».')
        else:
            self.info.setText(f'Бот @{bot} не бачить жодної групи. Додай його в групу, напиши там '
                              'будь-що й натисни «Знайти групи» ще раз.')

    def _save(self):
        i = self.lb.currentRow()
        if i < 0:
            return
        cid, title, forum, topics = self.chats[i]
        if (not forum or not topics) and QMessageBox.question(
                self, 'Звернення', 'Без тем кожне звернення піде просто в загальний чат, а не окремою '
                                   'темою. Усе одно зберегти?') != QMessageBox.Yes:
            return
        feedback.save_key(self.token.text().strip(), cid, title)
        QMessageBox.information(self, 'Звернення', f'Ключ збережено:\n{feedback.KEYFILE}\n\n'
                                                   'Надішли цей файл перекладачеві (у git його немає).')
        if self.on_done:
            self.on_done()
        self.close()
