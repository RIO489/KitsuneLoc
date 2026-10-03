# -*- coding: utf-8 -*-
"""Вікно «Машинний переклад…» редактора на Qt — те саме, що mt_window.py (Tk).

Перекладач вибирає, що перекласти (вибрані рядки / розділ дерева / уся гра), сервіс
(Claude, DeepL, Google) і пише вказівки до стилю; програма показує обсяг і орієнтовну
ціну, а після «Почати» перекладає у фоні (mt/job.py). Результат — у поле «Машинний»
рядків (Project.set_mt), не в переклад: у гру сам не йде, перекладач бере його кнопкою
«Взяти в переклад» і вичитує. Ключі — mt.key (mt/keys.py), вказівки —
`Переклад\\<гра>\\машинний.json` (ті самі файли, що й у Tk-вікна).

Від редактора (qtui/editor.EditorWindow) потрібні: pr, ctx, terms, node, _node_rows(node),
node_title(), selected_keys(), refs(), game_title(), settings (dict або None),
_save_setting(ключ, значення), _commit(), _save(), _levels_state(), _count(), _mt_changed(keys).
Фоновий потік до віджетів не торкається: усе — сигналом Bridge у головний потік.
"""
import json, os, threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QRadioButton, QVBoxLayout, QWidget)

from mt import engines, job, keys

SETTINGS = 'машинний.json'          # у теці перекладу гри: вказівки до стилю, сервіс, модель
SERVICES = (('claude', 'Claude (Anthropic)'), ('deepl', 'DeepL'), ('google', 'Google Translate'))
KEY_HELP = {'claude': 'ключ API — console.anthropic.com → API Keys (рахунок платний)',
            'deepl': 'ключ — deepl.com/your-account/keys (безкоштовний план: 500 тис. знаків на місяць)',
            'google': 'ключ API Google Cloud з увімкненим Cloud Translation API (платно за знаки)'}


class Bridge(QObject):
    """Фоновий потік -> вікно (сигнали Qt потокобезпечні)."""
    got = Signal(str, object)


class MTWindow(QDialog):
    def __init__(self, ed, keys_sel=None):
        super().__init__(ed)
        self.ed, self.pr = ed, ed.pr
        self.sel = list(keys_sel or ed.selected_keys())
        self.cancel, self.thread, self.eng = threading.Event(), None, None
        self._last_progress = False
        self.cfg = self._load_cfg()
        self.setWindowTitle('Машинний переклад')
        self.resize(860, 700)
        self.setModal(False)
        self.bridge = Bridge()
        self.bridge.got.connect(self._got)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        # --- що перекласти ---------------------------------------------------
        f, fl = self._box(lay, '✦ Що перекласти')
        self.scope = QButtonGroup(self)
        self.r_sel, self.r_node, self.r_all = QRadioButton(), QRadioButton(), QRadioButton()
        (self.r_sel if len(self.sel) > 1 else self.r_node).setChecked(True)
        for rb in (self.r_sel, self.r_node, self.r_all):
            self.scope.addButton(rb)
            rb.toggled.connect(lambda on: on and self._recount())
            fl.addWidget(rb)
        self.node_name = ed.node_title() or 'Усі рядки'
        self.redo = QCheckBox('і там, де машинний уже є (перекласти заново)')
        self.with_tr = QCheckBox('і вже перекладені мною (машинний — поруч, для порівняння)')
        fl.addSpacing(4)
        for c in (self.redo, self.with_tr):
            c.toggled.connect(lambda _on: self._recount())
            fl.addWidget(c)

        # --- сервіс ----------------------------------------------------------
        f, fl = self._box(lay, '✦ Сервіс')
        h = QHBoxLayout()
        self.svc = QButtonGroup(self)
        self.svc_btns = {}
        cur_svc = self.cfg.get('сервіс', 'claude')
        for val, lab in SERVICES:
            rb = QRadioButton(lab)
            rb.setChecked(val == cur_svc)
            self.svc.addButton(rb)
            self.svc_btns[val] = rb
            h.addWidget(rb)
            h.addSpacing(10)
        if not any(rb.isChecked() for rb in self.svc_btns.values()):
            self.svc_btns['claude'].setChecked(True)
        for rb in self.svc_btns.values():
            rb.toggled.connect(lambda on: on and self._svc_changed())
        h.addStretch(1)
        fl.addLayout(h)
        self.model_row = QWidget()
        h = QHBoxLayout(self.model_row)
        h.setContentsMargins(0, 4, 0, 0)
        h.addWidget(QLabel('Модель:'))
        self.model = QComboBox()
        self.model.addItems([lab for _m, lab, _a, _b in engines.CLAUDE_MODELS])
        cur = next((lab for m, lab, _a, _b in engines.CLAUDE_MODELS if m == self.cfg.get('модель')), None)
        if cur:
            self.model.setCurrentText(cur)
        self.model.currentIndexChanged.connect(lambda _i: self._recount())
        h.addWidget(self.model)
        h.addStretch(1)
        fl.addWidget(self.model_row)
        # бібліотека для Claude — не обов'язкова: ставиться тут, коли справді знадобилась
        self.lib_row = QWidget()
        h = QHBoxLayout(self.lib_row)
        h.setContentsMargins(0, 4, 0, 0)
        self.lib_info = QLabel('')
        self.lib_info.setObjectName('Warn')
        self.lib_info.setWordWrap(True)
        h.addWidget(self.lib_info, 1)
        self.b_lib = QPushButton('Встановити')
        self.b_lib.setObjectName('Accent')
        self.b_lib.setAutoDefault(False)
        self.b_lib.clicked.connect(self._install_lib)
        h.addWidget(self.b_lib)
        fl.addWidget(self.lib_row)
        h = QHBoxLayout()
        self.key_info = QLabel('')
        self.key_info.setObjectName('Hint')
        self.key_info.setWordWrap(True)
        h.addWidget(self.key_info, 1)
        b = QPushButton('Ключ…')
        b.setAutoDefault(False)
        b.clicked.connect(self._set_key)
        h.addWidget(b)
        fl.addLayout(h)

        # --- вказівки --------------------------------------------------------
        f, fl = self._box(lay, '✦ Вказівки для перекладу (стиль, звертання, що лишати англійською)')
        self.style_t = QPlainTextEdit()
        self.style_t.setPlainText(self.cfg.get('вказівки', job.DEFAULT_STYLE))
        self.style_t.setFixedHeight(self.style_t.fontMetrics().lineSpacing() * 4 + 22)
        fl.addWidget(self.style_t)
        hint = QLabel('Глосарій («Терміни…»), уже перекладені імена, мовець і попередні рядки сцени '
                      'додаються самі.')
        hint.setObjectName('Hint')
        hint.setWordWrap(True)
        fl.addWidget(hint)

        self.info = QLabel('')
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        h = QHBoxLayout()
        self.b_start = QPushButton('Почати')
        self.b_start.setObjectName('Accent')
        self.b_start.setAutoDefault(False)
        self.b_start.clicked.connect(self._start)
        h.addWidget(self.b_start)
        self.b_stop = QPushButton('Зупинити')
        self.b_stop.setEnabled(False)
        self.b_stop.setAutoDefault(False)
        self.b_stop.clicked.connect(self._stop)
        h.addWidget(self.b_stop)
        h.addStretch(1)
        b = QPushButton('Закрити')
        b.setAutoDefault(False)
        b.clicked.connect(self.close)
        h.addWidget(b)
        lay.addLayout(h)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(10)
        lay.addWidget(self.bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        lay.addWidget(self.log, 1)
        self._svc_changed()

    def _box(self, lay, title):
        f = QFrame()
        f.setObjectName('Panel')
        fl = QVBoxLayout(f)
        fl.setContentsMargins(10, 8, 10, 10)
        fl.setSpacing(3)
        lab = QLabel(title)
        lab.setObjectName('Section')
        fl.addWidget(lab)
        lay.addWidget(f)
        return f, fl

    # ------------------------------------------------------------ налаштування гри
    def _cfg_path(self):
        return os.path.join(self.pr.xl, SETTINGS)

    def _load_cfg(self):
        try:
            with open(self._cfg_path(), encoding='utf-8') as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_cfg(self):
        self.cfg.update({'сервіс': self._svc(), 'модель': self._model_id(),
                         'вказівки': self.style_t.toPlainText().strip()})
        try:
            with open(self._cfg_path(), 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    def _svc(self):
        return next((v for v, rb in self.svc_btns.items() if rb.isChecked()), 'claude')

    def _model_id(self):
        return next((m for m, lab, _a, _b in engines.CLAUDE_MODELS if lab == self.model.currentText()),
                    engines.CLAUDE_MODELS[0][0])

    # ------------------------------------------------------------ обсяг
    def _scope(self):
        return 'sel' if self.r_sel.isChecked() else 'all' if self.r_all.isChecked() else 'node'

    def _rows(self):
        s = self._scope()
        if s == 'sel':
            src = [self.pr.by_key[k] for k in self.sel if k in self.pr.by_key]
        elif s == 'node':
            src = self.ed._node_rows(self.ed.node)
        else:
            src = self.pr.rows
        return job.pick(self.pr, src, redo=self.redo.isChecked(), with_tr=self.with_tr.isChecked())

    def _recount(self):
        n = lambda v: f'{v:,}'.replace(',', ' ')
        cnt = lambda rows: len(job.pick(self.pr, rows, self.redo.isChecked(), self.with_tr.isChecked()))
        sel = [self.pr.by_key[k] for k in self.sel if k in self.pr.by_key]
        self.r_sel.setText(f'вибрані рядки ({n(cnt(sel))})')
        self.r_sel.setEnabled(bool(sel))
        self.r_node.setText(f'розділ дерева «{self.node_name}» ({n(cnt(self.ed._node_rows(self.ed.node)))})')
        self.r_all.setText(f'уся гра ({n(cnt(self.pr.rows))})')
        rows = self._rows()
        chars = sum(len(r['e']['src']) for r in rows)
        words = sum(self.pr.words(r) for r in rows)
        est = ''
        try:
            eng = self._engine(quiet=True)
            if eng is not None and rows:
                usd = eng.estimate(len(rows), chars)
                est = (' · орієнтовно ' + (f'${usd:.2f}' if usd >= 0.01 else 'менше $0.01')
                       if usd else ' · безкоштовно (у межах ліміту плану)')
        except engines.MTError as ex:
            est = f' · {ex}'
        self.info.setText(f'До перекладу: {n(len(rows))} рядків (однакові — один раз), {n(words)} слів, '
                          f'{n(chars)} знаків{est}.\nМашинний іде в поле «Машинний», не в переклад — у гру '
                          'сам не потрапить.')

    def _svc_changed(self):
        s = self._svc()
        self.model_row.setVisible(s == 'claude')
        self._lib_state()
        k = keys.get(s)
        self.key_info.setText((f'Ключ: {keys.shown(k)}' if k else 'Ключа ще немає — «Ключ…». ') + ' · ' + KEY_HELP[s])
        self._recount()

    def _set_key(self):
        s = self._svc()
        k, ok = QInputDialog.getText(self, 'Ключ', f'Ключ для {dict(SERVICES)[s]}\n({KEY_HELP[s]}).\n'
                                     'Зберігається лише на цьому ПК (mt.key), порожньо — прибрати.',
                                     QLineEdit.Password)
        if not ok:
            return
        keys.put(s, k)
        self._svc_changed()

    def _lib_state(self):
        """Рядок «бібліотека для Claude»: видно, лише коли вибрано Claude, а її немає чи вона стара."""
        st = engines.anthropic_state() if self._svc() == 'claude' else 'ok'
        self.lib_row.setVisible(st != 'ok' or getattr(self, '_lib_busy', False))
        if getattr(self, '_lib_busy', False):
            return
        self.b_lib.setEnabled(True)
        self.b_lib.setText('Оновити' if st == 'old' else 'Встановити')
        self.lib_info.setText('Для Claude потрібна бібліотека anthropic (~20 МБ, раз). DeepL і Google — без неї.'
                              if st == 'missing' else
                              'Бібліотека anthropic застаріла — для Claude її треба оновити.')

    def _install_lib(self):
        self._lib_busy = True
        self.b_lib.setEnabled(False)
        self.lib_info.setText('Встановлюю бібліотеку anthropic… (до хвилини, потрібен інтернет)')

        def install():                          # (не «job»: так звати модуль mt.job)
            ok, msg = engines.install_anthropic()
            self.bridge.got.emit('lib', (ok, msg))
        threading.Thread(target=install, daemon=True).start()

    # ------------------------------------------------------------ сервіс
    def _engine(self, quiet=False):
        s = self._svc()
        if s == 'claude':
            if engines.anthropic_state() != 'ok':
                raise engines.MTError('спершу встанови бібліотеку для Claude — кнопка «Встановити» вище')
            return engines.Claude(self._model_id(), self.ed.game_title(), self.style_t.toPlainText(),
                                  self.ed.terms, job.names(self.pr))
        if s == 'deepl':
            return engines.DeepL()
        return engines.Google()

    def _start(self):
        if self.thread is not None:
            return
        if self.ed.ctx is None:              # межі ширини редактор рахує у фоні
            self._say('Чекаю, поки редактор порахує межі ширини…')
            QTimer.singleShot(700, self._start)
            return
        rows = self._rows()
        if not rows:
            self._say('Нема чого перекладати: усе вибране вже має машинний чи свій переклад.')
            return
        settings = self.ed.settings if self.ed.settings is not None else {}
        if not settings.get('mt_consent'):
            if QMessageBox.question(
                    self, 'Машинний переклад',
                    'Текст гри (оригінал, японська, ваші примітки, глосарій і вже перекладені імена) '
                    'буде надіслано вибраному сервісу перекладу в інтернеті.\n\nСервіси платні за обсяг '
                    '(крім безкоштовного ліміту DeepL) — гроші знімає сервіс з вашого рахунку.\n\nПогодитися?') \
                    != QMessageBox.Yes:
                return
            self.ed._save_setting('mt_consent', True)
        try:
            self.eng = self._engine()
        except engines.MTError as ex:
            self._say(f'⚠ {ex}')
            return
        self._save_cfg()
        self.ed._commit()
        self.cancel.clear()
        self.bar.setMaximum(max(1, len(rows)))
        self.bar.setValue(0)
        self.b_start.setEnabled(False)
        self.b_stop.setEnabled(True)
        self._say(f'Перекладаю {len(rows)} рядків через {self.eng.label}…')
        j = job.Job(self.pr, self.eng, rows, self.ed.ctx, self.ed.terms, self.ed.refs(),
                    self.bridge.got.emit, self.cancel)
        self.thread = threading.Thread(target=self._run, args=(j,), daemon=True)
        self.thread.start()

    def _run(self, j):
        try:
            j.run()
        except Exception as ex:                                 # noqa: BLE001
            self.bridge.got.emit('log', f'⚠ Помилка: {ex}')
            self.bridge.got.emit('done', (j.ok, j.failed, j.warned))

    def _stop(self):
        self.cancel.set()
        self._say('Зупиняю після поточного пакета…')

    def _got(self, kind, data):
        """Повідомлення фонового перекладу — у головному потоці."""
        if kind == 'result':
            # результати (за них уже заплачено) пишемо, навіть коли вікно вже закрили
            changed = []
            for k, text in data:
                changed += self.pr.set_mt(k, text, self.eng.label)
            self.ed._mt_changed(changed)
            return
        if kind == 'done':
            self._finish(*data)
            return
        if not self.isVisible():
            return
        if kind == 'lib':                       # бібліотеку для Claude встановлено (чи ні)
            ok, msg = data
            self._lib_busy = False
            self._lib_state()
            if not ok or self.lib_row.isVisible():
                self.lib_info.setText(('Не вдалося: ' if not ok else '') + msg)
                self.lib_row.setVisible(True)
            self._say(f'Бібліотека anthropic: {msg}.')
            self._recount()
            return
        if kind == 'progress':
            self.bar.setValue(data[0])
            self._say(f'{data[0]} / {data[1]} · {self.eng.spent()}', replace=True)
        elif kind == 'log':
            self._say(data)
        elif kind == 'fatal':
            QMessageBox.warning(self, 'Машинний переклад', data)

    def _finish(self, ok, failed, warned):
        self.thread = None
        self.b_start.setEnabled(True)
        self.b_stop.setEnabled(False)
        self.ed._save()
        self.ed._levels_state()
        self.ed._count()
        self._say(f'Готово: перекладено {ok}' + (f', з попередженнями {warned}' if warned else '')
                  + (f', не вдалося {failed}' if failed else '') + f'. {self.eng.spent()}.\n'
                  'У редакторі: фільтр «Лише машинний», кнопка «Взяти в переклад» (Ctrl+M).')
        self._recount()

    def _say(self, msg, replace=False):
        if replace and self._last_progress:
            c = self.log.textCursor()
            c.movePosition(QTextCursor.End)
            c.select(QTextCursor.BlockUnderCursor)       # останній рядок разом з переносом перед ним
            c.removeSelectedText()
        self.log.appendPlainText(msg)
        self.log.moveCursor(QTextCursor.End)
        self.log.ensureCursorVisible()
        self._last_progress = replace

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:            # Escape — як «Закрити» (з питанням, якщо переклад іде)
            self.close()
            return
        super().keyPressEvent(ev)

    def closeEvent(self, ev):
        if self.thread is not None:
            if QMessageBox.question(self, 'Машинний переклад', 'Переклад ще йде. Зупинити й закрити?') \
                    != QMessageBox.Yes:
                ev.ignore()
                return
            self.cancel.set()
        self._save_cfg()
        super().closeEvent(ev)
