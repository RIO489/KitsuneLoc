# -*- coding: utf-8 -*-
"""Вікно «Терміни» на Qt — те саме, що terms_window.py (Tk), інший вигляд.

Ліворуч — глосарій (glossary.py): пошук, додати/змінити/видалити, імпорт імен з книг і
глосарію Crowdin. Праворуч — усі рядки, де трапляється шукане слово, з наявним перекладом
(у режимі редактора — з проєкту, інакше — з work). Подвійний клік чи Enter по такому
рядку — перейти до нього (app._goto: редактор або книга Excel).

Жанр гри (JRPG…) підключає базу загальних термінів (glossary.genres); вони показані
сірим, свій термін з тим самим словом їх перекриває.

Від головного вікна: dirs(), cur['game'], get_project(), _goto(ціль), editor_win (у нього
reload_terms(), якщо є), theme.
"""
import os, threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSplitter,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

import glossary
from qtui import theme as qtheme

LIMIT = 400                     # скільки рядків «як перекладено» показувати
NO_GENRE = '— немає —'


class Bridge(QObject):
    """Фоновий потік -> вікно (сигнали Qt потокобезпечні)."""
    got = Signal(str, object)


def _alive(w):
    try:
        import shiboken6
        return shiboken6.isValid(w)
    except Exception:                                       # noqa: BLE001
        return True


class TermsWindow(QWidget):
    def __init__(self, app):
        super().__init__(app, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.app = app
        work, xl, _o, _b = app.dirs()
        self.work, self.xl = work, xl
        self.game = app.cur['game']
        # рядок праворуч несе (source, id, оригінал) у Qt.UserRole
        import project
        self.project = app.get_project() if project.enabled(xl) else None
        self.terms = glossary.load(xl)
        self.corpus = None                       # [(оригінал, переклад, де, source, id)] — у фоні
        self.t = qtheme.palette(getattr(app, 'theme', 'stars'))
        self.setWindowTitle('Терміни — ' + os.path.basename(xl))
        self.resize(1180, 680)
        self.setMinimumSize(900, 480)
        self.bridge = Bridge()
        self.bridge.got.connect(self._got)
        self.t_conc = QTimer(self, singleShot=True, timeout=lambda: self._concordance(self.q.text().strip()))
        self._build()
        self._fill_terms()
        threading.Thread(target=self._load_corpus, daemon=True).start()

    # ------------------------------------------------------------------ вигляд
    def _panel(self):
        f = QFrame()
        f.setObjectName('Panel')
        lay = QVBoxLayout(f)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        self.back.glow(f)
        return f, lay

    def _hint(self, text):
        h = QLabel(text)
        h.setObjectName('Hint')
        h.setWordWrap(True)
        return h

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.back = qtheme.Backdrop(getattr(self.app, 'theme', 'stars'))
        outer.addWidget(self.back)
        root = QVBoxLayout(self.back)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.addWidget(QLabel('Пошук слова:'))
        self.q = QLineEdit()
        self.q.setFixedWidth(280)
        self.q.textChanged.connect(lambda _t: self._changed())
        top.addWidget(self.q)
        h = QLabel('англійською або українською: і в глосарії, і в перекладі книг')
        h.setObjectName('Hint')
        top.addWidget(h)
        top.addStretch(1)
        top.addWidget(QLabel('Жанр гри:'))
        self.genre = QComboBox()
        self.genre.addItems([NO_GENRE] + glossary.genres())
        self.genre.setCurrentText(glossary.genre(self.xl) or NO_GENRE)
        self.genre.currentTextChanged.connect(lambda _t: self._set_genre())
        top.addWidget(self.genre)
        root.addLayout(top)

        self.split = QSplitter(Qt.Horizontal)
        self.split.setHandleWidth(14)
        self.split.splitterMoved.connect(lambda *_a: self.back.update())
        root.addWidget(self.split, 1)

        # --- глосарій
        left, ll = self._panel()
        lab = QLabel('Глосарій')
        lab.setObjectName('Head')
        ll.addWidget(lab)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(['Англійською', 'Українською', 'Примітка'])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        for i, w in enumerate((150, 160, 140)):
            self.tree.setColumnWidth(i, w)
        self.tree.currentItemChanged.connect(lambda *_a: self._pick())
        ll.addWidget(self.tree, 1)

        form = QGridLayout()
        form.setHorizontalSpacing(8)
        self.en, self.ua, self.note = QLineEdit(), QLineEdit(), QLineEdit()
        for r, (label, w) in enumerate((('Англійською:', self.en), ('Українською:', self.ua),
                                        ('Примітка:', self.note))):
            form.addWidget(QLabel(label), r, 0)
            form.addWidget(w, r, 1)
        form.addWidget(self._hint('кілька варіантів — через «/»: Нептун/Нептуна'), 3, 1)
        ll.addLayout(form)
        row = QHBoxLayout()
        for text, fn, accent in (('Додати / зберегти', self._save, True), ('Видалити', self._delete, False),
                                 ('Очистити поля', self._clear, False)):
            b = QPushButton(text)
            if accent:
                b.setObjectName('Accent')
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        for text, fn in (('Забрати з Crowdin…', self._import_crowdin), ('Додати імена з книг', self._import_names)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        ll.addLayout(row)
        self.split.addWidget(left)

        # --- як перекладено в книгах
        right, rl = self._panel()
        self.head = QLabel('Як перекладено в книгах')
        self.head.setObjectName('Head')
        rl.addWidget(self.head)
        self.conc = QTreeWidget()
        self.conc.setColumnCount(3)
        self.conc.setHeaderLabels(['Оригінал', 'Переклад', 'Де'])
        self.conc.setRootIsDecorated(False)
        self.conc.setUniformRowHeights(True)
        self.conc.header().setStretchLastSection(False)
        self.conc.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.conc.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.conc.setColumnWidth(2, 110)
        self.conc.itemActivated.connect(lambda *_a: self._go())       # подвійний клік і Enter
        self.conc.setContextMenuPolicy(Qt.CustomContextMenu)
        self.conc.customContextMenuRequested.connect(self._conc_menu)
        rl.addWidget(self.conc, 1)
        rl.addWidget(self._hint('Подвійний клік по рядку — перейти до нього й перекласти.'))
        self.status = self._hint('Читаю переклад з книг…')
        rl.addWidget(self.status)
        self.split.addWidget(right)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 2)
        self.split.setSizes([460, 700])

        bot = QHBoxLayout()
        bot.addWidget(self._hint('Глосарій зберігається одразу. Колонка «Терміни» в книгах і перевірка '
                                 '(«термін … у перекладі не знайдено») оновляться після «1» / '
                                 '«Перевірити переклад».'), 1)
        b = QPushButton('Закрити')
        b.clicked.connect(self.close)
        bot.addWidget(b)
        root.addLayout(bot)
        self.q.setFocus()

    # ---------------------------------------------------------------- глосарій
    def _fill_terms(self):
        q = self.q.text().strip().lower()
        self.tree.blockSignals(True)
        self.tree.clear()
        dim = QColor(self.t.get('dim', '#6a6a6a'))
        items = []
        for k, t in enumerate(self.terms):
            if q and q not in t['en'].lower() and q not in (t.get('ua') or '').lower():
                continue
            note = t.get('примітка', '')
            if t.get('жанр'):
                note = f'база {t["жанр"]}' + (f': {note}' if note else '')
            it = QTreeWidgetItem([t['en'], t.get('ua', ''), note])
            it.setData(0, Qt.UserRole, k)
            if t.get('жанр'):
                for c in range(3):
                    it.setForeground(c, dim)
            items.append(it)
        self.tree.addTopLevelItems(items)
        self.tree.setCurrentItem(None)
        self.tree.blockSignals(False)

    def _pick(self):
        it = self.tree.currentItem()
        if it is None:
            return
        t = self.terms[it.data(0, Qt.UserRole)]
        self.en.setText(t['en'])
        self.ua.setText(t.get('ua', ''))
        self.note.setText(t.get('примітка', ''))
        self._concordance(t['en'])

    def _store(self):
        try:
            glossary.save(self.xl, self.terms)
        except OSError as ex:
            QMessageBox.critical(self, 'Не вдалося зберегти', str(ex))
            return False
        self.terms = glossary.load(self.xl)
        self._fill_terms()
        self._refresh_editor()
        return True

    def _refresh_editor(self):
        """Відкритий редактор бере терміни при відкритті — оновити й у ньому."""
        win = getattr(self.app, 'editor_win', None)
        try:
            if win is not None and _alive(win) and win.isVisible() and hasattr(win, 'reload_terms'):
                win.reload_terms()
        except RuntimeError:                                # вікно редактора вже знищено
            pass

    def _set_genre(self):
        name = '' if self.genre.currentText() == NO_GENRE else self.genre.currentText()
        try:
            glossary.set_genre(self.xl, name)
        except OSError as ex:
            QMessageBox.critical(self, 'Не вдалося зберегти', str(ex))
            return
        self.terms = glossary.load(self.xl)
        self._fill_terms()
        self._refresh_editor()
        n = sum(1 for t in self.terms if t.get('жанр'))
        self.status.setText(f'Жанр: {name} — додано термінів з бази: {n}' if name else 'Базу жанру вимкнено.')

    def _save(self):
        en, ua = self.en.text().strip(), self.ua.text().strip()
        if not en:
            return
        # термін бази жанру не правимо — поруч з'являється свій, і він його перекриває
        t = next((x for x in self.terms if x['en'].lower() == en.lower() and not x.get('жанр')), None)
        if t is None:
            t = {'en': en}
            self.terms.append(t)
        t.update({'en': en, 'ua': ua})
        if self.note.text().strip():
            t['примітка'] = self.note.text().strip()
        else:
            t.pop('примітка', None)
        if self._store():
            self.status.setText(f'Збережено: {en} → {ua}')

    def _delete(self):
        en = self.en.text().strip().lower()
        if not any(x['en'].lower() == en and not x.get('жанр') for x in self.terms):
            if any(x['en'].lower() == en for x in self.terms):
                QMessageBox.information(self, 'Терміни', 'Це термін з бази жанру — його не видалити. '
                                        'Щоб перекладати інакше, впиши свій переклад і натисни '
                                        '«Додати / зберегти»: свій термін перекриє термін бази.')
            return
        n = len(self.terms)
        self.terms = [x for x in self.terms if x['en'].lower() != en or x.get('жанр')]
        if len(self.terms) != n and self._store():
            self._clear()

    def _clear(self):
        for w in (self.en, self.ua, self.note):
            w.setText('')

    def _to_form(self):
        """Шукане слово — в поле «Англійською» (щоб додати в глосарій)."""
        if not self.en.text().strip():
            self.en.setText(self.q.text().strip())

    def _go(self):
        """Подвійний клік по рядку: відкрити його для перекладу — у редакторі
        або в книзі Excel (як клік по попередженню в головному вікні)."""
        it = self.conc.currentItem()
        where = it.data(0, Qt.UserRole) if it is not None else None
        if not where:
            return
        source, eid, src = where
        self.app._goto((self.game, source, eid, src))

    def _conc_menu(self, pos):
        it = self.conc.itemAt(pos)
        if it is not None:
            self.conc.setCurrentItem(it)
        m = QMenu(self)
        a = m.addAction('Перейти до рядка', self._go)
        a.setEnabled(it is not None)
        m.addAction('Шукане слово — у глосарій', self._to_form)
        m.exec(self.conc.viewport().mapToGlobal(pos))

    def _import_names(self):
        """Імена мовців з книг (з уже перекладеними) — у глосарій, якщо їх там ще немає."""
        import sheets
        have = {t['en'].lower() for t in self.terms}
        added = 0
        for source, entries in sheets.collect(self.work):
            for e, _scene, who, kind in sheets._rows(source, entries):
                is_name = kind == 'name' or str(who).startswith('IDS_EVT_TITLE_NAME_SUB')
                src, tr = (e.get('src') or '').strip(), (e.get('tr') or '').strip()
                if is_name and src and tr and src.lower() not in have and len(src) < 40:
                    self.terms.append({'en': src, 'ua': tr, 'примітка': "ім'я"})
                    have.add(src.lower())
                    added += 1
        if added and self._store():
            self.status.setText(f'Додано імен: {added}')
        elif not added:
            self.status.setText('Нових перекладених імен немає.')

    def _import_crowdin(self, paths=None):
        """Глосарій, вивантажений з Crowdin (.tbx / .csv / .xlsx; можна кілька файлів —
        варіанти перекладу об'єднуються), — у свої терміни."""
        if paths is None:
            paths, _f = QFileDialog.getOpenFileNames(
                self, 'Глосарій Crowdin (можна вибрати кілька файлів)', '',
                'Глосарій Crowdin (*.tbx *.csv *.xlsx);;Усі файли (*.*)')
        if not paths:
            return
        try:
            got = glossary.read_many(paths)
        except Exception as ex:                      # битий файл, не той формат   # noqa: BLE001
            QMessageBox.critical(self, 'Crowdin', f'Не вдалося прочитати глосарій:\n{ex}')
            return
        if not got:
            QMessageBox.information(self, 'Crowdin', 'У файлі немає термінів з перекладом.')
            return
        _t, added, same, diff = glossary.merge(self.terms, got)
        replace = False
        if diff:
            show = '\n'.join(f'{en}: {mine} → {new}' for en, mine, new in diff[:12])
            more = f'\n… і ще {len(diff) - 12}' if len(diff) > 12 else ''
            ans = QMessageBox.question(
                self, 'Crowdin', f'{len(diff)} термінів у глосарії вже є, але з іншим перекладом '
                f'(свій → з Crowdin):\n\n{show}{more}\n\nЗамінити їх перекладом з Crowdin?\n'
                '«Так» — замінити, «Ні» — лишити свої, «Скасувати» — нічого не додавати.',
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if ans not in (QMessageBox.Yes, QMessageBox.No):
                return
            replace = ans == QMessageBox.Yes
        terms, added, same, diff = glossary.merge(self.terms, got, replace=replace)
        self.terms = terms
        if (added or (replace and diff)) and not self._store():
            return
        msg = f'З Crowdin: нових термінів {added}, уже були такі самі {same}'
        if diff:
            msg += f', з іншим перекладом {len(diff)} ({"замінено" if replace else "лишено свої"})'
        self.status.setText(msg + '.')

    # -------------------------------------------------------- як перекладено
    def _load_corpus(self):
        """[(оригінал, переклад, де, source, id)]. У режимі редактора переклад —
        з проєкту (там найсвіжіший, з незбереженими ще правками), інакше — з work."""
        import sheets
        rows = []
        try:
            pr = self.project
            if pr is not None:
                for r in pr.rows:
                    e = r['e']
                    if e.get('src'):
                        rows.append((e['src'], e.get('tr') or '', sheets._scene(r['source']),
                                     r['source'], e['id']))
            else:
                for source, entries in sheets.collect(self.work):
                    where = sheets._scene(source)
                    for e in entries:
                        if e.get('src'):
                            rows.append((e['src'], e.get('tr') or '', where, source, e['id']))
        except Exception as ex:                                       # noqa: BLE001
            self._emit('err', str(ex))
            return
        self._emit('corpus', rows)

    def _emit(self, kind, val):
        try:
            self.bridge.got.emit(kind, val)
        except RuntimeError:                                # вікно вже закрили
            pass

    def _got(self, kind, val):
        if kind == 'err':
            self.status.setText(f'Не вдалося прочитати: {val}')
        elif kind == 'corpus':
            self.corpus = val
            self.status.setText(f'Рядків у книгах: {len(val)}. Введи слово для пошуку.')
            self._changed(0)

    def _changed(self, delay=250):
        self._fill_terms()
        self.t_conc.start(delay)

    def _concordance(self, word):
        self.t_conc.stop()
        self.conc.clear()
        if self.corpus is None or len(word) < 2:
            self.head.setText('Як перекладено в книгах')
            return
        rx = glossary.pattern(word)
        low = word.lower()
        hits = [r for r in self.corpus if rx.search(r[0]) or (r[1] and low in r[1].lower())]
        hits.sort(key=lambda r: (not r[1], len(r[0])))          # спершу перекладені, коротші
        items = []
        for src, tr, where, source, eid in hits[:LIMIT]:
            it = QTreeWidgetItem([src.replace('\n', ' ⏎ '), tr.replace('\n', ' ⏎ ') or '—', where])
            it.setToolTip(0, src)
            if tr:
                it.setToolTip(1, tr)
            it.setData(0, Qt.UserRole, (source, eid, src))
            items.append(it)
        self.conc.addTopLevelItems(items)
        done = sum(1 for r in hits if r[1])
        self.head.setText(f'Як перекладено в книгах: «{word}»')
        self.status.setText(f'Знайдено рядків: {len(hits)}, з них перекладено {done}'
                            + (f' (показано перші {LIMIT})' if len(hits) > LIMIT else ''))
