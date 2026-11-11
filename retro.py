"""Retro console ROMs for webMAN MOD's ROM launcher (plays them through RetroArch on the PS3).

webMAN scans <drive>/ROMS/<SYSTEM>/ where SYSTEM is one of its known folder names.
"""
from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass

from discinfo import GameInfo, UnsupportedError


@dataclass(frozen=True)
class Platform:
    folder: str        # webMAN ROMS sub-folder
    name: str          # display name
    libretro: str      # libretro-thumbnails playlist name (for box art)
    exts: tuple


PLATFORMS = [
    Platform('NES', 'NES', 'Nintendo - Nintendo Entertainment System', ('.nes', '.fds', '.unf', '.unif')),
    Platform('SNES', 'Super Nintendo', 'Nintendo - Super Nintendo Entertainment System',
             ('.smc', '.sfc', '.fig', '.swc', '.bs')),
    Platform('GB', 'Game Boy', 'Nintendo - Game Boy', ('.gb',)),
    Platform('GBC', 'Game Boy Color', 'Nintendo - Game Boy Color', ('.gbc',)),
    Platform('GBA', 'Game Boy Advance', 'Nintendo - Game Boy Advance', ('.gba', '.agb')),
    Platform('N64', 'Nintendo 64', 'Nintendo - Nintendo 64', ('.n64', '.z64', '.v64')),
    Platform('VBOY', 'Virtual Boy', 'Nintendo - Virtual Boy', ('.vb',)),
    Platform('GENESIS', 'Genesis / Mega Drive', 'Sega - Mega Drive - Genesis', ('.md', '.smd', '.gen')),
    Platform('MASTER', 'Master System', 'Sega - Master System - Mark III', ('.sms',)),
    Platform('GG', 'Game Gear', 'Sega - Game Gear', ('.gg',)),
    Platform('SG1000', 'SG-1000', 'Sega - SG-1000', ('.sg',)),
    Platform('PCE', 'PC Engine / TurboGrafx-16', 'NEC - PC Engine - TurboGrafx 16', ('.pce',)),
    Platform('SGX', 'PC Engine SuperGrafx', 'NEC - PC Engine SuperGrafx', ('.sgx',)),
    Platform('NGP', 'Neo Geo Pocket', 'SNK - Neo Geo Pocket', ('.ngp',)),
    Platform('NGPC', 'Neo Geo Pocket Color', 'SNK - Neo Geo Pocket Color', ('.ngc', '.ngpc', '.npc')),
    Platform('ATARI2600', 'Atari 2600', 'Atari - 2600', ('.a26',)),
    Platform('ATARI7800', 'Atari 7800', 'Atari - 7800', ('.a78',)),
    Platform('LYNX', 'Atari Lynx', 'Atari - Lynx', ('.lnx',)),
    Platform('JAGUAR', 'Atari Jaguar', 'Atari - Jaguar', ('.j64', '.jag')),
    Platform('WSWAM', 'WonderSwan', 'Bandai - WonderSwan', ('.ws',)),
    Platform('WSWAMC', 'WonderSwan Color', 'Bandai - WonderSwan Color', ('.wsc',)),
    Platform('COLECO', 'ColecoVision', 'Coleco - ColecoVision', ('.col',)),
    Platform('INTV', 'Intellivision', 'Mattel - Intellivision', ('.int',)),
    Platform('VECX', 'Vectrex', 'GCE - Vectrex', ('.vec',)),
    Platform('MSX', 'MSX', 'Microsoft - MSX', ('.mx1', '.mx2')),
    Platform('FBNEO', 'Arcade (FinalBurn Neo)', 'FBNeo - Arcade Games', ()),
    Platform('MAME', 'Arcade (MAME)', 'MAME', ()),
]
BY_FOLDER = {p.folder: p for p in PLATFORMS}
BY_EXT = {e: p for p in PLATFORMS for e in p.exts}
ARCHIVE_EXTS = ('.zip', '.7z')
ROM_EXTS = tuple(BY_EXT) + ARCHIVE_EXTS

# Every ROMS sub-folder webMAN MOD knows (from its roms_paths.txt), used when scanning a drive.
WEBMAN_FOLDERS = set('''2048 CAP32 MAME MAME2000 MAME2003 MIDWAY MAMEPLUS FBA FBA2012 FBNEO ATARI ATARI2600 STELLA
ATARI800 ATARI5200 ATARI7800 JAGUAR LYNX HANDY HATARI CANNONBALL NXENGINE COLECO AMIGA CD32 VICE X64 X64SC X64DTV
XSCPU64 X128 XCBM2 XCMB25X0 XPET XPLUS4 XVIC DOSBOX GME GW DOOM QUAKE JAVAME JUMP O2EM INTV MSX FMSX MSX2 BMSX NEOCD
NEO NEOGEO PCE PCECD PCFX SGX NGP NGPC N64 NES FCEUMM NESTOPIA QNES GB GBC GAMBATTE TGBDUAL GBA VBA MGBA VBOY PALM
POKEMINI SCUMMVM GENESIS GEN SEGACD MEGAD MEGADRIVE GG GEARBOY MASTER PICO SG1000 FUSE ZX81 SNES MSNES SNES9X
SNES9X2005 SNES9X2010 SNES9X_NEXT THEODORE VECX WSWAM WSWAMC'''.split())


class NeedsPlatform(Exception):
    """The ROM's system could not be detected; ask the user."""


def is_rom(path: str) -> bool:
    return os.path.isfile(path) and path.lower().endswith(ROM_EXTS)


def is_genesis_bin(path: str) -> bool:
    try:
        with open(path, 'rb') as f:
            f.seek(0x100)
            return f.read(4) == b'SEGA'
    except OSError:
        return False


def detect(path: str) -> Platform | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in BY_EXT:
        return BY_EXT[ext]
    if ext == '.bin' and is_genesis_bin(path):
        return BY_FOLDER['GENESIS']
    if ext == '.zip':
        try:
            with zipfile.ZipFile(path) as z:
                found = {BY_EXT[os.path.splitext(n)[1].lower()] for n in z.namelist()
                         if os.path.splitext(n)[1].lower() in BY_EXT}
            if len(found) == 1:
                return found.pop()
        except (zipfile.BadZipFile, OSError):
            pass
    return None


def identify(path: str, platform: str | None = None) -> GameInfo:
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise UnsupportedError('File not found')
    plat = BY_FOLDER.get(platform) if platform else detect(path)
    if plat is None:
        raise NeedsPlatform(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    return GameInfo('RETRO', 'ROM', stem, '', path, [path], size=os.path.getsize(path),
                    largest_file=os.path.getsize(path), platform=plat.folder)


def platform_name(folder: str) -> str:
    p = BY_FOLDER.get(folder)
    return p.name if p else folder
