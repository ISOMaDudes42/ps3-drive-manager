"""Identify PS3 / PS2 / PS1 game images and folders by reading their contents."""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field

FAT32_MAX_FILE = 0xFFFFFFFF  # 4 GiB - 1 byte

CUE_FILE_RE = re.compile(r'^(\s*)FILE\s+(?:"([^"]+)"|(\S+))\s+(\w+)\s*$', re.M | re.I)
SPLIT_RE = re.compile(r'\.iso\.(\d+)$', re.I)


class UnsupportedError(Exception):
    """The path is not a game this tool understands."""


@dataclass
class GameInfo:
    system: str              # 'PS3', 'PS2', 'PS1', 'RETRO' or '?'
    fmt: str                 # 'ISO', 'Split ISO', 'Folder', 'BIN/CUE', 'BIN', 'ROM'
    title: str
    game_id: str
    path: str                # main file (.iso / .iso.0 / .cue / .bin) or the game folder
    files: list[str] = field(default_factory=list)
    size: int = 0
    largest_file: int = 0
    warnings: list[str] = field(default_factory=list)
    cover: str | None = None
    platform: str = ''       # retro games: webMAN ROMS folder (SNES, GBA, ...)

    def __eq__(self, other):  # identity semantics: two scans of one file are different objects
        return self is other

    __hash__ = object.__hash__

    @property
    def display_id(self) -> str:
        return self.game_id or '-'


# --------------------------------------------------------------------------- ISO9660

class DiscImage:
    """Minimal read-only ISO9660 reader. Handles 2048-byte ISOs and raw 2352-byte BIN images."""

    LAYOUTS = [(2048, 0), (2352, 24), (2352, 16)]  # (sector size, user-data offset)

    def __init__(self, path: str):
        self.f = open(path, 'rb')
        try:
            self.sector_size, self.offset = self._detect()
            pvd = self.read_sectors(16)
            root = pvd[156:156 + 34]
            self.root_lba = struct.unpack_from('<I', root, 2)[0]
            self.root_size = struct.unpack_from('<I', root, 10)[0]
        except Exception:
            self.f.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.f.close()

    def _detect(self):
        for size, off in self.LAYOUTS:
            self.f.seek(16 * size + off)
            if self.f.read(6) == b'\x01CD001':
                return size, off
        raise UnsupportedError('Not a readable disc image (no ISO9660 filesystem found)')

    def read_sectors(self, lba: int, count: int = 1) -> bytes:
        if self.sector_size == 2048:
            self.f.seek(lba * 2048)
            return self.f.read(count * 2048)
        out = bytearray()
        for i in range(count):
            self.f.seek((lba + i) * self.sector_size + self.offset)
            out += self.f.read(2048)
        return bytes(out)

    def list_dir(self, lba: int, size: int) -> dict:
        data = self.read_sectors(lba, (size + 2047) // 2048)[:size]
        entries = {}
        pos = 0
        while pos < len(data):
            length = data[pos]
            if length == 0:  # records never cross sector boundaries; skip padding
                pos = (pos // 2048 + 1) * 2048
                continue
            rec = data[pos:pos + length]
            if len(rec) < 34:
                break
            raw = rec[33:33 + rec[32]]
            if raw not in (b'\x00', b'\x01'):
                name = raw.decode('ascii', 'replace').split(';')[0].rstrip('.').upper()
                entries[name] = (struct.unpack_from('<I', rec, 2)[0],
                                 struct.unpack_from('<I', rec, 10)[0],
                                 bool(rec[25] & 2))
            pos += length
        return entries

    def find(self, path: str):
        lba, size, is_dir = self.root_lba, self.root_size, True
        for part in path.strip('/').split('/'):
            if not is_dir:
                return None
            entry = self.list_dir(lba, size).get(part.upper())
            if entry is None:
                return None
            lba, size, is_dir = entry
        return lba, size, is_dir

    def read_file(self, path: str, max_bytes: int = 1 << 20) -> bytes | None:
        entry = self.find(path)
        if not entry or entry[2]:
            return None
        lba, size, _ = entry
        size = min(size, max_bytes)
        return self.read_sectors(lba, (size + 2047) // 2048)[:size]


# --------------------------------------------------------------------------- parsers

def parse_sfo(data: bytes) -> dict:
    """Parse a PS3 PARAM.SFO file."""
    if data[:4] != b'\x00PSF':
        raise ValueError('Not a PARAM.SFO file')
    key_start, data_start, count = struct.unpack_from('<III', data, 8)
    out = {}
    for i in range(count):
        koff, fmt, length, _maxlen, doff = struct.unpack_from('<HHIII', data, 20 + i * 16)
        kpos = key_start + koff
        key = data[kpos:data.index(b'\x00', kpos)].decode('ascii', 'replace')
        raw = data[data_start + doff:data_start + doff + length]
        if fmt == 0x0404:
            out[key] = struct.unpack('<I', raw[:4])[0]
        else:
            out[key] = raw.rstrip(b'\x00').decode('utf-8', 'replace')
    return out


def parse_system_cnf(text: str):
    """Return (system, game_id) from a PS1/PS2 SYSTEM.CNF."""
    m = re.search(r'^\s*BOOT2\s*=\s*(.+)$', text, re.M | re.I)
    system = 'PS2'
    if not m:
        m = re.search(r'^\s*BOOT\s*=\s*(.+)$', text, re.M | re.I)
        system = 'PS1'
    if not m:
        return '?', ''
    idm = re.search(r'([A-Z]{4})[_-]?(\d{3})\.?(\d{2})', m.group(1).upper())
    return system, (f'{idm.group(1)}-{idm.group(2)}{idm.group(3)}' if idm else '')


def parse_cue(path: str):
    """Return (cue text, [absolute paths of referenced files])."""
    with open(path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    folder = os.path.dirname(path)
    files = [os.path.join(folder, m.group(2) or m.group(3)) for m in CUE_FILE_RE.finditer(text)]
    return text, files


# --------------------------------------------------------------------------- identify

def clean_title(stem: str) -> str:
    return re.sub(r'\s+', ' ', stem.replace('_', ' ')).strip()


def _stem(path: str) -> str:
    name = os.path.basename(path.rstrip('\\/'))
    m = SPLIT_RE.search(name)
    if m:
        return name[:m.start()]
    return os.path.splitext(name)[0]


def split_parts(first_part: str) -> list[str]:
    """Given name.iso.0 return [name.iso.0, name.iso.1, ...] (contiguous parts that exist)."""
    base = first_part[:SPLIT_RE.search(first_part).start()]
    parts, i = [], 0
    while os.path.isfile(f'{base}.iso.{i}'):
        parts.append(f'{base}.iso.{i}')
        i += 1
    return parts


def identify(path: str) -> GameInfo:
    path = os.path.abspath(path)
    if os.path.isdir(path):
        return _identify_folder(path)
    if not os.path.isfile(path):
        raise UnsupportedError('File not found')
    low = path.lower()
    m = SPLIT_RE.search(low)
    if m:
        if m.group(1) != '0':
            raise UnsupportedError('Select the first part (.iso.0) of a split ISO')
        return _identify_image(path, split_parts(path), 'Split ISO')
    ext = os.path.splitext(low)[1]
    if ext == '.cue':
        return _identify_cue(path)
    if ext in ('.bin', '.img'):
        cue = os.path.splitext(path)[0] + '.cue'
        if os.path.isfile(cue):
            return _identify_cue(cue)
        return _identify_image(path, [path], 'BIN')
    if ext == '.iso':
        return _identify_image(path, [path], 'ISO')
    raise UnsupportedError(f'Unsupported file type "{ext}"')


def _finish(info: GameInfo) -> GameInfo:
    sizes = [os.path.getsize(f) for f in info.files]
    info.size = sum(sizes)
    info.largest_file = max(sizes, default=0)
    if info.game_id:  # drop the "[ID]" tag we add to file names so titles display cleanly
        info.title = re.sub(r'\s*\[' + re.escape(info.game_id) + r'\]', '', info.title, flags=re.I).strip() or info.title
    if info.system == '?':
        info.warnings.append('Could not detect the console type of this disc')
    if info.system == 'PS2' and info.fmt in ('BIN', 'BIN/CUE'):
        info.warnings.append('webMAN needs PS2 games as .iso; convert this image first')
    if info.system == 'PS3' and info.fmt in ('BIN', 'BIN/CUE'):
        info.warnings.append('PS3 games must be .iso or folder format')
    return info


def _identify_image(image: str, files: list[str], fmt: str, main: str | None = None) -> GameInfo:
    stem = _stem(main or image)
    with DiscImage(image) as disc:
        sfo = disc.read_file('PS3_GAME/PARAM.SFO')
        if sfo:
            p = parse_sfo(sfo)
            title = re.sub(r'\s+', ' ', str(p.get('TITLE', ''))).strip() or clean_title(stem)
            info = GameInfo('PS3', fmt, title, str(p.get('TITLE_ID', '')), main or image, files)
        elif disc.find('PS3_DISC.SFB'):
            info = GameInfo('PS3', fmt, clean_title(stem), '', main or image, files)
        else:
            cnf = disc.read_file('SYSTEM.CNF', 64 * 1024)
            system, gid = parse_system_cnf(cnf.decode('latin-1')) if cnf else ('?', '')
            if system == '?' and disc.sector_size == 2352:
                system = 'PS1'  # raw CD image without SYSTEM.CNF: almost always PS1
            info = GameInfo(system, fmt, clean_title(stem), gid, main or image, files)
    return _finish(info)


def _identify_cue(cue: str) -> GameInfo:
    _text, bins = parse_cue(cue)
    if not bins:
        raise UnsupportedError('CUE sheet does not reference any files')
    missing = [b for b in bins if not os.path.isfile(b)]
    if missing:
        raise UnsupportedError(f'CUE references missing file: {os.path.basename(missing[0])}')
    info = _identify_image(bins[0], [cue] + bins, 'BIN/CUE', main=cue)
    if len(bins) > 1:
        info.warnings.append('Multi-file BIN/CUE; if it will not boot, merge into one .bin (e.g. with binmerge)')
    return info


def _identify_folder(folder: str) -> GameInfo:
    sfo_path = os.path.join(folder, 'PS3_GAME', 'PARAM.SFO')
    if not os.path.isfile(sfo_path):
        raise UnsupportedError('Folder is not a PS3 game (no PS3_GAME\\PARAM.SFO)')
    with open(sfo_path, 'rb') as f:
        p = parse_sfo(f.read())
    files = []
    for dirpath, _dirs, names in os.walk(folder):
        files.extend(os.path.join(dirpath, n) for n in names)
    title = re.sub(r'\s+', ' ', str(p.get('TITLE', ''))).strip() or clean_title(_stem(folder))
    return _finish(GameInfo('PS3', 'Folder', title, str(p.get('TITLE_ID', '')), folder, files))


def find_candidates(folder: str) -> list[str]:
    """Recursively find importable games under a folder (does not descend into PS3 game folders)."""
    found = []
    from retro import ROM_EXTS
    for dirpath, dirs, names in os.walk(folder):
        if os.path.isfile(os.path.join(dirpath, 'PS3_GAME', 'PARAM.SFO')):
            found.append(dirpath)
            dirs[:] = []
            continue
        lower = {n.lower() for n in names}
        for n in names:
            low = n.lower()
            ext = os.path.splitext(low)[1]
            if ext in ('.iso', '.cue') or low.endswith('.iso.0') or ext in ROM_EXTS:
                found.append(os.path.join(dirpath, n))
            elif ext in ('.bin', '.img') and (os.path.splitext(low)[0] + '.cue') not in lower:
                found.append(os.path.join(dirpath, n))
    return found
