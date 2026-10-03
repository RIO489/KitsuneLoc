# -*- coding: utf-8 -*-
"""Вікно «Гра на екрані» на Qt — те саме, що screenwatch_window.py (Tk), інший вигляд.

Стежить за вікном гри, розпізнає текст (screenwatch.py) і показує, які рядки перекладу
зараз на екрані та в якому вони стані:

  ✓ переклад у грі            — на екрані український текст;
  ⚠ у грі ще англійською      — переклад є, але гра показує оригінал (не залито «2»);
  ✗ не перекладено            — на екрані оригінал, перекладу немає.

Подвійний клік — рядок у редакторі. «Редактор іде за грою» — редактор сам переходить до
знайденої репліки (без фокусу: гра лишається попереду; поки ти пишеш у редакторі — не
заважає). «Поверх усіх вікон» — маленьке вікно над грою.

Журнал зі знімками: до кожного рядка журналу — знімок кадру, на якому його побачено
(тимчасова тека `кеш\\знімки журналу\\<гра>\\<сеанс>`; знімок рядка, що випав із журналу,
видаляється одразу, решта — з «Очистити» чи закриттям вікна). Клік по рядку — знімок із
рамкою навколо цього тексту замість живого кадру («Наживо» — назад). Права кнопка —
«Зберегти в нагадування» (reminders.py: знімок і рядок лишаються, доки їх не видалять).

Потоки: пошук будується у фоні, Watcher — свій потік; у вікно все приходить сигналом Qt
(Bridge), віджетів з потоків не чіпаємо.
"""
import os, shutil, threading, time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QMainWindow, QMenu, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)

from PIL import Image, ImageDraw

import screenwatch
from qtui import theme as qtheme

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# куди писати (тести підміняють ці дві змінні модуля)
SHOTS_ROOT = os.path.join(HERE, 'кеш', 'знімки журналу')
FRAMES_ROOT = os.path.join(HERE, 'кеш', 'кадри гри')

STATES = {'ok': ('✓ переклад у грі', '#2e9e57'),
          'warn': ('⚠ у грі ще англійською', '#d08a00'),
          # у файлах гри — інший (старий) переклад цього рядка: після змін не натиснуто «2»
          'stale': ('⚠ у грі старий переклад', '#d08a00'),
          'miss': ('✗ не перекладено', '#d0453b'),
          # текст, якого немає ні в перекладі, ні в оригіналі, ні у файлах гри
          # (або розпізнано надто погано: похилий шрифт історії діалогів)
          'old': ('? невідомий текст', '#8f6fd6')}
THUMB_W = 560
HISTORY = 200


class Bridge(QObject):
    """Фоновий потік -> вікно (сигнали Qt потокобезпечні)."""
    got = Signal(str, object)


class Thumb(QLabel):
    """Кадр гри (чи знімок з журналу); подвійний клік — перегляд з наближенням."""

    def __init__(self, on_double):
        super().__init__()
        self.on_double = on_double

    def mouseDoubleClickEvent(self, ev):
        self.on_double()


class WatchWindow(QMainWindow):
    def __init__(self, app, editor):
        super().__init__()
        self.app, self.ed, self.pr = app, editor, editor.pr
        self.theme_name = getattr(app, 'theme', None) or getattr(editor, 'theme_name', 'stars')
        self.setWindowTitle('Гра на екрані')
        self.resize(620, 720)
        self.setMinimumSize(460, 480)
        self.bridge = Bridge()
        self.bridge.got.connect(self._got)
        self.watcher = None
        self.index = None
        self.last = None
        self.rows = {}                  # ключ -> QTreeWidgetItem у журналі
        self.frames = 0
        self.shots = {}                 # ключ рядка журналу -> {файл, рамка, стан, текст, хто, ключ, час}
        self.shot_dir = None            # тимчасова тека знімків цього вікна (створюється з першим)
        self.pinned = None              # ключ, чий знімок зараз показано замість живого кадру
        self._closed = False
        self._build()
        self.game_text = {}             # source -> {id: текст}, що зараз у файлах гри
        self.font_ocr = None            # screenwatch.GameFontOcr (Neptunia) | False — Windows OCR
        self.status.setText('Готую пошук по рядках…')
        self._rebuild(start=False)
        self.show()

    # ------------------------------------------------------------ вигляд
    def _build(self):
        self.back = qtheme.Backdrop(self.theme_name)
        self.setCentralWidget(self.back)
        root = QVBoxLayout(self.back)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)

        top = QHBoxLayout()
        self.b_run = QPushButton('Почати')
        self.b_run.setObjectName('Accent')
        self.b_run.clicked.connect(self._toggle)
        self.b_run.setEnabled(False)
        top.addWidget(self.b_run)
        self.follow = QCheckBox('Редактор іде за грою')
        self.follow.setChecked(True)
        top.addWidget(self.follow)
        self.ontop = QCheckBox('Поверх усіх вікон')
        self.ontop.toggled.connect(self._set_ontop)
        top.addWidget(self.ontop)
        top.addStretch(1)
        self.keep = QCheckBox('Зберігати кадри')
        self.keep.toggled.connect(lambda _on: self._keep_frames())
        top.addWidget(self.keep)
        b = QPushButton('Очистити')
        b.clicked.connect(self._clear)
        top.addWidget(b)
        root.addLayout(top)

        sf = QHBoxLayout()
        self.status = QLabel('')
        self.status.setObjectName('Hint')
        self.status.setWordWrap(True)
        sf.addWidget(self.status, 1)
        # «Наживо» — видно лише, поки показано знімок з журналу
        self.b_live = QPushButton('Наживо')
        self.b_live.clicked.connect(self._unpin)
        self.b_live.hide()
        sf.addWidget(self.b_live, 0, Qt.AlignTop)
        root.addLayout(sf)

        pf = QFrame()
        pf.setObjectName('Panel')
        pl = QVBoxLayout(pf)
        pl.setContentsMargins(6, 6, 6, 6)
        self.back.glow(pf)
        self.canvas = Thumb(self._zoom)
        self.canvas.setFixedSize(THUMB_W, THUMB_W * 9 // 16)
        self.canvas.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.canvas.setCursor(Qt.PointingHandCursor)
        self.canvas.setToolTip('Подвійний клік — кадр на весь екран з наближенням')
        pl.addWidget(self.canvas, 0, Qt.AlignHCenter)
        root.addWidget(pf)

        lf = QFrame()
        lf.setObjectName('Panel')
        ll = QVBoxLayout(lf)
        ll.setContentsMargins(6, 6, 6, 6)
        self.back.glow(lf)
        self.list = QTreeWidget()
        self.list.setColumnCount(3)
        self.list.setHeaderLabels(['Стан', 'Хто', 'Текст'])
        self.list.setRootIsDecorated(False)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        h = self.list.header()
        h.setStretchLastSection(True)
        h.setSectionResizeMode(0, QHeaderView.Interactive)
        h.resizeSection(0, 170)
        h.resizeSection(1, 110)
        self.list.itemDoubleClicked.connect(lambda *_a: self._open())
        self.list.itemSelectionChanged.connect(self._show_shot)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        for k in ('Return', 'Enter'):
            QShortcut(QKeySequence(k), self.list, activated=self._open, context=Qt.WidgetShortcut)
        ll.addWidget(self.list)
        root.addWidget(lf, 1)

    def _set_ontop(self, on):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        self.show()                     # зміна прапорця ховає вікно

    def _colors(self):
        c = getattr(self.app, 'colors', None)
        return c() if c else qtheme.palette(self.theme_name)

    def _exe(self):
        games = getattr(self.app, 'GAMES', None)
        if games is None:
            from qtui import main
            games = main.legacy()['GAMES']
        return games[self.pr.game]['exe']

    # ------------------------------------------------------------ пошук
    def _rebuild(self, start):
        """Пошук — заново (у фоні): переклад міг змінитись, а після «2» — і файли гри."""
        try:
            game_dir = self.app.root_dir()
        except Exception:                                   # noqa: BLE001 (RuntimeError, немає app)
            game_dir = None
        self.b_run.setEnabled(False)
        threading.Thread(target=self._build_index, args=(game_dir, start), daemon=True).start()

    def _build_index(self, game_dir, start):
        items = []
        for r in self.pr.rows:
            if r['kind'] == 'key':
                continue
            items.append((r['k'], 'src', r['e']['src']))
            if r['e'].get('tr'):
                items.append((r['k'], 'tr', r['e']['tr']))
        gt = {}
        if game_dir and self.pr.game == 'nep':
            # текст, що зараз у файлах гри, — щоб упізнати старий, ще не залитий переклад
            try:
                import translate_nep
                gt = translate_nep.game_texts(game_dir, self.pr.work)
            except Exception:                               # noqa: BLE001
                gt = {}
            sk = screenwatch.skeleton
            for src, d in gt.items():
                for i, t in d.items():
                    r = self.pr.by_key.get(f'{src}\t{i}')
                    if r and r['kind'] != 'key' and t.strip() and \
                            sk(t) not in (sk(r['e']['src']), sk(r['e'].get('tr', ''))):
                        items.append((r['k'], 'game', t))
        if self.font_ocr is None:
            # свій розпізнавач шрифтами гри (будь-яка гра з профілем рушія; без профілю —
            # виняток і Windows OCR); один на вікно — знайдені масштаби не губляться
            try:
                self.font_ocr = screenwatch.GameFontOcr(self.pr.game, self.ed.bk)
            except Exception:                               # noqa: BLE001
                self.font_ocr = False               # немає шрифтів — Windows OCR
        self.bridge.got.emit('index', (screenwatch.Index(items), gt, start))

    def _toggle(self):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
            self.b_run.setText('Почати')
            self.status.setText('Зупинено.')
            return
        self.status.setText('Читаю переклад і те, що зараз у файлах гри…')
        self._rebuild(start=True)

    def _start(self):
        exe = self._exe()
        self.watcher = screenwatch.Watcher(exe, self.index, lambda *a: self.bridge.got.emit('frame', a),
                                           ocr=self.font_ocr or None)
        self._keep_frames()
        self.watcher.start()
        self.b_run.setText('Зупинити')
        self.status.setText(f'Шукаю вікно гри ({exe})…')

    # ------------------------------------------------------------ кадри
    def _got(self, what, data):
        if self._closed:
            return
        if what == 'index':
            self.index, self.game_text, start = data
            self.b_run.setEnabled(True)
            if start:
                self._start()
            else:
                self.status.setText('Запусти гру й натисни «Почати». Програма раз на секунду дивиться '
                                    'у вікно гри й шукає на екрані рядки перекладу.')
        elif what == 'frame':
            self._frame(*data)

    def _frame(self, state, img, hits):
        if not self.watcher:
            return
        if state == 'nogame':
            self.status.setText(f'Не бачу вікна гри ({self._exe()}). Запусти гру; згорнуте вікно не видно.')
            return
        if state == 'same':
            return
        if state.startswith('err:'):
            self.status.setText('Помилка: ' + state[4:])
            return
        self.frames += 1
        shown = []
        for ratio, keys, box, text in hits:
            if not keys:
                shown.append((ratio, None, 'old', box, text))
                continue
            key, kind = self._pick(keys)
            if key is None:
                continue
            shown.append((ratio, key, self._state(key, kind), box, text))
        self.last = (img, shown)
        if not self.pinned:
            self._draw(img, shown)
        shot = self._save_shot(img) if shown else None
        for ratio, key, st, box, text in shown:
            rk = self._remember(key, st, text)
            if shot and rk in self.rows:
                it = self.rows[rk]
                self.shots[rk] = {'файл': shot, 'рамка': [int(v) for v in box], 'стан': st,
                                  'текст': it.text(2), 'хто': it.text(1), 'ключ': key,
                                  'час': time.strftime('%H:%M:%S')}
        self._prune_shots()
        known = [s for s in shown if s[1]]
        n_old = len(shown) - len(known)
        n_stale = sum(1 for s in known if s[2] in ('stale', 'warn'))
        msg = f'Кадр {self.frames} ({time.strftime("%H:%M:%S")}): '
        msg += f'знайдено рядків {len(known)}' if known else 'знайомого тексту не видно'
        if n_stale:
            msg += f'; {n_stale} — у грі ще не твій переклад (натисни «2»)'
        if n_old:
            msg += f'; ще {n_old} — текст, якого програма не знає'
        if not self.pinned:                       # поки видно знімок — підпис знімка не перетираємо
            self.status.setText(msg + '.')
        if self.follow.isChecked() and known:
            self._follow(known)

    def _pick(self, keys):
        """З кількох рядків з тим самим текстом: перекладений ('tr'), далі старий
        переклад з файлів гри ('game'), далі оригінал."""
        keys = [(k, kind) for k, kind in keys if k in self.pr.by_key]
        if not keys:
            return None, None
        for want in ('tr', 'game'):
            for k, kind in keys:
                if kind == want:
                    return k, kind
        return keys[0]

    def _state(self, key, kind):
        e = self.pr.by_key[key]['e']
        if kind == 'tr' or self.pr.by_key[key].get('лишити'):
            return 'ok'                              # «не перекладати» — оригінал у грі і є правильний
        if kind == 'game':
            return 'stale'
        tr = e.get('tr', '')
        if tr and screenwatch.skeleton(tr) != screenwatch.skeleton(e['src']):
            return 'warn'
        return 'ok' if tr else 'miss'

    def _draw(self, img, shown, width=2):
        k = THUMB_W / img.width
        im = img.convert('RGB').resize((THUMB_W, max(1, round(img.height * k))), Image.BILINEAR)
        d = ImageDraw.Draw(im)
        for _r, _key, st, box, _t in shown:
            d.rectangle([box[0] * k - 2, box[1] * k - 2, box[2] * k + 2, box[3] * k + 2],
                        outline=STATES[st][1], width=width)
        self.canvas.setFixedSize(im.width, im.height)
        self.canvas.setPixmap(qtheme.pixmap(im))

    def _remember(self, key, st, ocr_text=''):
        if key is None:
            # невідомий текст: показуємо розпізнане (українське — латиницею OCR), ключ — скелет
            key = 'ocr:' + screenwatch.skeleton(ocr_text)
            vals = (STATES[st][0], '', 'розпізнано: ' + ' '.join(ocr_text.split()))
        else:
            r = self.pr.by_key[key]
            if st == 'stale':
                src, i = key.split('\t')
                text = 'у грі: «' + ' '.join(self.game_text.get(src, {}).get(i, '').split()) + \
                       '» → тепер: «' + ' '.join(r['e'].get('tr', '').split()) + '»'
            elif st == 'ok' and r['e'].get('tr'):
                text = ' '.join(r['e']['tr'].split())
            else:
                text = ' '.join(r['e']['src'].split())
            vals = (STATES[st][0], r.get('who') or '', text)
        # перестановка рядка нагору не має «вибирати» інші рядки (і показувати їхні знімки)
        lst = self.list
        sel = lst.selectedItems()
        lst.blockSignals(True)
        try:
            it = self.rows.get(key)
            if it is not None and lst.indexOfTopLevelItem(it) >= 0:
                lst.takeTopLevelItem(lst.indexOfTopLevelItem(it))
            else:
                it = QTreeWidgetItem()
                self.rows[key] = it
            for col, v in enumerate(vals):
                it.setText(col, v)
                it.setForeground(col, QBrush(QColor(STATES[st][1])))
            it.setData(0, Qt.UserRole, key)
            lst.insertTopLevelItem(0, it)
            while lst.topLevelItemCount() > HISTORY:
                lst.takeTopLevelItem(HISTORY)
            for s in sel:
                if lst.indexOfTopLevelItem(s) >= 0:
                    s.setSelected(True)
        finally:
            lst.blockSignals(False)
        self.rows = {k: i for k, i in self.rows.items() if lst.indexOfTopLevelItem(i) >= 0}
        return key

    def _follow(self, shown):
        """Редактор — до найпевнішої довгої репліки кадру (без фокусу). Поки фокус у
        редакторі (перекладач пише) — не чіпаємо."""
        f = QApplication.focusWidget()
        if f is not None and f.window() is self.ed:
            return
        best = max(shown, key=lambda s: (s[0] >= 0.75, len(s[4])))
        if best[0] < 0.75 or not self._ed_alive():
            return
        self._goto(best[1], quiet=True)

    def _ed_alive(self):
        try:
            return self.ed.isVisible()
        except RuntimeError:                                # вікно редактора вже знищено
            return False

    def _goto(self, key, quiet=False):
        """editor.goto(key, quiet=True) — якщо редактор уміє «тихо»; інакше звичайний goto
        (у Qt-редакторі вибір рядка фокус і так не забирає)."""
        try:
            self.ed.goto(key, quiet=quiet) if quiet else self.ed.goto(key)
        except TypeError:
            self.ed.goto(key)

    # ------------------------------------------------------------ інше
    def frames_dir(self):
        return os.path.join(FRAMES_ROOT, self.pr.game)

    def _keep_frames(self):
        """Повні кадри гри й розпізнане — на диск (щоб налаштувати розпізнавання)."""
        if self.watcher:
            self.watcher.save_dir = self.frames_dir() if self.keep.isChecked() else None
        if self.keep.isChecked():
            self.status.setText(f'Кадри зберігаються в {self.frames_dir()}')

    def _selected_key(self):
        sel = self.list.selectedItems()
        return sel[0].data(0, Qt.UserRole) if sel else None

    def _open(self):
        key = self._selected_key()
        if key and not key.startswith('ocr:') and self._ed_alive():
            self._goto(key)

    def _clear(self):
        self._unpin()
        self.list.clear()
        self.rows.clear()
        self.shots.clear()
        self._prune_shots()

    def closeEvent(self, ev):
        self._closed = True
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        if getattr(self.ed, 'watch_win', None) is self:
            self.ed.watch_win = None
        if self.shot_dir:
            shutil.rmtree(self.shot_dir, ignore_errors=True)
        super().closeEvent(ev)

    # для редактора (назви — як у Tk-вікна)
    def winfo_exists(self):
        return self.isVisible()

    def lift(self):
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ знімки журналу
    def shots_root(self):
        return os.path.join(SHOTS_ROOT, self.pr.game)

    def _save_shot(self, img):
        """Кадр — у тимчасову теку сеансу (JPEG). Повертає шлях або None."""
        try:
            if self.shot_dir is None:
                root = self.shots_root()
                # теки попередніх сеансів (вікно закрили аварійно) — прибрати
                if os.path.isdir(root):
                    for d in os.listdir(root):
                        shutil.rmtree(os.path.join(root, d), ignore_errors=True)
                self.shot_dir = os.path.join(root, time.strftime('%Y%m%d-%H%M%S'))
                os.makedirs(self.shot_dir, exist_ok=True)
            path = os.path.join(self.shot_dir, f'{self.frames:06d}.jpg')
            img.convert('RGB').save(path, quality=85)
            return path
        except OSError:
            return None

    def _prune_shots(self):
        """Знімки лише для рядків, що є в журналі; решту — з диска."""
        self.shots = {k: s for k, s in self.shots.items() if k in self.rows}
        if self.pinned and self.pinned not in self.shots:
            self._unpin()
        if not self.shot_dir or not os.path.isdir(self.shot_dir):
            return
        keep = {os.path.basename(s['файл']) for s in self.shots.values()}
        for f in os.listdir(self.shot_dir):
            if f not in keep:
                try:
                    os.remove(os.path.join(self.shot_dir, f))
                except OSError:
                    pass

    def _show_shot(self):
        """Клік по рядку журналу — його знімок з рамкою навколо тексту."""
        key = self._selected_key()
        if key is None:
            return
        shot = self.shots.get(key)
        if not shot or not os.path.exists(shot['файл']):
            self.status.setText('Для цього рядка знімка немає (побачено до того, як з\'явились знімки).')
            return
        try:
            img = Image.open(shot['файл'])
            img.load()
        except OSError:
            return
        self.pinned = key
        self._draw(img, [(1.0, key, shot['стан'], shot['рамка'], shot['текст'])], width=4)
        self.b_live.show()
        self.status.setText(f'Знімок з журналу ({shot["час"]}): «{shot["текст"][:80]}». '
                            'Права кнопка — зберегти в нагадування; «Наживо» — назад до гри.')

    def _zoom(self):
        from qtui import imageview
        if self.pinned and self.pinned in self.shots:
            sh = self.shots[self.pinned]
            if os.path.exists(sh['файл']):
                self.viewer = imageview.ImageView(self, sh['файл'], title=sh['текст'][:80],
                                                  boxes=[(*sh['рамка'], STATES[sh['стан']][1])],
                                                  colors=self._colors())
            return
        if self.last:
            img, shown = self.last
            self.viewer = imageview.ImageView(self, img.copy(), title='Кадр гри',
                                              boxes=[(*box, STATES[st][1]) for _r, _k, st, box, _t in shown],
                                              colors=self._colors())

    def _unpin(self):
        if not self.pinned:
            return
        self.pinned = None
        self.b_live.hide()
        self.list.blockSignals(True)
        self.list.clearSelection()
        self.list.blockSignals(False)
        if self.last:
            self._draw(*self.last)

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if it is None:
            return
        self.list.setCurrentItem(it)
        it.setSelected(True)
        key = self._selected_key()
        m = QMenu(self)
        m.addAction('Зберегти в нагадування', lambda: self._to_reminders(key))
        if key and not key.startswith('ocr:'):
            m.addAction('Відкрити рядок у редакторі', self._open)
        m.exec(self.list.viewport().mapToGlobal(pos))

    def _to_reminders(self, key):
        """Рядок журналу (зі знімком, якщо є) — у «Нагадування» → «Збережений журнал екрану гри»."""
        import reminders
        it = self.rows.get(key)
        if it is None or self.list.indexOfTopLevelItem(it) < 0:
            return
        st_label, who, text = it.text(0), it.text(1), it.text(2)
        shot = self.shots.get(key) or {}
        try:
            store = reminders.Store(self.pr.xl)
            store.add('екран', text, image=shot.get('файл'), стан=st_label, хто=who or None,
                      ключ=None if key.startswith('ocr:') else key, рамка=shot.get('рамка'))
        except OSError as ex:
            self.status.setText(f'Не вдалося зберегти нагадування: {ex}')
            return
        self.status.setText('Збережено в «Нагадування» (головне вікно програми → «Нагадування…»).')
        notify = getattr(self.app, 'reminders_changed', None)
        if notify:
            notify()
