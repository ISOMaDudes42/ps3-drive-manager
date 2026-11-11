"""PS3 XMB themes (.p3t): copy to <USB>/PS3/THEME so the PS3 can install them."""
from __future__ import annotations

import os
import shutil

THEME_REL = os.path.join('PS3', 'THEME')
P3T_MAGIC = b'P3TF'


class ThemeError(Exception):
    pass


def validate(path: str):
    with open(path, 'rb') as f:
        if f.read(4) != P3T_MAGIC:
            raise ThemeError(f'{os.path.basename(path)} is not a PS3 theme (.p3t) file')


def theme_dir(root: str) -> str:
    return os.path.join(root, THEME_REL)


def list_themes(root: str) -> list[str]:
    d = theme_dir(root)
    if not os.path.isdir(d):
        return []
    return sorted((os.path.join(d, n) for n in os.listdir(d) if n.lower().endswith('.p3t')), key=str.lower)


def add_theme(src: str, root: str) -> str:
    validate(src)
    d = theme_dir(root)
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, os.path.basename(src))
    if os.path.exists(dest):
        raise ThemeError(f'{os.path.basename(src)} is already on the drive')
    if shutil.disk_usage(root).free < os.path.getsize(src) + 4 * 1024 * 1024:
        raise ThemeError('Not enough free space on the drive')
    shutil.copyfile(src, dest)
    return dest


def preview_bytes(path: str, limit: int = 64 * 1024 * 1024) -> bytes | None:
    """Best effort: find the largest JPEG or PNG image stored inside the theme file."""
    with open(path, 'rb') as f:
        data = f.read(limit)
    best = b''
    pos = 0
    while (i := data.find(b'\x89PNG\r\n\x1a\n', pos)) != -1:
        end = data.find(b'IEND', i)
        if end == -1:
            break
        blob = data[i:end + 8]
        if len(blob) > len(best):
            best = blob
        pos = end
    pos = 0
    while (i := data.find(b'\xff\xd8\xff', pos)) != -1:
        end = data.find(b'\xff\xd9', i + 3)
        if end == -1:
            break
        blob = data[i:end + 2]
        if len(blob) > len(best):
            best = blob
        pos = i + 3
    return best or None
