#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KitsuneLoc — переклад ігор українською (Crystar, Mary Skelter: Nightmares, Neptunia Re;Birth1).

Три кроки: дістати текст з гри -> перекласти в редакторі -> залити назад.

З 3.1 вікно — Qt (qtui/main.py), робоча логіка — core.py. Старе вікно (Tk) — Переклад-старе.pyw:
запасне, поки нове обкатується. Немає бібліотеки PySide6 — пропонуємо «Встановити.bat» і відкриваємо
старе вікно.
"""
import os, runpy, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _old_window(reason):
    """Без PySide6: сказати, що робити, і відкрити старе вікно."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning('KitsuneLoc', f'{reason}\n\nДвічі клацни «Встановити.bat» у теці програми — '
                               'він доставить бібліотеку PySide6 для нового вікна.\n\nЗараз відкриється старе вікно.')
        root.destroy()
    except Exception:                                   # noqa: BLE001
        pass
    runpy.run_path(os.path.join(HERE, 'Переклад-старе.pyw'), run_name='__main__')


if __name__ == '__main__':
    try:
        import PySide6                                  # noqa: F401
    except ImportError:
        _old_window('Нове вікно програми потребує бібліотеки PySide6, а її ще немає.')
        sys.exit(0)
    from qtui.main import main
    sys.exit(main())
