# -*- coding: utf-8 -*-
"""«Створити інсталятор…» — переклад, уже залитий у гру, одним .zip для інших людей.

Спільне для всіх ігор: у zip ідуть файли гри, які відрізняються від оригіналів у
backup\\<гра> (їх замінила кнопка «2»), і наші власні файли без оригіналу (MSK —
ua_strings.bin + dinput8.dll, Unreal — мод-пак). Встановлює скрипт PowerShell
(`інсталятор/install.ps1`), запускається двома .bat (лише ASCII — cmd.exe ламає
кирилицю в батниках). Він сам шукає гру в Steam, перевіряє, що вона закрита,
кладе оригінали в <тека гри>\\KitsuneLoc_backup і вміє все повернути.

Будова zip:
    <Гра> — українська\\
        Встановити переклад.bat, Видалити переклад.bat, Прочитай мене.txt
        kitsuneloc\\install.ps1, kitsuneloc\\patch.json, kitsuneloc\\files\\<шлях у грі>
"""
import os, json, time, hashlib, filecmp, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PS1 = os.path.join(HERE, 'інсталятор', 'install.ps1')

BAT_INSTALL = (
    '@echo off\r\n'
    'if not exist "%~dp0kitsuneloc\\install.ps1" (\r\n'
    '  echo Unpack the whole zip archive first, then run this file again.\r\n'
    '  pause\r\n'
    '  exit /b 1\r\n'
    ')\r\n'
    'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0kitsuneloc\\install.ps1"{arg}\r\n'
    'pause\r\n')


def md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest().upper()           # як Get-FileHash


def changed_files(data_dir, backup_dir):
    """Відносні шляхи файлів гри, що відрізняються від оригіналів у backup."""
    out = []
    for dp, _d, fs in os.walk(backup_dir):
        for fn in fs:
            orig = os.path.join(dp, fn)
            rel = os.path.relpath(orig, backup_dir)
            cur = os.path.join(data_dir, rel)
            if not os.path.isfile(cur):
                continue
            a, b = os.stat(orig), os.stat(cur)
            if a.st_size == b.st_size and (abs(a.st_mtime - b.st_mtime) < 2
                                           or filecmp.cmp(orig, cur, shallow=False)):
                continue                    # у грі оригінал (напр. текстури вимкнено)
            out.append(rel)
    return sorted(out)


def readme(title, note):
    text = (
        f'Український переклад гри «{title}»\r\n'
        '(зроблено програмою KitsuneLoc)\r\n\r\n'
        'ЯК ВСТАНОВИТИ\r\n'
        '1. Розпакуйте весь архів (правою кнопкою -> «Видобути все…»).\r\n'
        '2. Закрийте гру.\r\n'
        '3. Запустіть «Встановити переклад.bat».\r\n'
        '   Програма сама знайде гру в Steam. Якщо не знайде — попросить показати теку гри.\r\n\r\n'
        'ЯК ПОВЕРНУТИ ОРИГІНАЛ\r\n'
        'Запустіть «Видалити переклад.bat». Оригінальні файли збережено в теці гри,\r\n'
        'у підтеці KitsuneLoc_backup — не видаляйте її, поки переклад встановлено.\r\n'
        'Можна й так: Steam -> Властивості гри -> Встановлені файли ->\r\n'
        '«Перевірити цілісність файлів гри».\r\n\r\n'
        'ВАЖЛИВО\r\n'
        '- Переклад зроблено для поточної версії гри в Steam. Якщо гру оновлять,\r\n'
        '  інсталятор попередить, що файли гри інші.\r\n')
    if note:
        text += f'- {note}\r\n'
    return text


def build(out_zip, game, data_dir, backup_dir, extra=(), note='', version='',
          step=lambda i, n, label='': None, say=lambda *a: None):
    """Зібрати інсталятор. `game` — запис GAMES (+ 'root', 'data' — тека даних відносно
    кореня гри). `extra` — наші файли без оригіналу (відносно data_dir).
    Повертає (к-сть файлів, розмір zip)."""
    rels = changed_files(data_dir, backup_dir)
    extra = [r for r in extra if os.path.isfile(os.path.join(data_dir, r)) and r not in rels]
    if not rels and not extra:
        raise RuntimeError('У грі немає перекладу — нема чого класти в інсталятор.\n'
                           'Спершу натисни «2. Залити переклад у гру».')
    files = []
    todo = [(r, True) for r in rels] + [(r, False) for r in extra]
    for k, (rel, has_orig) in enumerate(todo, 1):
        step(k, len(todo), 'Перевіряю файли')
        cur = os.path.join(data_dir, rel)
        e = {'path': rel, 'size': os.path.getsize(cur), 'md5': md5(cur)}
        if has_orig:
            orig = os.path.join(backup_dir, rel)
            e['orig_size'], e['orig_md5'] = os.path.getsize(orig), md5(orig)
        files.append(e)
    manifest = {
        'kitsuneloc': version, 'made': time.strftime('%Y-%m-%d %H:%M'),
        'title': game['title'], 'steam': game['steam'], 'exe': game['exe'],
        'marker': game['marker'], 'data': game.get('data', ''),
        'note': note, 'files': files,
    }
    top = f'{game["folder"]} — українська'
    tmp = out_zip + '.part'
    total = sum(e['size'] for e in files)
    try:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr(f'{top}/Встановити переклад.bat', BAT_INSTALL.format(arg=''))
            z.writestr(f'{top}/Видалити переклад.bat', BAT_INSTALL.format(arg=' -Uninstall'))
            z.writestr(f'{top}/Прочитай мене.txt', '﻿' + readme(game['title'], note))
            z.write(PS1, f'{top}/kitsuneloc/install.ps1')
            z.writestr(f'{top}/kitsuneloc/patch.json',
                       json.dumps(manifest, ensure_ascii=False, indent=1))
            done = 0
            for e in files:
                step(done >> 20, max(1, total >> 20), 'Пакую в zip')
                z.write(os.path.join(data_dir, e['path']),
                        f'{top}/kitsuneloc/files/' + e['path'].replace(os.sep, '/'))
                say(f'  + {e["path"]}')
                done += e['size']
        os.replace(tmp, out_zip)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return len(files), os.path.getsize(out_zip)
