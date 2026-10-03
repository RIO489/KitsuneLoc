# -*- coding: utf-8 -*-
"""Ключі сервісів перекладу: `mt.key` у теці програми, {"claude": "...", "deepl": "...",
"google": "..."}. У .gitignore — ключ особистий (платний рахунок), до git не потрапляє."""
import json, os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'mt.key')


def load():
    try:
        with open(PATH, encoding='utf-8') as f:
            d = json.load(f)
        return {k: v.strip() for k, v in d.items() if isinstance(v, str) and v.strip()} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def get(name):
    return load().get(name, '')


def put(name, key):
    d = load()
    key = (key or '').strip()
    if key:
        d[name] = key
    else:
        d.pop(name, None)
    with open(PATH + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(PATH + '.tmp', PATH)


def shown(key):
    """Ключ для показу: лише краї."""
    return f'{key[:7]}…{key[-4:]}' if len(key) > 14 else ('задано' if key else '')
