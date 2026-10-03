# -*- coding: utf-8 -*-
"""Вікно «Гра на екрані»: стежить за вікном гри, розпізнає текст (screenwatch.py)
і показує, які рядки перекладу зараз на екрані та в якому вони стані:

  ✓ переклад у грі            — на екрані український текст;
  ⚠ у грі ще англійською      — переклад є, але гра показує оригінал (не залито «2»);
  ✗ не перекладено            — на екрані оригінал, перекладу немає.

Подвійний клік — рядок у редакторі. «Редактор іде за грою» — редактор сам
переходить до знайденої репліки (без фокусу: гра лишається попереду; поки ти
пишеш у редакторі — не заважає). «Поверх усіх вікон» — маленьке вікно над грою.

Журнал зі знімками: до кожного рядка журналу — знімок кадру, на якому його побачено
(тимчасова тека `кеш\\знімки журналу\\<гра>\\<сеанс>`; знімок рядка, що випав із журналу,
видаляється одразу, решта — з «Очистити» чи закриттям вікна). Клік по рядку — знімок
із рамкою навколо цього тексту замість живого кадру («Наживо» — назад). Права кнопка —
«Зберегти в нагадування» (reminders.py: знімок і рядок лишаються, доки їх не видалять).
"""
import os, queue, shutil, threading, time
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

import screenwatch

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


class WatchWindow(tk.Toplevel):
    def __init__(self, app, editor):
        super().__init__(editor)
        self.app, self.ed, self.pr = app, editor, editor.pr
        self.title('Гра на екрані')
        self.geometry('620x720')
        self.minsize(460, 480)
        self.q = queue.Queue()
        self.watcher = None
        self.index = None
        self.photo = None
        self.rows = {}                  # ключ -> iid у списку
        self.frames = 0
        self.shots = {}                 # ключ рядка журналу -> {файл, рамка, стан, текст, хто, час}
        self.shot_dir = None            # тимчасова тека знімків цього вікна (створюється з першим)
        self.pinned = None              # ключ, чий знімок зараз показано замість живого кадру
        self._build()
        self.protocol('WM_DELETE_WINDOW', self._close)
        try:
            self.app.dark_titlebar(self)
        except Exception:                                   # noqa: BLE001
            pass
        self.game_text = {}             # source -> {id: текст}, що зараз у файлах гри
        self.font_ocr = None            # screenwatch.GameFontOcr (Neptunia) | False — Windows OCR
        self.status.set('Готую пошук по рядках…')
        self._rebuild(start=False)
        self.after(100, self._poll)

    # ------------------------------------------------------------ вигляд
    def _build(self):
        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill='x')
        self.b_run = ttk.Button(top, text='Почати', command=self._toggle, style='Accent.TButton')
        self.b_run.pack(side='left')
        self.b_run.state(['disabled'])
        self.follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text='Редактор іде за грою', variable=self.follow).pack(side='left', padx=10)
        self.ontop = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text='Поверх усіх вікон', variable=self.ontop,
                        command=lambda: self.attributes('-topmost', self.ontop.get())).pack(side='left')
        ttk.Button(top, text='Очистити', command=self._clear).pack(side='right')
        self.keep = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text='Зберігати кадри', variable=self.keep,
                        command=self._keep_frames).pack(side='right', padx=8)

        self.status = tk.StringVar()
        sf = ttk.Frame(self, padding=(10, 0))
        sf.pack(fill='x')
        # «Наживо» — видно лише, поки показано знімок з журналу
        self.b_live = ttk.Button(sf, text='Наживо', command=self._unpin)
        ttk.Label(sf, textvariable=self.status, style='Hint.TLabel',
                  wraplength=500, justify='left').pack(side='left', fill='x', expand=True)

        c = self.app.colors()
        self.canvas = tk.Canvas(self, height=THUMB_W * 9 // 16, highlightthickness=0, bd=0,
                                bg=c.get('panel', '#202020'))
        self.canvas.pack(fill='x', padx=10, pady=6)
        # подвійний клік — кадр (чи знімок з журналу) на весь екран з наближенням
        self.canvas.bind('<Double-1>', lambda e: self._zoom())
        self.canvas.configure(cursor='hand2')

        lf = ttk.Frame(self)
        lf.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        self.list = ttk.Treeview(lf, columns=('state', 'who', 'text'), show='headings', selectmode='browse')
        for col, title, w, st in (('state', 'Стан', 170, False), ('who', 'Хто', 110, False),
                                  ('text', 'Текст', 300, True)):
            self.list.heading(col, text=title)
            self.list.column(col, width=w, stretch=st)
        for k, (_t, color) in STATES.items():
            self.list.tag_configure(k, foreground=color)
        sb = ttk.Scrollbar(lf, command=self.list.yview)
        self.list.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.list.pack(fill='both', expand=True)
        self.list.bind('<Double-1>', lambda e: self._open())
        self.list.bind('<Return>', lambda e: self._open())
        self.list.bind('<<TreeviewSelect>>', lambda e: self._show_shot())
        self.list.bind('<Button-3>', self._menu)

    # ------------------------------------------------------------ пошук
    def _rebuild(self, start):
        """Пошук — заново (у фоні): переклад міг змінитись, а після «2» — і файли гри."""
        try:
            game_dir = self.app.root_dir()
        except RuntimeError:
            game_dir = None
        self.b_run.state(['disabled'])
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
        self.q.put(('index', (screenwatch.Index(items), gt, start)))

    def _toggle(self):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
            self.b_run.configure(text='Почати')
            self.status.set('Зупинено.')
            return
        self.status.set('Читаю переклад і те, що зараз у файлах гри…')
        self._rebuild(start=True)

    def _start(self):
        exe = self.app.GAMES[self.pr.game]['exe']
        self.watcher = screenwatch.Watcher(exe, self.index, lambda *a: self.q.put(('frame', a)),
                                           ocr=self.font_ocr or None)
        self._keep_frames()
        self.watcher.start()
        self.b_run.configure(text='Зупинити')
        self.status.set(f'Шукаю вікно гри ({exe})…')

    # ------------------------------------------------------------ кадри
    def _poll(self):
        if not self.winfo_exists():
            return
        try:
            while True:
                what, data = self.q.get_nowait()
                if what == 'index':
                    self.index, self.game_text, start = data
                    self.b_run.state(['!disabled'])
                    if start:
                        self._start()
                    else:
                        self.status.set('Запусти гру й натисни «Почати». Програма раз на секунду дивиться '
                                        'у вікно гри й шукає на екрані рядки перекладу.')
                elif what == 'frame':
                    self._frame(*data)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _frame(self, state, img, hits):
        if not self.watcher:
            return
        exe = self.app.GAMES[self.pr.game]['exe']
        if state == 'nogame':
            self.status.set(f'Не бачу вікна гри ({exe}). Запусти гру; згорнуте вікно не видно.')
            return
        if state == 'same':
            return
        if state.startswith('err:'):
            self.status.set('Помилка: ' + state[4:])
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
                _st, who, shown_text = self.list.item(self.rows[rk], 'values')
                self.shots[rk] = {'файл': shot, 'рамка': [int(v) for v in box], 'стан': st,
                                  'текст': shown_text, 'хто': who, 'ключ': key,
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
            self.status.set(msg + '.')
        if self.follow.get() and known:
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
        im = img.resize((THUMB_W, max(1, round(img.height * k))), Image.BILINEAR)
        d = ImageDraw.Draw(im)
        for _r, _key, st, box, _t in shown:
            d.rectangle([box[0] * k - 2, box[1] * k - 2, box[2] * k + 2, box[3] * k + 2],
                        outline=STATES[st][1], width=width)
        self.photo = ImageTk.PhotoImage(im)
        self.canvas.configure(height=im.height)
        self.canvas.delete('all')
        self.canvas.create_image(0, 0, image=self.photo, anchor='nw')

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
        iid = self.rows.get(key)
        if iid and self.list.exists(iid):
            self.list.item(iid, values=vals, tags=(st,))
            self.list.move(iid, '', 0)
        else:
            self.rows[key] = self.list.insert('', 0, values=vals, tags=(st,))
        kids = self.list.get_children()
        for old in kids[HISTORY:]:
            self.list.delete(old)
        self.rows = {k: i for k, i in self.rows.items() if self.list.exists(i)}
        return key

    def _follow(self, shown):
        """Редактор — до найпевнішої довгої репліки кадру (без фокусу). Поки фокус у
        редакторі (перекладач пише) — не чіпаємо."""
        try:
            f = self.focus_get()
            if f is not None and f.winfo_toplevel() is self.ed:
                return
        except (KeyError, tk.TclError):
            pass
        best = max(shown, key=lambda s: (s[0] >= 0.75, len(s[4])))
        if best[0] < 0.75 or not self.ed.winfo_exists():
            return
        self.ed.goto(best[1], quiet=True)

    # ------------------------------------------------------------ інше
    def frames_dir(self):
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, 'кеш', 'кадри гри', self.pr.game)

    def _keep_frames(self):
        """Повні кадри гри й розпізнане — на диск (щоб налаштувати розпізнавання)."""
        if self.watcher:
            self.watcher.save_dir = self.frames_dir() if self.keep.get() else None
        if self.keep.get():
            self.status.set(f'Кадри зберігаються в {self.frames_dir()}')

    def _open(self):
        s = self.list.selection()
        if not s:
            return
        key = next((k for k, i in self.rows.items() if i == s[0]), None)
        if key and not key.startswith('ocr:') and self.ed.winfo_exists():
            self.ed.goto(key)

    def _clear(self):
        self._unpin()
        self.list.delete(*self.list.get_children())
        self.rows.clear()
        self.shots.clear()
        self._prune_shots()

    def _close(self):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        if getattr(self.ed, 'watch_win', None) is self:
            self.ed.watch_win = None
        if self.shot_dir:
            shutil.rmtree(self.shot_dir, ignore_errors=True)
        self.destroy()

    # ------------------------------------------------------------ знімки журналу
    def shots_root(self):
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(here, 'кеш', 'знімки журналу', self.pr.game)

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

    def _selected_key(self):
        s = self.list.selection()
        return next((k for k, i in self.rows.items() if s and i == s[0]), None)

    def _show_shot(self):
        """Клік по рядку журналу — його знімок з рамкою навколо тексту."""
        key = self._selected_key()
        if key is None:
            return
        shot = self.shots.get(key)
        if not shot or not os.path.exists(shot['файл']):
            self.status.set('Для цього рядка знімка немає (побачено до того, як з\'явились знімки).')
            return
        try:
            img = Image.open(shot['файл'])
            img.load()
        except OSError:
            return
        self.pinned = key
        self._draw(img, [(1.0, key, shot['стан'], shot['рамка'], shot['текст'])], width=4)
        self.b_live.pack(side='right', padx=(6, 0))
        self.status.set(f'Знімок з журналу ({shot["час"]}): «{shot["текст"][:80]}». '
                        'Права кнопка — зберегти в нагадування; «Наживо» — назад до гри.')

    def _zoom(self):
        import imageview
        if self.pinned and self.pinned in self.shots:
            sh = self.shots[self.pinned]
            if os.path.exists(sh['файл']):
                imageview.ImageView(self, sh['файл'], title=sh['текст'][:80],
                                    boxes=[(*sh['рамка'], STATES[sh['стан']][1])], colors=self.app.colors())
            return
        last = getattr(self, 'last', None)
        if last:
            img, shown = last
            imageview.ImageView(self, img.copy(), title='Кадр гри',
                                boxes=[(*box, STATES[st][1]) for _r, _k, st, box, _t in shown],
                                colors=self.app.colors())

    def _unpin(self):
        if not self.pinned:
            return
        self.pinned = None
        self.b_live.pack_forget()
        sel = self.list.selection()
        if sel:
            self.list.selection_remove(*sel)
        last = getattr(self, 'last', None)
        if last:
            self._draw(*last)

    def _menu(self, ev):
        iid = self.list.identify_row(ev.y)
        if not iid:
            return
        self.list.selection_set(iid)
        key = self._selected_key()
        m = tk.Menu(self, tearoff=False)
        m.add_command(label='Зберегти в нагадування', command=lambda: self._to_reminders(key))
        if key and not key.startswith('ocr:'):
            m.add_command(label='Відкрити рядок у редакторі', command=self._open)
        m.tk_popup(ev.x_root, ev.y_root)

    def _to_reminders(self, key):
        """Рядок журналу (зі знімком, якщо є) — у «Нагадування» → «Збережений журнал екрану гри»."""
        import reminders
        iid = self.rows.get(key)
        if iid is None or not self.list.exists(iid):
            return
        st_label, who, text = self.list.item(iid, 'values')
        shot = self.shots.get(key) or {}
        try:
            store = reminders.Store(self.pr.xl)
            store.add('екран', text, image=shot.get('файл'), стан=st_label, хто=who or None,
                      ключ=None if key.startswith('ocr:') else key, рамка=shot.get('рамка'))
        except OSError as ex:
            self.status.set(f'Не вдалося зберегти нагадування: {ex}')
            return
        self.status.set('Збережено в «Нагадування» (головне вікно програми → «Нагадування…»).')
        notify = getattr(self.app, 'reminders_changed', None)
        if notify:
            notify()
