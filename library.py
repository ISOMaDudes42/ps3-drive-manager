"""Drive handling: folder layout, scanning, importing and removing games."""
from __future__ import annotations

import os
import re
import shutil
import string
import threading
import time
from dataclasses import dataclass

import retro
from discinfo import (CUE_FILE_RE, FAT32_MAX_FILE, SPLIT_RE, GameInfo, UnsupportedError,
                      identify, parse_cue)

SYSTEM_DIRS = {'PS3': 'PS3ISO', 'PS2': 'PS2ISO', 'PS1': 'PSXISO'}
FOLDER_DIR = 'GAMES'
ROMS_DIR = 'ROMS'
ALL_DIRS = ['PS3ISO', 'PS2ISO', 'PSXISO', 'GAMES', 'ROMS']
SPLIT_SIZE = 0xFFFF0000      # part size used when splitting ISOs for FAT32
CHUNK = 4 * 1024 * 1024
COVER_EXTS = ('.jpg', '.png')


class Cancelled(Exception):
    pass


class ImportProblem(Exception):
    pass


class Progress:
    """Thread-safe-ish progress counter that also checks for cancellation."""

    def __init__(self, total: int, callback, cancel: threading.Event):
        self.total = max(total, 1)
        self.done = 0
        self.callback = callback
        self.cancel = cancel
        self._last = 0.0

    def add(self, n: int):
        if self.cancel.is_set():
            raise Cancelled()
        self.done += n
        now = time.monotonic()
        if now - self._last > 0.1 or self.done >= self.total:
            self._last = now
            self.callback(self.done, self.total)


# --------------------------------------------------------------------------- drives

@dataclass
class DriveInfo:
    root: str
    label: str
    fs: str
    kind: str
    total: int
    free: int

    def describe(self) -> str:
        label = f' "{self.label}"' if self.label else ''
        return (f'{self.root}{label}  [{self.kind}, {self.fs or "?"}]  '
                f'{fmt_size(self.free)} free of {fmt_size(self.total)}')


def volume_info(root: str):
    try:
        import ctypes
        name = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), name, 261, None, None, None, fs, 261)
        if ok:
            return fs.value, name.value
    except Exception:
        pass
    return '', ''


def list_drives() -> list[DriveInfo]:
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
    except Exception:
        return []
    kinds = {2: 'Removable', 3: 'Fixed'}
    system_drive = os.environ.get('SystemDrive', 'C:').upper()
    mask = k32.GetLogicalDrives()
    drives = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f'{letter}:\\'
        kind = kinds.get(k32.GetDriveTypeW(ctypes.c_wchar_p(root)))
        if not kind:
            continue
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        fs, label = volume_info(root)
        if root[:2].upper() == system_drive:
            kind = 'System'
        drives.append(DriveInfo(root, label, fs, kind, usage.total, usage.free))
    return drives


def fmt_size(n: float) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024


def prepare_drive(root: str) -> list[str]:
    created = []
    for d in ALL_DIRS:
        p = os.path.join(root, d)
        if not os.path.isdir(p):
            os.makedirs(p)
            created.append(d)
    return created


# --------------------------------------------------------------------------- naming / covers

def safe_name(name: str, limit: int = 120) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = re.sub(r'\s+', ' ', name).strip().rstrip('. ')
    return (name[:limit].rstrip('. ') or 'Untitled')


def target_base(info: GameInfo, title: str | None = None) -> str:
    title = title or info.title
    if info.system == 'RETRO':  # keep ROM names as-is: cover art lookup relies on them
        return safe_name(title, 180)
    if info.game_id and info.game_id.upper() not in title.upper():
        title = f'{title} [{info.game_id}]'
    return safe_name(title)


def dest_dir_for(info: GameInfo) -> str:
    """Folder relative to the drive root, using '/' (also used for FTP paths)."""
    if info.system == 'RETRO':
        return f'{ROMS_DIR}/{info.platform}'
    return FOLDER_DIR if info.fmt == 'Folder' else SYSTEM_DIRS[info.system]


def cover_base(info: GameInfo) -> str | None:
    """Path (without extension) where webMAN looks for this game's cover, or None for folder games."""
    if info.fmt == 'Folder':
        return None
    m = SPLIT_RE.search(info.path)
    if m:
        return info.path[:m.start()]
    return os.path.splitext(info.path)[0]


def find_cover(info: GameInfo) -> str | None:
    base = cover_base(info)
    if base is None:
        icon = os.path.join(info.path, 'PS3_GAME', 'ICON0.PNG')
        return icon if os.path.isfile(icon) else None
    for ext in COVER_EXTS:
        if os.path.isfile(base + ext):
            return base + ext
    return None


# --------------------------------------------------------------------------- identify many

def identify_any(path: str, platform: str | None = None) -> GameInfo:
    """Identify a PlayStation game or a retro ROM. Raises retro.NeedsPlatform for unknown ROMs."""
    if platform:
        return retro.identify(path, platform)
    low = path.lower()
    if os.path.isfile(path) and low.endswith(retro.ROM_EXTS):
        return retro.identify(path)
    try:
        return identify(path)
    except UnsupportedError:
        if low.endswith('.bin') and retro.is_genesis_bin(path):
            return retro.identify(path, 'GENESIS')
        raise


def identify_many(paths: list[str], platforms: dict | None = None):
    """Identify several paths, ignoring .bin files already claimed by a .cue.

    Returns (infos, errors, needs_platform). `platforms` maps path -> retro folder to force.
    """
    platforms = platforms or {}
    paths = sorted(set(os.path.abspath(p) for p in paths),
                   key=lambda p: (not p.lower().endswith('.cue'), p.lower()))
    infos, errors, needs, claimed = [], [], [], set()
    for p in paths:
        if os.path.normcase(p) in claimed:
            continue
        try:
            info = identify_any(p, platforms.get(p))
        except retro.NeedsPlatform:
            needs.append(p)
            continue
        except (UnsupportedError, OSError, ValueError) as e:
            errors.append((p, str(e)))
            continue
        key = os.path.normcase(info.path)
        if key in claimed:
            continue
        claimed.add(key)
        claimed.update(os.path.normcase(f) for f in info.files)
        infos.append(info)
    return infos, errors, needs


SYSTEM_ORDER = {'PS3': 0, 'PS2': 1, 'PS1': 2, 'RETRO': 3, '?': 4}


def scan_drive(root: str):
    """Return (games, errors) for everything webMAN would see on this drive."""
    candidates, forced = [], {}
    roms = os.path.join(root, ROMS_DIR)
    if os.path.isdir(roms):
        for sub in os.listdir(roms):
            d = os.path.join(roms, sub)
            if not (os.path.isdir(d) and sub.upper() in retro.WEBMAN_FOLDERS):
                continue
            for name in os.listdir(d):
                p = os.path.join(d, name)
                if os.path.isfile(p) and not name.lower().endswith(COVER_EXTS):
                    candidates.append(p)
                    forced[os.path.abspath(p)] = sub.upper()
    for sub in ('PS3ISO', 'PS2ISO', 'PSXISO'):
        d = os.path.join(root, sub)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            low = name.lower()
            ext = os.path.splitext(low)[1]
            if ext in ('.iso', '.cue', '.bin', '.img') or low.endswith('.iso.0'):
                candidates.append(os.path.join(d, name))
    games_dir = os.path.join(root, FOLDER_DIR)
    if os.path.isdir(games_dir):
        for name in os.listdir(games_dir):
            p = os.path.join(games_dir, name)
            if os.path.isdir(p) and os.path.isdir(os.path.join(p, 'PS3_GAME')):
                candidates.append(p)
    games, errors, _needs = identify_many(candidates, forced)
    for g in games:
        g.cover = find_cover(g)
    games.sort(key=lambda g: (SYSTEM_ORDER.get(g.system, 9), g.platform, g.title.lower()))
    return games, errors


# --------------------------------------------------------------------------- import

def _copy_concat(sources, part_path, split_size, created, prog: Progress):
    """Copy the concatenation of `sources` into one or more outputs (split every split_size bytes)."""
    part, written, out = 0, 0, None
    try:
        for src in sources:
            with open(src, 'rb') as f:
                while True:
                    buf = f.read(CHUNK)
                    if not buf:
                        break
                    view = memoryview(buf)
                    while view:
                        if out is None:
                            p = part_path(part)
                            created.append(p)
                            out = open(p, 'wb')
                            written = 0
                        n = min(split_size - written, len(view)) if split_size else len(view)
                        out.write(view[:n])
                        written += n
                        view = view[n:]
                        prog.add(n)
                        if split_size and written >= split_size:
                            out.close()
                            out = None
                            part += 1
    finally:
        if out:
            out.close()


def import_game(info: GameInfo, root: str, fs: str, on_progress, cancel: threading.Event) -> GameInfo:
    fs_u = (fs or '').upper()
    is_fat = fs_u.startswith('FAT')
    if info.system == '?':
        raise ImportProblem('Unknown console type; not copied')
    if info.system in ('PS2', 'PS3') and info.fmt in ('BIN', 'BIN/CUE'):
        raise ImportProblem(f'{info.system} games in {info.fmt} format are not supported by webMAN')

    free = shutil.disk_usage(root).free
    if info.size + 64 * 1024 * 1024 > free:
        raise ImportProblem(f'Not enough space ({fmt_size(info.size)} needed, {fmt_size(free)} free)')

    dest_dir = os.path.join(root, dest_dir_for(info))
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.join(dest_dir, target_base(info))
    prog = Progress(info.size, on_progress, cancel)
    created: list[str] = []

    try:
        if info.fmt == 'Folder':
            if fs_u == 'NTFS':
                raise ImportProblem('webMAN cannot load folder-format games from NTFS drives. '
                                    'Use a FAT32 drive, convert the game to ISO, or send it to the PS3 over FTP.')
            if is_fat and info.largest_file > FAT32_MAX_FILE:
                raise ImportProblem('This game has a file over 4 GB, which FAT32 cannot store. '
                                    'Convert it to ISO (it will be split automatically) or use NTFS.')
            if os.path.exists(base):
                raise ImportProblem('Already on the drive')
            created.append(base)
            for src in info.files:
                rel = os.path.relpath(src, info.path)
                dst = os.path.join(base, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                _copy_concat([src], lambda _i, d=dst: d, 0, [], prog)
            main = base

        elif info.fmt in ('ISO', 'Split ISO'):
            if os.path.exists(base + '.iso') or os.path.exists(base + '.iso.0'):
                raise ImportProblem('Already on the drive')
            need_split = is_fat and info.size > FAT32_MAX_FILE
            if need_split and info.system != 'PS3':
                raise ImportProblem('ISO is over 4 GB; FAT32 cannot hold it. Use an NTFS drive.')
            if need_split:
                _copy_concat(info.files, lambda i: f'{base}.iso.{i}', SPLIT_SIZE, created, prog)
                main = base + '.iso.0'
            else:
                _copy_concat(info.files, lambda _i: base + '.iso', 0, created, prog)
                main = base + '.iso'

        elif info.fmt == 'BIN/CUE':
            cue_src, bins = info.files[0], info.files[1:]
            if os.path.exists(base + '.cue'):
                raise ImportProblem('Already on the drive')
            if len(bins) == 1:
                names = [os.path.basename(base) + os.path.splitext(bins[0])[1]]
            else:
                names = [f'{os.path.basename(base)} (Track {i + 1}){os.path.splitext(b)[1]}'
                         for i, b in enumerate(bins)]
            for b, n in zip(bins, names):
                dst = os.path.join(dest_dir, n)
                if os.path.exists(dst):
                    raise ImportProblem(f'{n} already exists on the drive')
                _copy_concat([b], lambda _i, d=dst: d, 0, created, prog)
            text, _ = parse_cue(cue_src)
            it = iter(names)
            text = CUE_FILE_RE.sub(lambda m: f'{m.group(1)}FILE "{next(it)}" {m.group(4)}', text)
            created.append(base + '.cue')
            with open(base + '.cue', 'w', encoding='utf-8', newline='\r\n') as f:
                f.write(text)
            prog.add(0)
            main = base + '.cue'

        elif info.fmt == 'ROM':
            ext = os.path.splitext(info.path)[1]
            if os.path.exists(base + ext):
                raise ImportProblem('Already on the drive')
            _copy_concat([info.path], lambda _i: base + ext, 0, created, prog)
            main = base + ext

        else:  # single BIN / IMG
            ext = os.path.splitext(info.path)[1].lower()
            if os.path.exists(base + ext):
                raise ImportProblem('Already on the drive')
            _copy_concat([info.path], lambda _i: base + ext, 0, created, prog)
            main = base + ext

    except BaseException:
        for p in reversed(created):
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                elif os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        raise

    new = identify_any(main, info.platform or None)
    new.cover = find_cover(new)
    return new


def rename_game(info: GameInfo, title: str) -> GameInfo:
    """Rename a PS1/PS2 game's files (and cover) to 'title [ID]'. Returns the re-identified game."""
    if info.system not in ('PS1', 'PS2') or info.fmt not in ('ISO', 'BIN', 'BIN/CUE'):
        raise ImportProblem('Only PS1 and PS2 games can be renamed')
    folder = os.path.dirname(info.path)
    new_base = os.path.join(folder, target_base(info, title))
    old_base = os.path.splitext(info.path)[0]
    if os.path.normcase(new_base) == os.path.normcase(old_base):
        return info
    moves = []  # (old, new)
    if info.fmt == 'BIN/CUE':
        cue, bins = info.files[0], info.files[1:]
        stem = os.path.basename(new_base)
        names = ([stem + os.path.splitext(bins[0])[1]] if len(bins) == 1 else
                 [f'{stem} (Track {i + 1}){os.path.splitext(b)[1]}' for i, b in enumerate(bins)])
        moves += [(b, os.path.join(folder, n)) for b, n in zip(bins, names)]
        moves.append((cue, new_base + '.cue'))
    else:
        moves.append((info.path, new_base + os.path.splitext(info.path)[1]))
    for ext in COVER_EXTS:
        if os.path.isfile(old_base + ext):
            moves.append((old_base + ext, new_base + ext))
    for old, new in moves:
        if os.path.normcase(old) != os.path.normcase(new) and os.path.exists(new):
            raise ImportProblem(f'{os.path.basename(new)} already exists')
    done = []
    try:
        for old, new in moves:
            os.rename(old, new)
            done.append((old, new))
        if info.fmt == 'BIN/CUE':
            text, _ = parse_cue(new_base + '.cue')
            it = iter(names)
            text = CUE_FILE_RE.sub(lambda m: f'{m.group(1)}FILE "{next(it)}" {m.group(4)}', text)
            with open(new_base + '.cue', 'w', encoding='utf-8', newline='\r\n') as f:
                f.write(text)
    except Exception:
        for old, new in reversed(done):
            try:
                os.rename(new, old)
            except OSError:
                pass
        raise
    main = new_base + ('.cue' if info.fmt == 'BIN/CUE' else os.path.splitext(info.path)[1])
    new_info = identify(main)
    new_info.cover = find_cover(new_info)
    return new_info


def remove_game(info: GameInfo):
    if info.fmt == 'Folder':
        shutil.rmtree(info.path)
        return
    for f in info.files:
        if os.path.exists(f):
            os.remove(f)
    base = cover_base(info)
    for ext in COVER_EXTS:
        if base and os.path.isfile(base + ext):
            os.remove(base + ext)
