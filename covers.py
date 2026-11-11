"""Download box art and save it next to the game where webMAN looks for it."""
from __future__ import annotations

import os
import re
import urllib.error
import urllib.parse
import urllib.request

from discinfo import GameInfo

USER_AGENT = 'PS3DriveManager/1.0'

# {id} = game ID (BLUS30443 / SLUS-20062), {region} = GameTDB region code.
# Editable in settings.json if a source moves.
DEFAULT_TEMPLATES = {
    'PS3': ['https://art.gametdb.com/ps3/cover/{region}/{id}.jpg',
            'https://art.gametdb.com/ps3/coverM/{region}/{id}.jpg'],
    'PS2': ['https://raw.githubusercontent.com/xlenore/ps2-covers/main/covers/default/{id}.jpg'],
    'PS1': ['https://raw.githubusercontent.com/xlenore/psx-covers/main/covers/default/{id}.jpg'],
}

ALL_REGIONS = ['US', 'EN', 'JA', 'FR', 'DE', 'ES', 'IT', 'KO', 'ZH']
REGION_HINT = {'U': ['US'], 'E': ['EN', 'FR', 'DE', 'ES', 'IT'], 'J': ['JA'], 'A': ['ZH', 'US'], 'K': ['KO']}


def _regions(game_id: str) -> list[str]:
    preferred = REGION_HINT.get(game_id[2:3].upper(), [])
    return preferred + [r for r in ALL_REGIONS if r not in preferred]


def _download(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read(8 * 1024 * 1024)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    if data[:3] == b'\xff\xd8\xff' or data[:8] == b'\x89PNG\r\n\x1a\n':
        return data
    return None


LIBRETRO_URL = 'https://thumbnails.libretro.com/{playlist}/Named_Boxarts/{name}.png'


def _libretro_names(stem: str) -> list[str]:
    """libretro thumbnail names: the ROM name with &*/:`<>?\\|" replaced by _, plus a tag-stripped variant."""
    clean = re.sub(r'[&*/:`<>?\\|"]', '_', stem)
    names = [clean]
    no_brackets = re.sub(r'\s*\[[^\]]*\]', '', clean).strip()  # GoodTools tags like [!]
    if no_brackets and no_brackets != clean:
        names.append(no_brackets)
    return names


def _save(data: bytes, dest_base: str) -> str:
    path = dest_base + ('.png' if data[:4] == b'\x89PNG' else '.jpg')
    with open(path, 'wb') as f:
        f.write(data)
    return path


def fetch_retro_cover(info: GameInfo, dest_base: str) -> str | None:
    from retro import BY_FOLDER
    plat = BY_FOLDER.get(info.platform)
    if not plat:
        return None
    stem = os.path.splitext(os.path.basename(info.path))[0]
    for name in _libretro_names(stem):
        url = LIBRETRO_URL.format(playlist=urllib.parse.quote(plat.libretro), name=urllib.parse.quote(name))
        data = _download(url)
        if data:
            return _save(data, dest_base)
    return None


def fetch_cover(info: GameInfo, dest_base: str, templates: dict | None = None) -> str | None:
    """Try each source; save as dest_base + .jpg/.png. Returns saved path or None."""
    if info.system == 'RETRO':
        return fetch_retro_cover(info, dest_base)
    if not info.game_id or info.system not in ('PS3', 'PS2', 'PS1'):
        return None
    templates = templates or DEFAULT_TEMPLATES
    for tpl in templates.get(info.system, []):
        regions = _regions(info.game_id) if '{region}' in tpl else ['']
        for region in regions:
            data = _download(tpl.format(id=info.game_id, region=region))
            if data:
                return _save(data, dest_base)
    return None
