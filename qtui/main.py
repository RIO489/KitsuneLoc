# -*- coding: utf-8 -*-
"""Головне вікно KitsuneLoc (Qt, з 3.1; запуск — Переклад.pyw).

Уся робота (кроки «1», «2», «3», перевірка, прогрес, повернення оригіналів, інсталятор,
перехід на редактор) — core.Core: вікно від нього успадковує і дає те, що Core потребує
(self.cur, self.settings, чергу self.q, game/badge/editor_hint, _show_books, _refresh_title,
open_editor, _after). Ті самі методи успадковує й старе Tk-вікно (Переклад-старе.pyw).
"""
import html, os, queue, sys, threading, time, traceback

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QRadioButton, QTextBrowser, QVBoxLayout,
                               QWidget)

import themes
from qtui import theme as qtheme

import core

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def legacy():
    """Глобальні змінні робочої логіки (core.py): GAMES, шляхи, save_settings… — словником.
    (Назва з часів, коли їх брали зі старого Переклад.pyw; так їх шукають вікна.)"""
    return core.__dict__


class Var:
    """Те, що в Tk було StringVar/BooleanVar (методи Core кличуть .get())."""

    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


class LabelShim:
    """Мітка з методом configure(text=, foreground=) — як ttk.Label (так їх кличе Core)."""

    def __init__(self, lab):
        self.lab = lab

    def configure(self, text=None, foreground=None, **_kw):
        if text is not None:
            self.lab.setText(text)
        if foreground is not None:
            self.lab.setStyleSheet(f'color: {foreground};')


class MainWindow(core.Core, QMainWindow):
    def __init__(self):
        super().__init__()
        L = legacy()
        self.L, self.GAMES, self.VERSION = L, L['GAMES'], L['VERSION']
        self.settings = L['load_settings']()
        self.theme = self.settings.get('theme', 'stars')
        if self.theme not in themes.THEMES:
            self.theme = 'stars'
        g0 = self.settings.get('last_game', 'crystar')
        self.game = Var(g0 if g0 in self.GAMES else next(iter(self.GAMES)))
        self.busy = self.closing = False
        self.cancel = threading.Event()
        self.t0 = 0
        self.q = queue.Queue()
        self.logf = None
        self.links = {}
        self.book_pc, self.book_files = {}, {}
        self.editor_win = None
        self.reminders_win = None
        self.pics_win = self.scan_win = None
        self.cur = {'game': self.game.get(), 'path': '', 'slot': 'en'}
        self.resize(1080, 820)
        self._build()
        self._apply_theme()
        self._refresh_path()
        self._open_log()
        self.say(f'KitsuneLoc, версія {self.VERSION} (нове вікно, Qt).', 'dim')
        self.poller = QTimer(self, interval=80, timeout=self._poll)
        self.poller.start()
        QTimer.singleShot(200, self._check_deps)
        QTimer.singleShot(3000, self._feedback_flush)      # звернення, що чекали без інтернету

    def _check_deps(self):
        """Бібліотеки зі списку старого вікна (App.DEPS): чого немає — запропонувати доставити."""
        import importlib.util, subprocess
        missing = [pip for mod, pip in self.DEPS if importlib.util.find_spec(mod) is None]
        if not missing:
            return
        if QMessageBox.question(self, 'Не вистачає бібліотек',
                                'Для роботи потрібні бібліотеки, яких зараз немає:\n' + ', '.join(missing)
                                + '\n\nВстановити автоматично?') != QMessageBox.Yes:
            self.say('Без цих бібліотек програма працюватиме не повністю. Запусти «Встановити.bat».', 'warn')
            return
        self.say('Встановлюю ' + ', '.join(missing) + '…', 'dim')

        def job():
            try:
                subprocess.run([sys.executable, '-m', 'pip', 'install'] + missing, check=True, capture_output=True)
                self.say('Готово. Перезапусти вікно.', 'ok')
            except Exception as ex:                         # noqa: BLE001
                self.say(f'Не вдалося встановити: {ex}\nЗапусти «Встановити.bat» вручну.', 'err')
        threading.Thread(target=job, daemon=True).start()

    # ------------------------------------------------------------------ вигляд
    def _panel(self, title):
        f = QFrame()
        f.setObjectName('Panel')
        lay = QVBoxLayout(f)
        lay.setContentsMargins(14, 8, 14, 12)
        lay.setSpacing(6)
        lab = QLabel(title)
        lab.setObjectName('Section')
        lay.addWidget(lab)
        self.back.glow(f)
        return f, lay

    def _build(self):
        self.back = qtheme.Backdrop(self.theme)
        self.setCentralWidget(self.back)
        root = QVBoxLayout(self.back)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(12)

        # --- гра ---------------------------------------------------------
        gf, gl = self._panel('✦ Гра')
        row = QHBoxLayout()
        self.game_btns = QButtonGroup(self)
        for k, g in self.GAMES.items():
            rb = QRadioButton(g['title'])
            rb.setChecked(k == self.game.get())
            rb.toggled.connect(lambda on, k=k: on and self._switch_game(k))
            self.game_btns.addButton(rb)
            row.addWidget(rb)
        row.addStretch(1)
        self.badge_lab = QLabel('')
        self.badge = LabelShim(self.badge_lab)
        row.addWidget(self.badge_lab)
        gl.addLayout(row)
        row = QHBoxLayout()
        self.slot_lab = QLabel('Мова тексту в грі (куди писати переклад):')
        row.addWidget(self.slot_lab)
        self.slot = Var(self.settings.get('crystar_slot', 'en'))
        self.slot_btns = []
        for val, label in (('en', 'англійська'), ('ja', 'японська')):
            rb = QRadioButton(label)
            rb.setChecked(val == self.slot.get())
            rb.toggled.connect(lambda on, v=val: on and self._save_slot(v))
            self.slot_btns.append(rb)
            row.addWidget(rb)
        row.addStretch(1)
        gl.addLayout(row)
        self.textures = QCheckBox('Заливати в гру написи на картинках (текстури) і свої картинки')
        self.textures.toggled.connect(self._save_textures)
        gl.addWidget(self.textures)
        self.mt_game = QCheckBox('Підставляти машинний переклад, де ще немає свого (пробний прохід)')
        self.mt_game.toggled.connect(self._save_mt_game)
        gl.addWidget(self.mt_game)
        row = QHBoxLayout()
        row.addWidget(QLabel('Тека гри:'))
        self.path_edit = QLineEdit()
        self.path_edit.editingFinished.connect(self._path_typed)
        row.addWidget(self.path_edit, 1)
        self.b_find = QPushButton('Знайти')
        self.b_find.clicked.connect(self._autofind)
        row.addWidget(self.b_find)
        self.b_pick = QPushButton('Обрати…')
        self.b_pick.clicked.connect(self._pick)
        row.addWidget(self.b_pick)
        gl.addLayout(row)
        root.addWidget(gf)

        # --- робота ------------------------------------------------------
        wf, wl = self._panel('✦ Робота')
        row = QHBoxLayout()
        play = self.L['play']
        self.b1 = self._step(row, '1. Дістати текст з гри', 'зібрати книги Excel',
                             lambda: (self._flush_editor(), self._run(self.do_export, 'neptune.wav')), True)
        self.b_open = self._step(row, 'Відкрити теку з перекладом', 'там лежать книги .xlsx', self.open_xlsx)
        self.b2 = self._step(row, '2. Залити переклад у гру', 'з резервною копією',
                             lambda: (self._flush_editor(), self._run(self.do_import, 'neptune-shy.wav')), True)
        self.b3 = self._step(row, '3. Запустити гру', 'подивитись результат', self.launch)
        row.addStretch(1)
        wl.addLayout(row)
        row = QHBoxLayout()
        self.b_editor = QPushButton('Редактор перекладу…')
        self.b_editor.setObjectName('Accent')
        self.b_editor.clicked.connect(lambda: self.open_editor())
        row.addWidget(self.b_editor)
        self.b_remind = QPushButton('Нагадування…')
        self.b_remind.clicked.connect(self.open_reminders)
        row.addWidget(self.b_remind)
        self.hint_lab = QLabel('')
        self.hint_lab.setObjectName('Hint')
        self.editor_hint = LabelShim(self.hint_lab)
        row.addWidget(self.hint_lab, 1)
        wl.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel('Книга:'))
        self.book_box = QComboBox()
        self.book_box.setMinimumWidth(320)
        row.addWidget(self.book_box)
        self.b_book = QPushButton('Відкрити книгу')
        self.b_book.clicked.connect(self.open_book)
        row.addWidget(self.b_book)
        self.b_pics = QPushButton('Написи на картинках (з прев\'ю)…')
        self.b_pics.clicked.connect(lambda: self.open_pics())
        row.addWidget(self.b_pics)
        self.b_terms = QPushButton('Терміни…')
        self.b_terms.clicked.connect(self.open_terms)
        row.addWidget(self.b_terms)
        self.b_scan = QPushButton('Знайти написи…')
        self.b_scan.clicked.connect(self.open_scan)
        row.addWidget(self.b_scan)
        row.addStretch(1)
        wl.addLayout(row)
        root.addWidget(wf)

        # --- додатково ---------------------------------------------------
        ef, el = self._panel('✦ Додатково')
        row = QHBoxLayout()
        self.b_prog = QPushButton('Мій прогрес')
        self.b_prog.clicked.connect(lambda: (self._flush_editor(), self._run(self.do_progress)))
        row.addWidget(self.b_prog)
        self.b_check = QPushButton('Перевірити переклад')
        self.b_check.clicked.connect(lambda: (self._flush_editor(), self._run(self.do_check)))
        row.addWidget(self.b_check)
        self.b_rest = QPushButton('Повернути оригінали гри')
        self.b_rest.clicked.connect(lambda: self._run(self.do_restore))
        row.addWidget(self.b_rest)
        self.b_inst = QPushButton('Створити інсталятор…')
        self.b_inst.clicked.connect(self.make_installer)
        row.addWidget(self.b_inst)
        self.b_report = QPushButton('Повідомити про проблему…')
        self.b_report.clicked.connect(lambda: self.open_feedback())
        row.addWidget(self.b_report)
        row.addStretch(1)
        self.sound = QCheckBox('Звуки')
        self.sound.setChecked(self.settings.get('sound', True))
        self.sound.toggled.connect(self._save_sound)
        row.addWidget(self.sound)
        row.addWidget(QLabel('Тема:'))
        self.theme_box = QComboBox()
        self.theme_box.addItems([themes.get(n)['title'] for n in themes.names()])
        self.theme_box.setCurrentText(themes.get(self.theme)['title'])
        self.theme_box.currentTextChanged.connect(lambda t: self._set_theme(themes.by_title(t)))
        row.addWidget(self.theme_box)
        b = QPushButton('Скопіювати лог')
        b.clicked.connect(self.copy_log)
        row.addWidget(b)
        el.addLayout(row)
        root.addWidget(ef)

        self.buttons = [self.b1, self.b2, self.b3, self.b_open, self.b_book, self.b_editor,
                        self.b_prog, self.b_check, self.b_rest, self.b_inst, self.b_find, self.b_pick]

        # --- стан і журнал -----------------------------------------------
        row = QHBoxLayout()
        self.status_lab = QLabel('Готово.')
        row.addWidget(self.status_lab, 1)
        self.elapsed = QLabel('')
        row.addWidget(self.elapsed)
        self.b_stop = QPushButton('Зупинити')
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self._stop)
        row.addWidget(self.b_stop)
        root.addLayout(row)
        self.pb = QProgressBar()
        self.pb.setTextVisible(False)
        self.pb.setFixedHeight(8)
        root.addWidget(self.pb)
        lf = QFrame()
        lf.setObjectName('Panel')
        ll = QVBoxLayout(lf)
        ll.setContentsMargins(8, 8, 8, 8)
        self.back.glow(lf)
        self.log = QTextBrowser()
        self.log.setOpenLinks(False)
        self.log.anchorClicked.connect(self._link_clicked)
        self.log.setContextMenuPolicy(Qt.CustomContextMenu)
        self.log.customContextMenuRequested.connect(self._log_menu)
        ll.addWidget(self.log)
        root.addWidget(lf, 1)
        self.log_panel = lf
        self.chibi = None

    def _step(self, row, text, hint, cmd, accent=False):
        box = QVBoxLayout()
        b = QPushButton(text)
        if accent:
            b.setObjectName('Accent')
        b.clicked.connect(cmd)
        box.addWidget(b)
        h = QLabel(hint)
        h.setObjectName('Hint')
        box.addWidget(h)
        row.addLayout(box)
        return b

    def _todo(self, row, text):
        b = QPushButton(text)
        b.setEnabled(False)
        b.setToolTip('Це вікно ще не перенесено в нову версію інтерфейсу — поки що є в старому вікні')
        row.addWidget(b)
        return b

    # ------------------------------------------------------------------ тема
    def _apply_theme(self):
        QApplication.instance().setStyleSheet(qtheme.qss(self.theme))
        self.back.retheme(self.theme)
        self._show_chibi()

    def _set_theme(self, name):
        if name == self.theme:
            return
        self.theme = name
        self.settings['theme'] = name
        self.L['save_settings'](self.settings)
        self._apply_theme()
        if self.editor_win is not None and self.editor_win.isVisible():
            self.say('Тема редактора зміниться, коли його перевідкрити.', 'dim')

    def colors(self):
        return themes.get(self.theme)

    # ------------------------------------------------------------------ гра й шляхи
    def _switch_game(self, k):
        self.game.set(k)
        self.settings['last_game'] = k
        self.L['save_settings'](self.settings)
        self._refresh_path()

    def _refresh_path(self):
        g = self.game.get()
        self.path_edit.setText(self.settings.get(g, ''))
        for w, on in ((self.textures, self._textures_on()), (self.mt_game, self._mt_in_game())):
            w.blockSignals(True)
            w.setChecked(on)
            w.blockSignals(False)
        for w in [self.slot_lab] + self.slot_btns:
            w.setEnabled(g == 'crystar')
        self._show_chibi()
        self._refresh_books()
        self._refresh_badge()
        self._refresh_title()
        self._refresh_editor_hint()
        w = self.reminders_win
        if w is not None and w.isVisible():              # нагадування — іншої гри
            w.reload(self._xl_now(), self.GAMES[g]['title'])

    def _refresh_title(self):
        if threading.current_thread() is not threading.main_thread():
            self.q.put(('call', self._refresh_title))        # Core._remember_pc кличе з потоку
            return
        base = f'KitsuneLoc {self.VERSION} — {self.GAMES[self.game.get()]["title"]}'
        pc = self.settings.get('pc', {}).get(self.game.get())
        self.setWindowTitle(f'{base} — {pc}' if pc else base)

    def _path_typed(self):
        p = self.path_edit.text().strip()
        if p != self.settings.get(self.game.get(), ''):
            self.settings[self.game.get()] = p
            self.L['save_settings'](self.settings)
            self._refresh_badge()

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, 'Оберіть теку з грою')
        if not d:
            return
        g = self.GAMES[self.game.get()]
        if not os.path.exists(os.path.join(d, g['marker'])):
            QMessageBox.warning(self, 'Не та тека', f'У цій теці немає {g["marker"]} — це не {g["title"]}.')
            return
        self.settings[self.game.get()] = os.path.normpath(d)
        self.L['save_settings'](self.settings)
        self._refresh_path()

    def _autofind(self):
        g = self.GAMES[self.game.get()]
        p = self.L['find_game'](g)
        if p:
            self.settings[self.game.get()] = p
            self.L['save_settings'](self.settings)
            self._refresh_path()
            self.say(f'Знайшов {g["title"]}: {p}', 'ok')
        else:
            self.say(f'Не знайшов {g["title"]} у бібліотеках Steam — вкажи теку вручну.', 'warn')

    def _save_textures(self, on):
        self.settings.setdefault('textures', {})[self.game.get()] = bool(on)
        self.L['save_settings'](self.settings)
        self.say('Написи на картинках ' + ('заливатимуться в гру.' if on else
                 'не заливатимуться: при «2» у грі будуть оригінальні текстури '
                 '(уже перекладені повернуться до оригіналу).'), 'dim')

    def _save_mt_game(self, on):
        self.settings.setdefault('mt_in_game', {})[self.game.get()] = bool(on)
        self.L['save_settings'](self.settings)
        self.say('Машинний переклад ' + ('підставлятиметься в гру там, де ще немає свого — лише '
                 'для пробного проходу, перед справжнім «2» вимкни.' if on else
                 'в гру не йде: там, де немає свого перекладу, буде оригінал.'), 'dim')

    def _save_slot(self, v):
        self.slot.set(v)
        self.settings['crystar_slot'] = v
        self.L['save_settings'](self.settings)

    def _save_sound(self, on):
        self.settings['sound'] = bool(on)
        self.L['save_settings'](self.settings)

    def _show_chibi(self):
        """Дівчина гри внизу праворуч у журналі (з кешу чібі), як у старому вікні."""
        if self.chibi is not None:
            self.chibi.deleteLater()
            self.chibi = None
        pics = qtheme.portraits(self.game.get(), 1, 230)
        if pics:
            self.chibi = qtheme.Overlay(self.back, pics[0], 'log')
            self.chibi.show()
            QTimer.singleShot(0, self._place_chibi)

    def _place_chibi(self):
        if self.chibi is None:
            return
        p = self.log_panel.mapTo(self.back, self.log_panel.rect().bottomRight())
        self.chibi.place(p.x() - self.chibi.width() - 18, p.y() - self.chibi.height() - 6)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        QTimer.singleShot(0, self._place_chibi)

    # ------------------------------------------------------------------ книги
    def _show_books(self, xl, names):
        cur = self.book_file()
        self.book_files = {}
        for fn in names:
            st = self.book_pc.get(os.path.join(xl, fn))
            label = fn[:-5]
            if st and st[2]:
                label += ' — ✓ 100%' if st[1] == st[2] else f' — {100 * st[1] / st[2]:.0f}%'
            self.book_files[label] = fn
        self.book_box.blockSignals(True)
        self.book_box.clear()
        self.book_box.addItems(list(self.book_files))
        keep = next((k for k, v in self.book_files.items() if v == cur), None)
        if keep:
            self.book_box.setCurrentText(keep)
        self.book_box.blockSignals(False)

    def book_file(self):
        return self.book_files.get(self.book_box.currentText(), '')

    def open_xlsx(self):
        self._snap()
        _w, xl, _o, _b = self.dirs()
        if not os.path.isdir(xl):
            QMessageBox.information(self, 'Немає файлів', 'Спочатку натисни «1. Дістати текст з гри».')
            return
        self._open_path(xl)

    def open_book(self):
        self._snap()
        _w, xl, _o, _b = self.dirs()
        name = self.book_file()
        if not name:
            QMessageBox.information(self, 'Немає книг', 'Спочатку натисни «1. Дістати текст з гри».')
            return
        self._open_path(os.path.join(xl, name))

    # ------------------------------------------------------------------ робота у фоні
    def _snap(self):
        self.cur = {'game': self.game.get(), 'path': self.path_edit.text().strip(), 'slot': self.slot.get()}

    def _run(self, fn, sound=None):
        if self.busy:
            return
        if sound and self.sound.isChecked():
            self.L['play'](sound)
        self._snap()
        try:
            self._fresh()
        except Exception:                                   # noqa: BLE001
            self.say('Не вдалося перечитати модулі:\n' + traceback.format_exc(), 'err')
        self.busy = True
        self.cancel.clear()
        self.t0 = time.time()
        for b in self.buttons:
            b.setEnabled(False)
        self.b_stop.setEnabled(True)
        Cancelled = self.L['Cancelled']

        def work():
            try:
                fn()
            except Cancelled:
                self.say('\nЗупинено. У теці гри нічого не змінено.', 'warn')
                self.set_status('Зупинено.')
            except RuntimeError as ex:
                self.say('\n' + str(ex), 'err')
                self.set_status('Не вдалося — дивись повідомлення нижче.')
            except Exception:                               # noqa: BLE001
                self.say('\nПОМИЛКА:\n' + traceback.format_exc(), 'err')
                self.set_status('Не вдалося — дивись повідомлення нижче.')
            finally:
                self.q.put(('done', None))
        threading.Thread(target=work, daemon=True).start()

    def _stop(self):
        if self.busy:
            self.cancel.set()
            self.set_status('Зупиняю…')
            self.b_stop.setEnabled(False)

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind in ('log', 'link', 'act'):
                    self._log_add(kind, val)
                elif kind == 'chibi':
                    if val == self.game.get():
                        self._show_chibi()
                elif kind == 'books':
                    game, xl, _names = val
                    if game == self.game.get() and os.path.isdir(xl):
                        self._show_books(xl, sorted(f for f in os.listdir(xl)
                                                    if f.endswith('.xlsx') and not f.startswith('~$')))
                elif kind == 'step':
                    i, n, label = val
                    self.pb.setMaximum(max(n, 1))
                    self.pb.setValue(i)
                    self.status_lab.setText(f'{label}  ({i}/{n})' if label else f'{i}/{n}')
                elif kind == 'status':
                    self.status_lab.setText(val)
                elif kind == 'call':
                    val()
                elif kind == 'done':
                    self.busy = False
                    self.elapsed.setText('')
                    for b in self.buttons:
                        b.setEnabled(True)
                    self.b_stop.setEnabled(False)
                    self.pb.setValue(0)
                    self._refresh_books()
                    self._refresh_badge()
                    if self.closing:
                        self.close()
                        return
        except queue.Empty:
            pass
        if self.busy and self.t0:
            s = int(time.time() - self.t0)
            self.elapsed.setText(f'{s // 60}:{s % 60:02d}')

    # ------------------------------------------------------------------ журнал
    def _log_add(self, kind, val):
        text, tag = val[:2]
        c = themes.get(self.theme)
        color = {'err': c['err'], 'warn': c['warn'], 'ok': c['ok'], 'dim': c['dim'],
                 'head': c['accent']}.get(tag, c.get('logfg', c['fg']))
        body = html.escape(text).replace('\n', '<br>')
        style = f'color:{color};'
        if tag == 'head':
            style += 'font-weight:bold;'
        if tag == 'mono':
            style += 'font-family:Consolas; font-size:9pt;'
        if kind == 'link':
            n = len(self.links)
            self.links[n] = ('goto', (self.game.get(),) + tuple(val[2]))
            body = f'<a href="kl:{n}" style="color:{color}; text-decoration: underline;">{body}</a>'
        elif kind == 'act':
            n = len(self.links)
            self.links[n] = ('act', val[2])
            body = f'<a href="kl:{n}" style="color:{color}; text-decoration: underline;">{body}</a>'
        self.log.moveCursor(QTextCursor.End)
        self.log.insertHtml(f'<span style="{style}">{body}</span><br>')
        self.log.moveCursor(QTextCursor.End)
        self.log.ensureCursorVisible()
        if self.logf:
            try:
                self.logf.write(text + '\n')
                self.logf.flush()
            except OSError:
                pass

    def _link_clicked(self, url):
        s = url.toString()
        if not s.startswith('kl:'):
            QDesktopServices.openUrl(url)
            return
        kind, val = self.links.get(int(s[3:]), (None, None))
        if kind == 'goto':
            self._goto(val[:4])
        elif kind == 'act':
            val()

    def _log_menu(self, pos):
        a = self.log.anchorAt(pos)
        m = QMenu(self)
        if a.startswith('kl:'):
            kind, val = self.links.get(int(a[3:]), (None, None))
            if kind == 'goto':
                game, s, i, src, msg, tr, approved = val
                m.addAction('Відкрити рядок', lambda: self._goto(val[:4]))
                if approved:
                    m.addAction('Скасувати затвердження', lambda: self._approve(int(a[3:]), False))
                else:
                    m.addAction('Затвердити: це не помилка', lambda: self._approve(int(a[3:]), True))
                m.addSeparator()
        m.addAction('Скопіювати лог', self.copy_log)
        m.exec(self.log.viewport().mapToGlobal(pos))

    def _approve(self, n, on):
        import sheets
        game, s, i, src, msg, tr, approved = self.links[n][1]
        xl = os.path.join(self.L['XLSX'], self.GAMES[game]['folder'])
        ok = sheets.load_approved(xl)
        key = sheets.approval_key(s, i, msg)
        if on:
            ok[key] = tr
        else:
            ok.pop(key, None)
        try:
            sheets.save_approved(xl, ok)
        except OSError as ex:
            self.set_status(f'Не вдалося зберегти: {ex}')
            return
        self.links[n] = ('goto', (game, s, i, src, msg, tr, on))
        self.set_status('Затверджено: це попередження більше не показуватиметься, доки не зміниш переклад.'
                        if on else 'Затвердження скасовано.')

    def _after(self, ms, fn):
        """Відкладена дія (Core._feedback_flush: звернення, що чекають, — раз на 5 хв)."""
        QTimer.singleShot(ms, fn)

    def copy_log(self):
        QApplication.clipboard().setText(self.log.toPlainText())
        self.set_status('Лог скопійовано в буфер обміну.')

    # ------------------------------------------------------------------ вікна
    def open_editor(self, goto=None):
        self._snap()
        import importlib, project
        work, xl, _o, bk = self.dirs()
        win = self.editor_win
        if win is not None and win.isVisible():
            if goto:
                win.goto(goto)
            win.raise_()
            win.activateWindow()
            return
        if not os.path.isdir(work) or not any(True for _ in __import__('common').walk(work)):
            QMessageBox.information(self, 'Редактор перекладу', 'Спочатку натисни «1. Дістати текст з гри».')
            return
        if not project.enabled(xl):
            busy = self.open_books(xl)
            if busy:
                QMessageBox.warning(self, 'Редактор перекладу', 'Спершу закрий в Excel: ' + ', '.join(busy) +
                                    '\n(збережи зміни — програма їх перенесе).')
                return
            if QMessageBox.question(
                    self, 'Перейти на редактор',
                    'Переклад переїде з книг Excel у програму:\n\n'
                    '• усе, що вже є в книгах, перенесеться;\n'
                    '• однакові рядки стануть пов\'язаними (переклав один — перекладено всі); '
                    'де однакові рядки вже перекладено по-різному — вони лишаться окремими;\n'
                    '• далі програма бере переклад із редактора, а не з книг. Книги можна '
                    'вивантажити будь-коли як копію, а зміни з них — завантажити назад.\n\n'
                    'Перейти?') != QMessageBox.Yes or self.busy:
                return
            self._run(lambda: self._migrate(), None)
            return
        try:
            for name in ('project', 'preview'):
                if name in sys.modules:
                    importlib.reload(sys.modules[name])
            from qtui.editor import EditorWindow
            self.editor_win = EditorWindow(self.get_project(), bk, self.theme,
                                           title=f'Редактор перекладу — {self.GAMES[self.cur["game"]]["title"]}',
                                           goto=goto, settings=self.settings,
                                           save_settings=self.L['save_settings'], games=self.GAMES, app=self)
            self.editor_win.show()
        except Exception as e:                              # noqa: BLE001
            QMessageBox.critical(self, 'Редактор перекладу', f'{e}\n\n{traceback.format_exc()}')

    def _atlas_ready(self, what):
        """Написи є лише в MSK і Neptunia, і потрібні чисті оригінали (інакше «Оригінал» — уже переклад)."""
        self._snap()
        if self.cur['game'] not in ('msk', 'nep'):
            QMessageBox.information(self, 'Немає написів', 'Написи на картинках є в Mary Skelter і Neptunia Re;Birth1.')
            return False
        try:
            bad = self.check_originals(fix=False)
        except RuntimeError:
            bad = []
        if bad:
            QMessageBox.warning(self, 'Немає чистих оригіналів',
                                'Резервні копії гри (' + ', '.join(bad) + ') — не оригінали: ' + what +
                                '\n\nЗакрий гру, у Steam зроби «Перевірити цілісність файлів гри» і натисни '
                                '«1. Дістати текст з гри». Потім відкрий це вікно знову.')
            return False
        return True

    def open_pics(self, goto=None):
        """«Написи на картинках» з живим прев'ю (qtui/atlas_editor.py); goto — ключ напису з редактора."""
        win = self.pics_win
        if win is not None and win.isVisible() and win.game == self.game.get():
            if goto:
                win.goto(goto)
            win.raise_()
            win.activateWindow()
            return
        if not self._atlas_ready('колонка «Оригінал» показала б уже перекладені картинки.'):
            return
        try:
            self._fresh()
            import importlib, atlas_editor
            import qtui.atlas_editor as qae
            importlib.reload(atlas_editor)
            importlib.reload(qae)
            self.pics_win = qae.AtlasEditor(self, goto=goto)
            self.pics_win.show()
        except Exception as e:                              # noqa: BLE001
            QMessageBox.critical(self, 'Написи на картинках', f'{e}\n\n{traceback.format_exc()}')

    def open_scan(self):
        """«Знайти написи»: перекладач сам розмічає написи на картинках (qtui/textscan_window.py)."""
        win = self.scan_win
        if win is not None and win.isVisible() and win.game == self.game.get():
            win.raise_()
            win.activateWindow()
            return
        if not self._atlas_ready('на картинках уже намальований переклад.'):
            return
        try:
            self._fresh()
            import importlib, textscan
            import qtui.textscan_window as qts
            importlib.reload(textscan)
            importlib.reload(qts)
            self.scan_win = qts.TextScan(self)
            self.scan_win.show()
        except Exception as e:                              # noqa: BLE001
            QMessageBox.critical(self, 'Знайти написи', f'{e}\n\n{traceback.format_exc()}')

    def open_reminders(self):
        """«Нагадування» (qtui/reminders_window.py) — для гри, вибраної вгорі."""
        self._snap()
        title = self.GAMES[self.game.get()]['title']
        w = self.reminders_win
        if w is not None and w.isVisible():
            w.reload(self._xl_now(), title)
            w.lift()
            return
        try:
            import importlib, reminders
            from qtui import reminders_window
            importlib.reload(reminders)
            importlib.reload(reminders_window)
            self.reminders_win = reminders_window.RemindersWindow(self, self._xl_now(), title)
        except Exception as e:                              # noqa: BLE001
            QMessageBox.critical(self, 'Нагадування', f'{e}\n\n{traceback.format_exc()}')

    def reminders_changed(self):
        """Нагадування додано з іншого вікна («Гра на екрані») — оновити відкрите."""
        w = self.reminders_win
        if w is not None and w.isVisible():
            w.reload()

    def open_terms(self):
        """Глосарій гри й «як уже перекладено» (qtui/terms_window.py)."""
        self._snap()
        try:
            import importlib, glossary
            from qtui import terms_window
            importlib.reload(glossary)
            importlib.reload(terms_window)
            self.terms_win = terms_window.TermsWindow(self)
            self.terms_win.show()
        except Exception as e:                              # noqa: BLE001
            QMessageBox.critical(self, 'Терміни', f'{e}\n\n{traceback.format_exc()}')

    def open_feedback(self, ctx=None):
        """«Повідомити про проблему» (qtui/feedback_window.py); ctx — рядок, звідки відкрито."""
        try:
            import importlib, feedback
            from qtui import feedback_window
            importlib.reload(feedback)
            importlib.reload(feedback_window)
            self.feedback_win = feedback_window.FeedbackWindow(self, ctx)
            self.feedback_win.show()
        except Exception as e:                              # noqa: BLE001
            QMessageBox.critical(self, 'Звернення', f'{e}\n\n{traceback.format_exc()}')

    def make_installer(self):
        if self.busy:
            return
        g = self.game.get()
        desk = os.path.join(os.path.expanduser('~'), 'Desktop')
        name = f'{self.GAMES[g]["folder"]} — українська ({time.strftime("%Y-%m-%d")}).zip'
        path, _f = QFileDialog.getSaveFileName(self, 'Куди зберегти інсталятор перекладу',
                                               os.path.join(desk if os.path.isdir(desk) else HERE, name),
                                               'Архів zip (*.zip)')
        if path:
            self._run(lambda: self.do_installer(os.path.normpath(path)))

    def launch(self):
        self._snap()
        try:
            g = self.GAMES[self.cur['game']]
            exe = os.path.join(self.root_dir(), g.get('launch', g['exe']))
        except RuntimeError as e:
            QMessageBox.critical(self, 'Помилка', str(e))
            return
        if not os.path.exists(exe):
            QMessageBox.critical(self, 'Помилка', f'Не знайшов {exe}')
            return
        import subprocess
        try:
            subprocess.Popen([exe], cwd=os.path.dirname(exe))      # робоча тека — тека гри (Neptunia)
        except OSError as e:
            QMessageBox.critical(self, 'Не вдалося запустити гру', str(e))

    # ------------------------------------------------------------------ закриття
    def closeEvent(self, ev):
        if self.busy and not self.closing:
            if QMessageBox.question(self, 'Триває робота',
                                    'Зараз програма пише файли. Якщо закрити просто зараз, файл у теці гри '
                                    'може лишитися недописаним.\n\nЗупинити роботу й закрити вікно?') \
                    != QMessageBox.Yes:
                ev.ignore()
                return
            self.closing = True
            self.cancel.set()
            self.set_status('Зупиняю…')
            ev.ignore()
            return
        try:
            self._flush_editor()
        except Exception:                                   # noqa: BLE001
            pass
        for w in (self.editor_win, self.reminders_win, self.pics_win, self.scan_win):
            if w is not None:
                w.close()
        self.settings['last_game'] = self.game.get()
        self.L['save_settings'](self.settings)
        if self.logf:
            try:
                self.logf.close()
            except OSError:
                pass
        super().closeEvent(ev)


def main():
    os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
    sys.path.insert(0, HERE)
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
