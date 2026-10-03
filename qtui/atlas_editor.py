# -*- coding: utf-8 -*-
"""«Написи на картинках» на Qt — те саме, що atlas_editor.py (Tk), інший вигляд.

Пишеш переклад — поруч одразу видно, як кнопка виглядатиме в грі (усі її варіанти:
звичайна, вибрана тощо). Зберігається туди ж, де й решта перекладу: у редактор
(переклад.json), а якщо гра ще на книгах — у книгу Excel (Mary Skelter — «24 Написи на
картинках», Neptunia — «22 Написи на картинках»).

Без Tk лише вигляд: атласи, варіанти напису, пробні правки й малювання — ті самі функції
Tk-модуля atlas_editor (Atlases, NepTextures, variants, tuned, restyled, render; Tk-вікна
вони не чіпають). Вибір стилю, «Новий стиль…» і «Усі стилі» — з qtui/textscan_window.py.
"""
import os, threading

from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QDialog, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPushButton, QScrollArea, QSlider, QSplitter, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from atlas_editor import (BG, BOOKS, DELAY, LOOK, MARKS, MAX_VARIANTS, MAX_W, SHEET, Atlases, NepTextures,
                          render, restyled, tuned, variants)
from maryskelter import atlas as atl
from qtui import theme as qtheme
from qtui.textscan_window import (Bridge, Card, StyleEditor, StylePicker, color_button, float_slider,
                                  paint_button, pil_pixmap)


def preview_pixmap(im):
    """Як to_photo у Tk: на темному тлі, ширше за MAX_W — зменшити."""
    return pil_pixmap(im, min(1.0, MAX_W / max(1, im.width)), BG)


class KeyEntry(QLineEdit):
    """Поле перекладу: Enter / ↓ — наступний напис, ↑ — попередній."""

    def __init__(self, step):
        super().__init__()
        self.step = step

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Down):
            self.step(1)
        elif ev.key() == Qt.Key_Up:
            self.step(-1)
        else:
            super().keyPressEvent(ev)


# ============================================================ вікно
class AtlasEditor(QMainWindow):
    def __init__(self, app, goto=None):
        super().__init__()
        self.app = app
        _work, xl, _out, bk = app.dirs()
        self.game = app.cur['game']
        self.book_name = BOOKS[self.game]
        self.book = os.path.join(xl, self.book_name)
        import project
        # з версії 1.7 переклад може жити в програмі (редактор) — тоді пишемо туди, не в книгу
        self.pr = app.get_project() if project.enabled(xl) else None
        if self.pr is None and not os.path.exists(self.book):
            raise RuntimeError(f'Книги «{self.book_name[:-5]}» ще немає — '
                               'спершу натисни «1. Дістати текст з гри».')
        if self.game == 'nep':
            from neptunia import atlas as natl
            self.marks = natl.load_marks()
            self.atlases = NepTextures(bk, app.root_dir())
        else:
            self.marks = atl.load_marks('написи.json')
            self.atlases = Atlases(bk, app.root_dir())
        self.styles = atl.load_styles()
        self.name = MARKS[self.game]            # для редактора стилю (StyleEditor)
        self.restyle = {}                        # ключ -> {стиль у розмітці: пробний стиль} (ще не записано)
        self.suggest = {}                        # ключ -> підібрані правки стилю (ще не в розмітці)
        self.rows = self._read_book()           # [{id, src, tr, where}]
        self.edits = {}                          # id -> новий переклад (ще не збережений)
        self.cur = None
        self.gen = 0                             # номер рендеру: застарілі результати відкидаємо
        self.last_res = None
        self.style_row = 0
        self.items = {}
        self.alive = True
        self._loading = False
        self.t = qtheme.palette(getattr(app, 'theme', 'stars'))
        self.bridge = Bridge()
        self.bridge.got.connect(self._got)
        self.t_render = QTimer(self, singleShot=True, timeout=self._render)

        self.setWindowTitle('Написи на картинках — переклад з прев\'ю')
        self.resize(1400, 780)
        self.setMinimumSize(900, 520)
        self._build()
        self._fill()
        if self.rows and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        if goto:
            self.goto(goto)

    def goto(self, key):
        """Стати на напис `key` (з редактора перекладу: рядок книги написів) — фільтри скидаємо."""
        if not any(r['id'] == key for r in self.rows):
            return False
        self.q.blockSignals(True)
        self.q.setText('')
        self.q.blockSignals(False)
        self.only_todo.blockSignals(True)
        self.only_todo.setChecked(False)
        self.only_todo.blockSignals(False)
        self._fill()
        it = self.items.get(key)
        if it is not None:
            self.tree.setCurrentItem(it)
            self.tree.scrollToItem(it)
        return True

    # ---------------------------------------------------------------- книга
    def _read_book(self):
        if self.pr is not None:
            return [{'id': r['e']['id'], 'src': r['e']['src'], 'tr': r['e'].get('tr', ''),
                     'where': r['who'] if isinstance(r['who'], str) else ', '.join(r['who'] or []),
                     'key': r['k']}
                    for r in self.pr.rows if r['source'].startswith('@атлас/')]
        from openpyxl import load_workbook
        wb = load_workbook(self.book, read_only=True, data_only=True)
        ws = wb[SHEET]
        it = ws.iter_rows(values_only=True)
        head = list(next(it))
        ix = {h: head.index(h) for h in ('id', 'Оригінал (EN)', 'Переклад', 'Хто / ключ')}
        rows = []
        for r in it:
            if not r or r[ix['id']] is None:
                continue
            rows.append({'id': str(r[ix['id']]), 'src': r[ix['Оригінал (EN)']] or '',
                         'tr': (r[ix['Переклад']] or '').strip() if isinstance(r[ix['Переклад']], str) else '',
                         'where': r[ix['Хто / ключ']] or ''})
        wb.close()
        return rows

    def _save(self):
        if not self.edits:
            return True
        if self.pr is not None:
            keys = {r['id']: r['key'] for r in self.rows}
            changed = []
            for i, v in self.edits.items():
                changed += self.pr.set_tr(keys[i], v)
            self.pr.save()
            win = getattr(self.app, 'editor_win', None)
            try:
                if win is not None and win.winfo_exists():
                    if hasattr(win, 'model'):           # Qt-редактор
                        win.model.changed(changed)
                    else:                               # Tk-редактор
                        win._update_rows(changed)
            except RuntimeError:                         # вікно редактора вже знищено
                pass
            for r in self.rows:
                if r['id'] in self.edits:
                    r['tr'] = self.pr.tr(r['key'])
            n = len(self.edits)
            self.edits.clear()
            self.saved.setText(f'Збережено: {n}. Далі — «2. Залити переклад у гру».')
            self._fill(keep=True)
            return True
        lock = os.path.join(os.path.dirname(self.book), '~$' + self.book_name)
        if os.path.exists(lock):
            QMessageBox.warning(self, 'Книга відкрита в Excel',
                                f'Закрий «{self.book_name[:-5]}» в Excel і збережи ще раз — '
                                'інакше Excel потім перезапише ці зміни.')
            return False
        from openpyxl import load_workbook
        wb = load_workbook(self.book)
        ws = wb[SHEET]
        head = [c.value for c in ws[1]]
        i_id, i_tr = head.index('id'), head.index('Переклад')
        n = 0
        for row in ws.iter_rows(min_row=2):
            k = str(row[i_id].value)
            if k in self.edits:
                row[i_tr].value = self.edits[k] or None
                n += 1
        try:
            wb.save(self.book)
        except PermissionError:
            QMessageBox.warning(self, 'Не вдалося зберегти',
                                'Книгу зайнято (відкрита в Excel?). Закрий її й спробуй ще раз.')
            return False
        for r in self.rows:
            if r['id'] in self.edits:
                r['tr'] = self.edits[r['id']]
        self.edits.clear()
        self.saved.setText(f'Збережено в книгу: {n}. Далі — «2. Залити переклад у гру».')
        self._fill(keep=True)
        return True

    def closeEvent(self, ev):
        if self.edits:
            ans = QMessageBox.question(self, 'Незбережені зміни',
                                       f'Змінено написів: {len(self.edits)}. Зберегти в книгу?',
                                       QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if ans == QMessageBox.Cancel or (ans == QMessageBox.Yes and not self._save()):
                ev.ignore()
                return
        self.alive = False
        self.gen += 1
        with self.atlases.lock:                  # розкодовані атласи — сотні МБ, звільнити одразу
            self.atlases.cache.clear()
        super().closeEvent(ev)

    def winfo_exists(self):
        return self.alive and self.isVisible()

    # ---------------------------------------------------------------- вигляд
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
        top.addWidget(QLabel('Пошук:'))
        self.q = QLineEdit()
        self.q.setFixedWidth(240)
        self.q.textChanged.connect(lambda _t: self._fill())
        top.addWidget(self.q)
        self.only_todo = QCheckBox('лише неперекладені')
        self.only_todo.toggled.connect(lambda _on: self._fill())
        top.addWidget(self.only_todo)
        top.addStretch(1)
        self.count = QLabel('')
        top.addWidget(self.count)
        root.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(12)
        split.splitterMoved.connect(lambda *_a: self.back.update())
        root.addWidget(split, 1)

        left, ll = self._panel()
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(['Оригінал', 'Переклад'])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(QHeaderView.Interactive)
        self.tree.setColumnWidth(0, 170)
        self.tree.currentItemChanged.connect(lambda _c, _p: self._select())
        ll.addWidget(self.tree)
        split.addWidget(left)

        right, rl = self._panel()
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(4, 2, 8, 2)
        lay.setSpacing(5)
        sa.setWidget(inner)
        rl.addWidget(sa)
        split.addWidget(right)
        split.setSizes([300, 1100])
        split.setStretchFactor(1, 1)

        self.head = QLabel('')
        self.head.setObjectName('Head')
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.where = self._hint(wrap=True)
        lay.addWidget(self.where)

        er = QHBoxLayout()
        er.addWidget(QLabel('Переклад:'))
        self.entry = KeyEntry(self._next)
        self.entry.setStyleSheet('font-size: 12pt;')
        self.entry.textChanged.connect(lambda _t: self._typed())
        er.addWidget(self.entry, 1)
        b = QPushButton('Лишити оригінал')
        b.clicked.connect(lambda: self.entry.setText(''))
        er.addWidget(b)
        lay.addLayout(er)

        # вигляд напису: підбір шрифту за оригіналом і «справжні літери»
        tools = QHBoxLayout()
        self.real = QCheckBox('Справжні літери з оригіналу')
        self.real.toggled.connect(lambda _on: self._real_toggled())
        tools.addWidget(self.real)
        b = QPushButton('Глянути різні шрифти…')
        b.clicked.connect(self._gallery)
        tools.addWidget(b)
        b = QPushButton('Повернути стандартний')
        b.clicked.connect(self._standard)
        tools.addWidget(b)
        self.b_apply = QPushButton('Записати в розмітку')
        self.b_apply.clicked.connect(self._apply_look)
        self.b_apply.setEnabled(False)
        tools.addWidget(self.b_apply)
        tools.addStretch(1)
        lay.addLayout(tools)
        # колір літер, обведення, квадратність — пробні правки стилю, як і шрифт
        paint = QHBoxLayout()
        paint.addWidget(QLabel('Літери:'))
        self.b_fill = color_button(40, 24)
        self.b_fill.clicked.connect(self._pick_fill)
        paint.addWidget(self.b_fill)
        paint.addSpacing(10)
        paint.addWidget(QLabel('Обведення:'))
        self.b_line = color_button(40, 24)
        self.b_line.clicked.connect(self._pick_line)
        paint.addWidget(self.b_line)
        self.line_w = QDoubleSpinBox()
        self.line_w.setRange(0, 8)
        self.line_w.setSingleStep(0.5)
        self.line_w.setDecimals(1)
        self.line_w.setFixedWidth(84)
        self.line_w.valueChanged.connect(lambda _v: self._line_width())
        paint.addWidget(self.line_w)
        paint.addWidget(QLabel('px'))
        paint.addSpacing(10)
        self.sq_lbl = QLabel('Квадратність 0.00')
        self.sq_lbl.setFixedWidth(130)
        paint.addWidget(self.sq_lbl)
        self.sq = QSlider(Qt.Horizontal)
        self.sq.setRange(0, 100)
        self.sq.setFixedWidth(140)
        self.sq.valueChanged.connect(lambda _v: self._square())
        paint.addWidget(self.sq)
        b = QPushButton('Як у стилі')
        b.clicked.connect(self._paint_reset)
        paint.addWidget(b)
        paint.addStretch(1)
        lay.addLayout(paint)
        self.look = self._hint(wrap=True)
        lay.addWidget(self.look)

        self.note = QLabel('')
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        grid = QGridLayout()
        grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        grid.setHorizontalSpacing(12)
        grid.addWidget(self._hint('Оригінал'), 0, 0)
        grid.addWidget(self._hint('У грі буде'), 0, 1)
        grid.addWidget(self._hint('Стиль'), 0, 2)
        self.cells, self.style_rows = [], []
        for k in range(MAX_VARIANTS):
            a, b = QLabel(), QLabel()
            for w in (a, b):
                w.setAlignment(Qt.AlignLeft | Qt.AlignTop)
                w.hide()
            grid.addWidget(a, k + 1, 0, Qt.AlignLeft | Qt.AlignTop)
            grid.addWidget(b, k + 1, 1, Qt.AlignLeft | Qt.AlignTop)
            self.cells.append((a, b))
            # стиль цього варіанта: вибір за групами, свій стиль на основі цього чи зміна свого
            sf = QWidget()
            sl = QVBoxLayout(sf)
            sl.setContentsMargins(0, 0, 0, 0)
            sl.setSpacing(3)
            pick = StylePicker(width=210)
            pick.changed.connect(lambda _n, k=k: self._restyle(k))
            sl.addWidget(pick)
            bf = QHBoxLayout()
            bn = QPushButton('Новий стиль…')
            bn.clicked.connect(lambda _c=False, k=k: self._edit_style(k, False))
            bf.addWidget(bn)
            b_own = QPushButton('Змінити свій…')
            b_own.clicked.connect(lambda _c=False, k=k: self._edit_style(k, True))
            bf.addWidget(b_own)
            sl.addLayout(bf)
            sf.hide()
            grid.addWidget(sf, k + 1, 2, Qt.AlignLeft | Qt.AlignTop)
            self.style_rows.append((sf, pick, b_own))
        lay.addLayout(grid)
        lay.addStretch(1)
        self._pickers_refresh()

        bot = QHBoxLayout()
        self.saved = self._hint('Enter / ↓ — наступний напис, ↑ — попередній.')
        bot.addWidget(self.saved, 1)
        b = QPushButton('Повідомити про цей напис…')
        b.clicked.connect(self._report)
        b.setEnabled(hasattr(self.app, 'open_feedback'))
        if not b.isEnabled():
            b.setToolTip('Звернення ще не перенесено в нову версію інтерфейсу')
        bot.addWidget(b)
        b = QPushButton('Зберегти в книгу')
        b.setObjectName('Accent')
        b.clicked.connect(self._save)
        bot.addWidget(b)
        b = QPushButton('Закрити')
        b.clicked.connect(self.close)
        bot.addWidget(b)
        root.addLayout(bot)

    def _report(self):
        """Звернення до власника про напис: текст + прев'ю «оригінал | переклад»."""
        if not hasattr(self.app, 'open_feedback'):
            return
        if not self.cur:
            self.app.open_feedback({'game': self.game, 'kind': 'вигляд'})
            return
        r = next(x for x in self.rows if x['id'] == self.cur)
        img = None
        res = self.last_res
        if res:
            a, b = res[0][0], res[0][1]
            img = Image.new('RGBA', (a.width + b.width + 8, max(a.height, b.height)), BG)
            img.alpha_composite(a, (0, 0))
            img.alpha_composite(b, (a.width + 8, 0))
        self.app.open_feedback({'game': self.game, 'kind': 'вигляд', 'image': img, 'row': {
            'source': r.get('source', '@атлас'), 'id': r['id'], 'src': r['src'],
            'tr': self._value(r), 'where': r.get('where', '')}})

    # ---------------------------------------------------------------- список
    def _value(self, r):
        return self.edits.get(r['id'], r['tr'])

    def _paint_item(self, it, r, v):
        it.setText(0, r['src'])
        it.setText(1, v or '—')
        col = (QColor(self.t['warn']) if r['id'] in self.edits else
               None if v else QColor(self.t['dim']))
        for c in (0, 1):
            if col is None:
                it.setData(c, Qt.ForegroundRole, None)
            else:
                it.setForeground(c, col)

    def _fill(self, keep=False):
        q = self.q.text().strip().lower()
        sel = self.cur
        self.tree.blockSignals(True)
        self.tree.clear()
        self.items = {}
        for r in self.rows:
            v = self._value(r)
            if self.only_todo.isChecked() and v:
                continue
            if q and q not in r['src'].lower() and q not in v.lower():
                continue
            it = QTreeWidgetItem()
            it.setData(0, Qt.UserRole, r['id'])
            self._paint_item(it, r, v)
            self.tree.addTopLevelItem(it)
            self.items[r['id']] = it
        done = sum(1 for r in self.rows if self._value(r))
        self.count.setText(f'перекладено {done} з {len(self.rows)}')
        if sel and sel in self.items:
            self.tree.setCurrentItem(self.items[sel])
            self.tree.scrollToItem(self.items[sel])
        self.tree.blockSignals(False)

    def _select(self):
        it = self.tree.currentItem()
        if it is None or it.data(0, Qt.UserRole) == self.cur:
            return
        self.cur = it.data(0, Qt.UserRole)
        r = next(x for x in self.rows if x['id'] == self.cur)
        self.head.setText(r['src'])
        self.where.setText(f'Де: {r["where"]}')
        self._loading = True
        self.entry.setText(self._value(r))
        self.real.setChecked(any(s.get('літери з оригіналу') for _src, _i, s in variants(self.marks, self.cur)))
        self._paint_show()
        self._loading = False
        self._look_status()
        self.entry.setFocus()
        self.entry.end(False)
        self._schedule(0)

    def _next(self, step=1):
        n = self.tree.topLevelItemCount()
        if not n:
            return
        it = self.items.get(self.cur)
        k = self.tree.indexOfTopLevelItem(it) if it is not None else -1
        nxt = self.tree.topLevelItem(max(0, min(n - 1, k + step)))
        self.tree.setCurrentItem(nxt)
        self.tree.scrollToItem(nxt)

    # ---------------------------------------------------------------- прев'ю
    def _typed(self):
        if self._loading or not self.cur:
            return
        r = next(x for x in self.rows if x['id'] == self.cur)
        v = self.entry.text().strip()
        if v == r['tr']:
            self.edits.pop(r['id'], None)
        else:
            self.edits[r['id']] = v
        it = self.items.get(r['id'])
        if it is not None:
            self._paint_item(it, r, v)
        self._schedule(DELAY)

    def _schedule(self, ms):
        self.t_render.start(ms)

    def _render(self):
        self.t_render.stop()
        self.gen += 1
        gen, key, text = self.gen, self.cur, self.entry.text().strip()
        if not key:
            return
        self.note.setText('малюю…')
        edit, real, restyle = self.suggest.get(key), self.real.isChecked(), self.restyle.get(key)
        atlases, marks, styles, game, br = self.atlases, self.marks, self.styles, self.game, self.bridge

        def work():
            try:
                res = render(atlases, marks, styles, key, text, game, edit, real, restyle)
                err = None
            except Exception as ex:                                   # noqa: BLE001
                res, err = [], str(ex)
            br.got.emit('show', (gen, text, res, err))

        threading.Thread(target=work, daemon=True).start()

    def _got(self, kind, val):
        if kind == 'show' and self.alive:
            self._show(*val)

    def _note(self, text, warn=False):
        self.note.setText(text)
        self.note.setStyleSheet(f'color: {self.t["warn"] if warn else self.t["dim"]};')

    def _show(self, gen, text, res, err):
        if gen != self.gen:
            return
        self.last_res = res
        for k, (a, b) in enumerate(self.cells):
            if k < len(res):
                a.setPixmap(preview_pixmap(res[k][0]))
                b.setPixmap(preview_pixmap(res[k][1]))
                a.show()
                b.show()
            else:
                a.clear()
                b.clear()
                a.hide()
                b.hide()
        self._style_rows_show()
        if err:
            self._note(f'Не вдалося намалювати: {err}', True)
            return
        if not text:
            self._note('Порожньо — у грі лишиться оригінальний напис.')
            return
        scale = min((x[2] for x in res), default=1.0)
        warns = [w for x in res for w in x[3]]
        if warns:
            self._note(f'Задовге: напис зменшено до {scale:.0%} — спробуй коротше.', True)
        elif scale < 0.97:
            how = 'трохи стиснуто' if scale >= 0.85 else 'помітно зменшено'
            self._note(f'Вміщається, {how} ({scale:.0%} від оригінального розміру).')
        else:
            self._note('Вміщається без зменшення.')

    # ------------------------------------------------------- вигляд напису
    def look_of(self, key):
        """Пробні правки вигляду (колір, обведення, квадратність) напису."""
        return {k: v for k, v in (self.suggest.get(key) or {}).items() if k in LOOK}

    def _style(self, key, tried=True):
        """Стиль першого варіанта напису з правками розмітки (і пробними)."""
        _src, _i, spec = variants(self.marks, key)[0]
        if tried:
            spec = restyled(spec, self.restyle.get(key))
        st = dict(self.styles[spec['стиль']])
        st.update(spec.get('правки', {}))
        if tried:
            st.update(self.suggest.get(key) or {})
        return st

    def _paint_show(self):
        """Кнопки кольорів і повзунок — за поточним стилем напису."""
        if not self.cur or not variants(self.marks, self.cur):
            return
        st = self._style(self.cur)
        fill = st.get('заливка', '#ffffff')
        first = fill[0] if isinstance(fill, list) else fill
        paint_button(self.b_fill, first, '⇅' if isinstance(fill, list) else '')    # ⇅ — градієнт
        o = st.get('обведення') or {}
        paint_button(self.b_line, o.get('колір', '#000000') if o.get('товщина') else None,
                     '' if o.get('товщина') else '—')
        self.line_w.setValue(float(o.get('товщина', 0)))
        self.sq.setValue(round(st.get('квадратність', 0) * 100))
        self.sq_lbl.setText(f'Квадратність {self.sq.value() / 100:.2f}')

    def _paint_refresh(self):
        self._loading = True
        self._paint_show()
        self._loading = False

    def _set_look(self, k, v):
        """Пробна правка вигляду; така сама, як у розмітці, — прибирається."""
        key = self.cur
        if not key:
            return
        e = dict(self.suggest.get(key) or {})
        if v == self._style(key, tried=False).get(k, 0 if k == 'квадратність' else None):
            e.pop(k, None)
        else:
            e[k] = v
        if e:
            self.suggest[key] = e
        else:
            self.suggest.pop(key, None)
        self._look_status()
        self._schedule(DELAY)

    @staticmethod
    def _with_alpha(new, old):
        """#rrggbb з вибору кольору + прозорість старого #rrggbbaa."""
        return new + old[7:9] if isinstance(old, str) and len(old) == 9 else new

    def _pick_fill(self):
        if not self.cur:
            return
        old = self._style(self.cur).get('заливка', '#ffffff')
        first = old[0] if isinstance(old, list) else old
        c = QColorDialog.getColor(QColor(first[:7]), self, 'Колір літер')
        if c.isValid():
            self._set_look('заливка', self._with_alpha(c.name(), first))
            self._paint_refresh()

    def _pick_line(self):
        if not self.cur:
            return
        o = dict(self._style(self.cur).get('обведення') or {})
        c = QColorDialog.getColor(QColor(o.get('колір', '#000000')[:7]), self, 'Колір обведення')
        if c.isValid():
            o['колір'] = self._with_alpha(c.name(), o.get('колір'))
            o['товщина'] = o.get('товщина') or 2
            self._set_look('обведення', o)
            self._paint_refresh()

    def _line_width(self):
        if self._loading or not self.cur:
            return
        w = max(0.0, min(8.0, float(self.line_w.value())))
        o = dict(self._style(self.cur).get('обведення') or {'колір': '#000000'})
        if w == o.get('товщина', 0):
            return
        o['товщина'] = w
        self._set_look('обведення', o)
        paint_button(self.b_line, o['колір'] if w else None, '' if w else '—')

    def _square(self):
        v = round(self.sq.value() / 100, 2)
        self.sq_lbl.setText(f'Квадратність {v:.2f}')
        if not self._loading:
            self._set_look('квадратність', v)

    def _paint_reset(self):
        """Прибрати пробні колір, обведення й квадратність (шрифт лишається)."""
        key = self.cur
        e = {k: v for k, v in (self.suggest.get(key) or {}).items() if k not in LOOK}
        if e:
            self.suggest[key] = e
        else:
            self.suggest.pop(key, None)
        self._paint_refresh()
        self._look_status()
        self._schedule(0)

    # ------------------------------------------------------- стиль напису
    def _pickers_refresh(self):
        names = atl.game_styles(self.styles, self.game)
        for _sf, pick, _b in self.style_rows:
            pick.refresh(self.styles, names)

    def _style_rows_show(self):
        """Стиль кожного варіанта (з пробною зміною) — у його рядку прев'ю."""
        vs = variants(self.marks, self.cur) if self.cur else []
        was, self._loading = self._loading, True
        try:
            for k, (sf, pick, b_own) in enumerate(self.style_rows):
                if k >= len(vs):
                    sf.hide()
                    continue
                name = restyled(vs[k][2], self.restyle.get(self.cur))['стиль']
                pick.set(name)
                b_own.setEnabled(bool(self.styles.get(name, {}).get('мій')))
                sf.show()
        finally:
            self._loading = was

    def _restyle(self, k):
        """Вибрано інший стиль для варіанта k: пробна зміна (для всіх кадрів напису з тим
        самим стилем у розмітці), доки не «Записати в розмітку»."""
        if self._loading or not self.cur:
            return
        vs = variants(self.marks, self.cur)
        if k >= len(vs):
            return
        old, new = vs[k][2]['стиль'], self.style_rows[k][1].get()
        m = dict(self.restyle.get(self.cur) or {})
        if new == old or new not in self.styles:
            m.pop(old, None)
        else:
            m[old] = new
        if m:
            self.restyle[self.cur] = m
        else:
            self.restyle.pop(self.cur, None)
        self._paint_refresh()
        self._look_status()
        self._style_rows_show()
        self._schedule(0)

    def _edit_style(self, k, edit):
        vs = variants(self.marks, self.cur) if self.cur else []
        if k >= len(vs):
            return
        self.style_row = k                       # з якого варіанта відкрито редактор
        StyleEditor(self, self.style_rows[k][1].get(), edit).show()

    def style_sample(self):
        """Для редактора стилю: (атлас, кадр, spec, [англ., переклад]) варіанта, з якого відкрито."""
        vs = variants(self.marks, self.cur) if self.cur else []
        k = self.style_row
        if k >= len(vs):
            return None
        src, i, spec = vs[k]
        # без пробного шрифту й «справжніх літер» — інакше не видно самого стилю
        spec = tuned(restyled(spec, self.restyle.get(self.cur)), None, False)
        img, boxes = self.atlases.get(src, self.marks[src])
        r = next(x for x in self.rows if x['id'] == self.cur)
        texts = [r['src'], self.entry.text().strip() or None]
        if self.game == 'nep':
            from neptunia import atlas as natl
            texts = [t and natl.text_for(spec, t) for t in texts]
        return img, tuple(atl.box_of(i, spec, boxes)), spec, texts

    def styles_changed(self, chosen=None):
        """Свій стиль збережено чи видалено (редактор стилю): перечитати стилі; `chosen` —
        пробно для варіанта, з якого відкривали редактор."""
        self.styles = atl.load_styles()
        for key, m in list(self.restyle.items()):         # видалений стиль — прибрати й звідси
            m = {a: b for a, b in m.items() if b in self.styles}
            if m:
                self.restyle[key] = m
            else:
                self.restyle.pop(key)
        self._pickers_refresh()
        if chosen and self.cur:
            self.style_rows[self.style_row][1].set(chosen)      # -> _restyle
        self._paint_refresh()
        self._look_status()
        self._style_rows_show()
        self._schedule(0)

    def _marked_real(self, key):
        return any(s.get('літери з оригіналу') for _src, _i, s in variants(self.marks, key))

    def _look_status(self):
        key = self.cur
        changed = key in self.suggest or key in self.restyle or self.real.isChecked() != self._marked_real(key)
        self.b_apply.setEnabled(bool(changed))
        if key in self.suggest or key in self.restyle:
            e, parts = self.suggest.get(key) or {}, []
            parts += [f'стиль {a} → {b}' for a, b in (self.restyle.get(key) or {}).items()]
            if 'шрифт' in e:
                var = e.get('варіація') or {}
                parts.append(f'шрифт {e["шрифт"]}' +
                             (f', товщина {var["wght"]}' if 'wght' in var else '') +
                             f', нахил {e["нахил"]}, розтяг {e["розтяг"]}')
            if 'заливка' in e:
                parts.append(f'літери {e["заливка"]}')
            if 'обведення' in e:
                o = e['обведення']
                parts.append(f'обведення {o.get("колір", "#000000")} {o.get("товщина", 0):g} px'
                             if o.get('товщина') else 'без обведення')
            if e.get('квадратність'):
                parts.append(f'квадратність {e["квадратність"]:.2f}')
            self.look.setText('Пробне: ' + '; '.join(parts) + ' — ще не записано.' +
                              (' Зі «Справжніми літерами» колір і квадратність не діють.'
                               if self.real.isChecked() and set(e) & set(LOOK) else ''))
        elif changed:
            self.look.setText('«Справжні літери» змінено лише для прев\'ю — ще не записано.')
        else:
            self.look.setText('')

    def _real_toggled(self):
        if self._loading:
            return
        self._look_status()
        self._schedule(0)

    def _gallery(self):
        if self.cur and variants(self.marks, self.cur):
            FontGallery(self, self.cur).show()

    def _chosen(self, key, edit):
        """Шрифт, вибраний у галереї: лише для прев'ю, поки не записано.
        Пробні колір і квадратність лишаються."""
        new = self.look_of(key)
        new.update(edit or {})
        if new:
            self.suggest[key] = new
        else:
            self.suggest.pop(key, None)
        if key == self.cur:
            self._look_status()
            self._schedule(0)

    @staticmethod
    def _restored(spec):
        """Кадр з правками стилю, що були до запису шрифту (None — шрифт не записували)."""
        if 'правки до шрифту' not in spec:
            return None
        spec = dict(spec)
        old = spec.pop('правки до шрифту')
        if old:
            spec['правки'] = old
        else:
            spec.pop('правки', None)
        return spec

    def _standard(self):
        """Прибрати пробний шрифт, а якщо шрифт уже записано в розмітку —
        повернути там правки стилю, що були до нього."""
        key = self.cur
        self.suggest.pop(key, None)
        self.restyle.pop(key, None)
        mod, marks = self._markup()
        n = 0
        for src, mark in marks.items():
            if src.startswith('_'):
                continue
            for i, spec in mark['кадри'].items():
                new = self._restored(spec) if atl.key_of(spec) == key else None
                if new is not None:
                    mark['кадри'][i] = new
                    n += 1
        if n:
            mod.save_marks(marks)
            self.marks = {k: v for k, v in marks.items() if not k.startswith('_')}
        self._look_status()
        self.look.setText(f'Повернуто стандартний вигляд у розмітці: кадрів {n}.' if n else
                          'Стандартний вигляд (у розмітці інший і не записували).')
        self._paint_refresh()
        self._schedule(0)

    def _apply_all(self, edit):
        """Один шрифт для ВСІХ написів гри (edit — лише шрифт і варіація; нахил і
        ширина кожного стилю лишаються свої). edit None — усім стандартний."""
        mod, marks = self._markup()
        n = 0
        for src, mark in marks.items():
            if src.startswith('_'):
                continue
            for i, spec in mark['кадри'].items():
                if edit is None:
                    new = self._restored(spec)
                    if new is None:
                        continue
                else:
                    if 'правки до шрифту' not in spec:
                        spec = dict(spec, **{'правки до шрифту': spec.get('правки')})
                    new = tuned(spec, edit)
                mark['кадри'][i] = new
                n += 1
        mod.save_marks(marks)
        if edit:
            from maryskelter import fontlib
            fontlib.adopt(edit['шрифт'])
        self.suggest.clear()
        self.marks = {k: v for k, v in marks.items() if not k.startswith('_')}
        self._look_status()
        self.look.setText((f'Шрифт {edit["шрифт"]} записано для всіх написів' if edit else
                           'Повернуто стандартні шрифти всім написам') + f': кадрів {n}.')
        self._schedule(0)

    def _markup(self):
        """(куди писати, уся розмітка): основна + перекладача; пишемо лише в «мої»
        (атлас/написи.мої.json чи нептун.мої.json) — основну веде власник програми."""
        name = MARKS[self.game]

        class Saver:
            @staticmethod
            def save_marks(marks):
                atl.save_user_marks(name, marks)
        return Saver, atl.load_marks(name)

    def _apply_look(self):
        """Записати вибраний шрифт і «справжні літери» в розмітку для всіх
        кадрів цього напису (атлас/написи.json чи атлас/нептун.json). Попередні
        правки стилю зберігаються в "правки до шрифту" — для «Повернути стандартний»."""
        key = self.cur
        mod, marks = self._markup()
        edit, real, n = self.suggest.get(key), self.real.isChecked(), 0
        restyle = self.restyle.get(key)
        for src, mark in marks.items():
            if src.startswith('_'):
                continue
            for i, spec in mark['кадри'].items():
                if atl.key_of(spec) != key:
                    continue
                if edit and 'правки до шрифту' not in spec:
                    spec = dict(spec, **{'правки до шрифту': spec.get('правки')})
                mark['кадри'][i] = tuned(restyled(spec, restyle), edit, real)
                n += 1
        mod.save_marks(marks)
        if edit and 'шрифт' in edit:
            from maryskelter import fontlib
            fontlib.adopt(edit['шрифт'])
        self.suggest.pop(key, None)
        self.restyle.pop(key, None)
        self.marks = {k: v for k, v in marks.items() if not k.startswith('_')}
        self._look_status()
        self.look.setText(f'Записано в розмітку: кадрів {n}.')
        self._paint_refresh()
        self._schedule(0)


Editor = AtlasEditor                              # назва як у Tk-модулі


# ============================================================ галерея шрифтів
class FontGallery(QDialog):
    """«Глянути різні шрифти»: той самий напис кожним шрифтом бібліотеки —
    стандартний першим. Товщину, нахил і ширину можна крутити для всіх разом;
    клік — вибрати, «Взяти вибраний» (або подвійний клік) — у прев'ю редактора."""

    COLS = 2
    CARD_W = 420                            # ширина картинки в картці, пікселі екрана

    def __init__(self, editor, key):
        super().__init__(editor)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        from maryskelter import fontlib
        self.ed, self.key = editor, key
        self.src, self.i, self.spec = variants(editor.marks, key)[0]
        self.spec = restyled(self.spec, editor.restyle.get(key))     # пробний стиль — теж
        st = dict(editor.styles[self.spec['стиль']])
        st.update(self.spec.get('правки', {}))
        st.update(editor.suggest.get(key) or {})
        var = st.get('варіація')
        self.fonts = [(None, None)] + fontlib.font_files()     # None — стандартний
        self.cards, self.sel = [], 0
        self.gen = 0
        self.alive = True
        self.bridge = Bridge()
        self.bridge.got.connect(self._got)
        self.t_start = QTimer(self, singleShot=True, timeout=self._start)

        self.setWindowTitle(f'Різні шрифти — {key}')
        self.resize(1000, 780)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        top = QGridLayout()
        img, boxes = editor.atlases.get(self.src, editor.marks[self.src])
        orig = img.crop(atl.box_of(self.i, self.spec, boxes))
        # дрібні написи збільшуємо (до 2×), великі — зменшуємо до ширини картки
        self.zoom = min(2.0, self.CARD_W / max(1, orig.width))
        top.addWidget(QLabel('Оригінал:'), 0, 0, Qt.AlignTop)
        o = QLabel()
        o.setPixmap(self._pixmap(orig))
        top.addWidget(o, 0, 1, 1, 6, Qt.AlignLeft)
        top.addWidget(QLabel('Текст:'), 1, 0)
        self.text = QLineEdit(editor.entry.text().strip() or self.spec['текст'])
        self.text.setFixedWidth(220)
        self.text.textChanged.connect(lambda _t: self._changed())
        top.addWidget(self.text, 1, 1)
        self.weight = float_slider(100, 900, var.get('wght', 700) if isinstance(var, dict) else 700)
        self.slant = float_slider(-0.1, 0.4, st.get('нахил', 0))
        self.stretch = float_slider(0.7, 1.4, st.get('розтяг', 1.0))
        self.labels = {}
        for col, (name, s) in enumerate((('Товщина', self.weight), ('Нахил', self.slant),
                                         ('Ширина', self.stretch)), start=2):
            box = QVBoxLayout()
            self.labels[name] = QLabel('')
            box.addWidget(self.labels[name])
            s.setFixedWidth(150)
            s.valueChanged.connect(lambda _v: self._changed())
            box.addWidget(s)
            top.addLayout(box, 1, col)
        hint = QLabel('Повзунки діють на всі шрифти, крім «Стандартного». Товщина — лише '
                      'для шрифтів з кількома товщинами; у …-Bold, …-Black вона зашита у файлі.')
        hint.setObjectName('Hint')
        top.addWidget(hint, 2, 0, 1, 7)
        root.addLayout(top)

        sa = QScrollArea()
        sa.setWidgetResizable(True)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        sa.setWidget(inner)
        root.addWidget(sa, 1)
        for k, (_d, fn) in enumerate(self.fonts):
            card = Card(k, 'Стандартний (як зараз)' if fn is None else fn.rsplit('.', 1)[0],
                        bold=fn is None, pic_bg='transparent')
            card.clicked.connect(self._select)
            card.double.connect(lambda n: (self._select(n), self._take()))
            grid.addWidget(card, k // self.COLS, k % self.COLS)
            self.cards.append(card)

        # свої шрифти (атлас/шрифти/мої, fontlib): з файлу чи з інтернету
        lib = QHBoxLayout()
        lib.addWidget(QLabel('Свої шрифти:'))
        b = QPushButton('Додати з файлу…')
        b.clicked.connect(self._add_files)
        lib.addWidget(b)
        b = QPushButton('Завантажити з інтернету…')
        b.clicked.connect(self._download)
        lib.addWidget(b)
        self.b_remove = QPushButton('Прибрати вибраний')
        self.b_remove.clicked.connect(self._remove)
        lib.addWidget(self.b_remove)
        h = QLabel('.ttf / .otf / .zip; посилання на файл, сторінка fonts.google.com або назва шрифту')
        h.setObjectName('Hint')
        lib.addWidget(h, 1)
        root.addLayout(lib)
        bot = QHBoxLayout()
        self.status = QLabel('')
        self.status.setObjectName('Hint')
        bot.addWidget(self.status, 1)
        b = QPushButton('Для всіх написів…')
        b.clicked.connect(self._take_all)
        bot.addWidget(b)
        b = QPushButton('Взяти вибраний')
        b.setObjectName('Accent')
        b.clicked.connect(self._take)
        bot.addWidget(b)
        b = QPushButton('Закрити')
        b.clicked.connect(self.close)
        bot.addWidget(b)
        root.addLayout(bot)
        self._select(0)
        self._changed(0)

    # ------------------------------------------------------------------
    def _select(self, n):
        self.cards[self.sel].mark(False)
        self.sel = n
        self.cards[n].mark(True, self.ed.t.get('accent', '#1f4f82'))
        d, fn = self.fonts[n]
        self.status.setText('Вибрано: ' + ('стандартний шрифт' if fn is None else fn) +
                            '. «Взяти вибраний» (або подвійний клік) — показати в редакторі.')
        if hasattr(self, 'b_remove'):
            from maryskelter import fontlib
            self.b_remove.setEnabled(d == fontlib.MY_DIR)

    def _edit(self, d, fn):
        """Правки стилю для шрифту з бібліотеки при поточних повзунках."""
        from maryskelter import fontlib
        if fn is None:
            return None
        fontlib.register(d, fn)
        return {'шрифт': fn, 'варіація': fontlib.variation(os.path.join(d, fn), self.weight.fget()),
                'нахил': round(self.slant.fget(), 3), 'розтяг': round(self.stretch.fget(), 3)}

    def _changed(self, delay=350):
        self.labels['Товщина'].setText(f'Товщина {self.weight.fget():.0f}')
        self.labels['Нахил'].setText(f'Нахил {self.slant.fget():.2f}')
        self.labels['Ширина'].setText(f'Ширина {self.stretch.fget():.2f}')
        self.t_start.start(delay)

    def _start(self):
        """Перемалювати всі картки у фоні (попередній прохід зупиняється сам)."""
        self.gen += 1
        gen, ed = self.gen, self.ed
        text = self.text.text().strip() or self.spec['текст']
        look = ed.look_of(self.key)                # пробні колір і квадратність — на всіх картках
        edits = [dict(look, **e) if e else (look or None)
                 for e in (self._edit(d, fn) for d, fn in self.fonts)]
        spec0, real, game = self.spec, ed.real.isChecked(), ed.game
        styles, atlases, src, mark, i, br = ed.styles, ed.atlases, self.src, ed.marks[self.src], self.i, self.bridge

        def work():
            img, boxes = atlases.get(src, mark)
            box = atl.box_of(i, spec0, boxes)
            canvas = img.copy()                 # "шаблон" бере тло з іншого місця атласу
            clean = img.crop(box)
            t = text
            if game == 'nep':
                from neptunia import atlas as natl
                t = natl.text_for(spec0, text)
            for k, edit in enumerate(edits):
                if gen != self.gen or not self.alive:
                    return
                canvas.paste(clean, box[:2])
                try:
                    atl.draw(canvas, box, tuned(spec0, edit, real), styles, t)
                    br.got.emit('card', (gen, k, canvas.crop(box), None))
                except Exception as ex:                               # noqa: BLE001
                    br.got.emit('card', (gen, k, None, str(ex)))
            br.got.emit('card', (gen, None, None, None))

        self.status.setText('Малюю…')
        threading.Thread(target=work, daemon=True).start()

    def _got(self, kind, val):
        if not self.alive:
            return
        if kind == 'library':
            self._library_done(val)
            return
        gen, k, im, err = val
        if gen != self.gen:
            return
        if k is None:
            self._select(self.sel)
            return
        pic = self.cards[k].pic
        if im is None:
            pic.setText(f'не вдалося: {err}')
            pic.setStyleSheet(f'color: {self.ed.t.get("warn", "#8a6100")};')
            return
        pic.setPixmap(self._pixmap(im))

    def _pixmap(self, im):
        return pil_pixmap(im, self.zoom, BG)

    def _take_all(self):
        """Вибраний шрифт — одразу в розмітку всіх написів гри."""
        d, fn = self.fonts[self.sel]
        edit = self._edit(d, fn)
        if edit is None:
            q = ('Повернути стандартні шрифти всім написам, яким шрифт записували?')
        else:
            var = edit.get('варіація') or {}
            q = (f'Записати шрифт {fn}' + (f' (товщина {var["wght"]})' if 'wght' in var else '') +
                 ' для ВСІХ написів цієї гри?\n\nНахил і ширина кожного стилю лишаться свої. '
                 'Скасувати можна тут же: картка «Стандартний» → «Для всіх написів…».')
            edit = {k: edit[k] for k in ('шрифт', 'варіація')}
        if QMessageBox.question(self, 'Для всіх написів', q) != QMessageBox.Yes:
            return
        self.ed._apply_all(edit)
        self.close()

    def _take(self):
        d, fn = self.fonts[self.sel]
        self.ed._chosen(self.key, self._edit(d, fn))
        self.close()

    def closeEvent(self, ev):
        self.alive = False
        self.gen += 1                           # зупинити фоновий прохід
        super().closeEvent(ev)

    def done(self, r):                          # Esc — теж зупинити фоновий прохід
        self.alive = False
        self.gen += 1
        super().done(r)

    # ------------------------------------------------------ свої шрифти
    def _add_files(self):
        paths, _f = QFileDialog.getOpenFileNames(self, 'Шрифти для написів', '',
                                                 'Шрифти й архіви (*.ttf *.otf *.zip);;Усі файли (*.*)')
        if paths:
            from maryskelter import fontlib
            self._library(lambda: fontlib.add_files(paths))

    def _download(self):
        text, ok = QInputDialog.getText(
            self, 'Завантажити шрифт',
            'Посилання на .ttf / .otf / .zip, сторінка fonts.google.com/specimen/…\n'
            'або просто назва шрифту з Google Fonts (напр. Rubik Mono One):')
        if ok and text and text.strip():
            from maryskelter import fontlib
            self._library(lambda: fontlib.download(text))

    def _remove(self):
        from maryskelter import fontlib
        d, fn = self.fonts[self.sel]
        if d != fontlib.MY_DIR or QMessageBox.question(
                self, 'Прибрати шрифт', f'Прибрати {fn} зі своїх шрифтів?\n(Написи, яким його вже '
                'записано, його збережуть — він скопійований у атлас/шрифти/.)') != QMessageBox.Yes:
            return
        fontlib.remove(fn)
        self._reopen()

    def _library(self, job):
        """Додати шрифти у фоні (завантаження може тривати), потім показати звіт."""
        self.status.setText('Додаю шрифти…')
        br = self.bridge

        def work():
            try:
                report = job()
            except Exception as ex:                                   # noqa: BLE001
                report = [f'✗ не вдалося: {ex}']
            br.got.emit('library', report)
        threading.Thread(target=work, daemon=True).start()

    def _library_done(self, report):
        added = any(r.startswith('✓') for r in report)
        QMessageBox.information(self, 'Свої шрифти', '\n'.join(report[:30]) or 'Нічого не додано.')
        if added:
            self._reopen()
        else:
            self.status.setText('Нічого не додано.')

    def _reopen(self):
        """Перебудувати вікно з новою бібліотекою (картки створюються раз)."""
        ed, key = self.ed, self.key
        self.close()
        FontGallery(ed, key).show()
