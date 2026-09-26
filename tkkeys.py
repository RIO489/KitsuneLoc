# -*- coding: utf-8 -*-
"""Ctrl+V / C / X / A / Z / Y у полях вводу за будь-якої розкладки + меню правої кнопки.

Tk у Windows прив'язує ці клавіші до латинських літер (keysym 'v', 'c'…). В українській
розкладці та сама клавіша дає keysym 'Cyrillic_em' — і вставка мовчки не працює (перекладач
пише українською, тож не працювала майже завжди). Розпізнаємо за фізичною клавішею
(keycode Windows), а коли розкладка латинська — Tk впорається сам.
"""
import tkinter as tk

# віртуальні коди клавіш Windows: V C X A Z Y
_ACTIONS = {86: '<<Paste>>', 67: '<<Copy>>', 88: '<<Cut>>', 65: '<<SelectAll>>',
            90: '<<Undo>>', 89: '<<Redo>>'}
_CLASSES = ('Text', 'Entry', 'TEntry', 'TCombobox', 'Spinbox', 'TSpinbox')


def _ctrl_key(ev):
    action = _ACTIONS.get(ev.keycode)
    if action is None or (len(ev.keysym) == 1 and ev.keysym.isascii()):
        return None                     # латинська розкладка — стандартні прив'язки Tk
    ev.widget.event_generate(action)
    return 'break'


def _menu(ev):
    w = ev.widget
    try:
        w.focus_set()
    except tk.TclError:
        return
    ro = str(w.cget('state')) in ('disabled', 'readonly') if 'state' in w.keys() else False
    m = tk.Menu(w, tearoff=0)
    m.add_command(label='Вирізати', command=lambda: w.event_generate('<<Cut>>'),
                  state='disabled' if ro else 'normal')
    m.add_command(label='Копіювати', command=lambda: w.event_generate('<<Copy>>'))
    m.add_command(label='Вставити', command=lambda: w.event_generate('<<Paste>>'),
                  state='disabled' if ro else 'normal')
    m.add_separator()
    m.add_command(label='Виділити все', command=lambda: w.event_generate('<<SelectAll>>'))
    try:
        m.tk_popup(ev.x_root, ev.y_root)
    finally:
        m.grab_release()
    return 'break'


def install(root):
    """Один раз на програму (прив'язки класів діють на всі вікна)."""
    if getattr(root, '_kl_keys', False):
        return
    root._kl_keys = True
    for cls in _CLASSES:
        root.bind_class(cls, '<Control-KeyPress>', _ctrl_key, add='+')
        root.bind_class(cls, '<Button-3>', _menu, add='+')
