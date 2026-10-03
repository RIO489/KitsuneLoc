# -*- coding: utf-8 -*-
"""Запуск Qt-редактора окремо (прототип): python -m qtui.run <гра> [--theme stars]
[--work ТЕКА --xl ТЕКА --backup ТЕКА] — за замовчуванням ті самі теки, що й у програми."""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')


def games():
    """GAMES програми (core.py)."""
    import core
    return core.GAMES


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('game')
    ap.add_argument('--theme')
    ap.add_argument('--work')
    ap.add_argument('--xl')
    ap.add_argument('--backup')
    ap.add_argument('--demo', action='store_true',
                    help='подивитись будь-яку гру: нічого не записується (ні переклад.json, ні work)')
    a = ap.parse_args(argv)
    g = games()[a.game]
    try:
        settings = json.load(open(os.path.join(HERE, 'settings.json'), encoding='utf-8'))
    except (OSError, ValueError):
        settings = {}
    theme = a.theme or settings.get('theme', 'stars')
    work = a.work or os.path.join(HERE, 'work', a.game)
    xl = a.xl or os.path.join(HERE, 'Переклад', g['folder'])
    bk = a.backup or os.path.join(HERE, 'backup', a.game)

    from PySide6.QtWidgets import QApplication
    import project
    from qtui.editor import EditorWindow
    app = QApplication.instance() or QApplication(sys.argv)
    if not project.enabled(xl) and not a.demo:
        # перехід з книг Excel на редактор (Project.create, з підтвердженням) робить програма;
        # тут не створюємо переклад.json мимохідь — гра лишається на книгах
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(None, 'Редактор перекладу',
                                f'{g["title"]}: переклад ще в книгах Excel. Спершу відкрий «Редактор '
                                'перекладу…» в програмі — вона перенесе переклад у редактор.')
        return 1
    pr = project.Project(a.game, work, xl)
    if a.demo:                       # огляд: правки живуть лише у вікні, на диск — нічого
        pr.save = pr.sync_work = lambda: None
    win = EditorWindow(pr, bk, theme, title=f'Редактор перекладу — {g["title"]}'
                       + (' (огляд: нічого не зберігається)' if a.demo else ''))
    win.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
