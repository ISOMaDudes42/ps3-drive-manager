"""Build a 'mod kit' USB: firmware update file + homebrew packages + a step-by-step guide.

Homebrew packages are downloaded from their official GitHub releases and verified.
Firmware (HFW / CFW) is never downloaded from an unofficial mirror: the user downloads it
from the official page, and this module validates it before copying it into PS3/UPDATE.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import urllib.request
from dataclasses import dataclass

from library import CHUNK, Progress

USER_AGENT = 'PS3DriveManager/1.0'
PUP_REL = os.path.join('PS3', 'UPDATE', 'PS3UPDAT.PUP')
GUIDE_NAME = 'PS3_MOD_GUIDE.txt'

LINKS = {
    'hen_guide': 'https://consolemods.org/wiki/PS3:PS3HEN',
    'hen_project': 'https://github.com/PS3Xploit/PS3HEN',
    'sony_firmware': 'https://www.playstation.com/en-us/support/hardware/ps3/system-software/',
    'cfw_guide': 'https://consolemods.org/wiki/PS3:Getting_Started',
    'webman': 'https://github.com/aldostools/webMAN-MOD/releases',
}

# Packages fetched from official GitHub releases. Installed on the PS3 from the USB root
# via Package Manager > Install Package Files > Standard (needs HEN or CFW active).
COMPONENTS = [
    dict(id='webman', name='webMAN MOD', repo='aldostools/webMAN-MOD',
         asset=r'^webMAN_MOD_.*_Installer\.pkg$', default=True,
         desc='Game loader and XMB integration. Install while holding L1 for the full edition.'),
    dict(id='prepiso', name='prepISO', repo='aldostools/webMAN-MOD',
         asset=r'^prepISO.*\.pkg$', default=True,
         desc='Lets webMAN load ISO games from NTFS drives (formerly prepNTFS).'),
    dict(id='irisman', name='IRISMAN', repo='aldostools/IRISMAN',
         asset=r'^IRISMAN_[\d.]+\.pkg$', default=True,
         desc='Backup manager: copy your game discs to the HDD, file manager.'),
    dict(id='ps2launcher', name='PS2 Classics Launcher', repo='aldostools/webMAN-MOD',
         asset=r'^PS2_Classics_Launcher\.pkg$', default=False,
         desc='Needed to play PS2 ISOs on consoles without PS2 hardware (most Slims/Super Slims).'),
    dict(id='ps2config', name='PS2 configs', repo='aldostools/webMAN-MOD',
         asset=r'^PS2CONFIG\.pkg$', default=False,
         desc='Compatibility fixes for the PS2 software emulator (about 100 MB).'),
]


class KitError(Exception):
    pass


# --------------------------------------------------------------------------- GitHub downloads

def _get_json(url: str):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT,
                                               'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def resolve(component: dict, cache: dict) -> dict:
    """Return {'name', 'url', 'size', 'digest', 'version'} for the component's latest release asset."""
    repo = component['repo']
    if repo not in cache:
        cache[repo] = _get_json(f'https://api.github.com/repos/{repo}/releases/latest')
    rel = cache[repo]
    for a in rel.get('assets', []):
        if re.match(component['asset'], a['name'], re.I):
            return dict(name=a['name'], url=a['browser_download_url'], size=a['size'],
                        digest=a.get('digest') or '', version=rel.get('tag_name', ''))
    raise KitError(f'No matching file in the latest {repo} release')


def download(asset: dict, dest_dir: str, on_progress, cancel) -> str:
    """Download to dest_dir, verify size / sha256 / PKG header, then move into place."""
    final = os.path.join(dest_dir, asset['name'])
    part = final + '.part'
    prog = Progress(asset['size'], on_progress, cancel)
    sha = hashlib.sha256()
    req = urllib.request.Request(asset['url'], headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(part, 'wb') as out:
            while True:
                buf = r.read(CHUNK // 4)
                if not buf:
                    break
                out.write(buf)
                sha.update(buf)
                prog.add(len(buf))
        size = os.path.getsize(part)
        if size != asset['size']:
            raise KitError(f'Download incomplete ({size} of {asset["size"]} bytes)')
        if asset['digest'].startswith('sha256:') and sha.hexdigest() != asset['digest'][7:].lower():
            raise KitError('Checksum mismatch; the download is corrupt')
        with open(part, 'rb') as f:
            if f.read(4) != b'\x7fPKG':
                raise KitError('Downloaded file is not a PS3 package')
        os.replace(part, final)
        return final
    finally:
        if os.path.exists(part):
            os.remove(part)


def installed_packages(root: str) -> dict:
    """Map component id -> filename of a matching .pkg already in the drive root."""
    try:
        names = os.listdir(root)
    except OSError:
        return {}
    found = {}
    for c in COMPONENTS:
        for n in names:
            if re.match(c['asset'], n, re.I):
                found[c['id']] = n
    return found


def remove_older(root: str, component: dict, keep: str):
    for n in os.listdir(root):
        if n != keep and re.match(component['asset'], n, re.I):
            os.remove(os.path.join(root, n))


# --------------------------------------------------------------------------- firmware

@dataclass
class PupInfo:
    version: str
    size: int
    md5: str = ''


def read_pup(path: str) -> PupInfo:
    """Validate a PS3 firmware update file and return its version."""
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        hdr = f.read(0x30)
        if len(hdr) < 0x30 or hdr[:8] != b'SCEUF\x00\x00\x00':
            raise KitError('Not a PS3 firmware update file (.PUP)')
        _magic, _pkg, _img, count, hlen, dlen = struct.unpack('>8sQQQQQ', hdr)
        if count == 0 or count > 64 or hlen + dlen != size:
            raise KitError('Firmware file is damaged or incomplete (size does not match its header)')
        version = ''
        for i in range(count):
            f.seek(0x30 + i * 0x20)
            entry_id, off, length, _pad = struct.unpack('>QQQQ', f.read(0x20))
            if entry_id == 0x100 and length < 64:
                f.seek(off)
                version = f.read(length).decode('ascii', 'replace').strip()
        if not version:
            raise KitError('Could not read the firmware version from this file')
    return PupInfo(version, size)


def drive_pup(root: str) -> PupInfo | None:
    p = os.path.join(root, PUP_REL)
    if not os.path.isfile(p):
        return None
    try:
        return read_pup(p)
    except (KitError, OSError):
        return PupInfo('invalid', os.path.getsize(p))


def install_pup(src: str, root: str, on_progress, cancel) -> PupInfo:
    info = read_pup(src)
    dest = os.path.join(root, PUP_REL)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if shutil.disk_usage(root).free < info.size + 16 * 1024 * 1024:
        raise KitError('Not enough free space on the drive for the firmware file')
    tmp = dest + '.part'
    prog = Progress(info.size * 2, on_progress, cancel)
    src_md5, dst_md5 = hashlib.md5(), hashlib.md5()
    try:
        with open(src, 'rb') as fi, open(tmp, 'wb') as fo:
            while buf := fi.read(CHUNK):
                fo.write(buf)
                src_md5.update(buf)
                prog.add(len(buf))
        with open(tmp, 'rb') as fv:  # read back to make sure the USB copy is good
            while buf := fv.read(CHUNK):
                dst_md5.update(buf)
                prog.add(len(buf))
        if src_md5.digest() != dst_md5.digest():
            raise KitError('Verification failed: the copy on the USB drive does not match. Try another drive.')
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    info.md5 = src_md5.hexdigest()
    return info


def file_md5(path: str, on_progress, cancel) -> str:
    prog = Progress(os.path.getsize(path), on_progress, cancel)
    h = hashlib.md5()
    with open(path, 'rb') as f:
        while buf := f.read(CHUNK):
            h.update(buf)
            prog.add(len(buf))
    return h.hexdigest()


# --------------------------------------------------------------------------- guide

def guide_text(fw_kind: str, pup: PupInfo | None, pkgs: dict) -> str:
    fw = f'{fw_kind} {pup.version}' if pup else f'{fw_kind} (not on the drive yet)'
    pkg_lines = '\n'.join(f'      - {n}' for n in pkgs.values()) or '      (none on the drive yet)'
    steps = [
        'PS3 MOD GUIDE  (written by PS3 Drive Manager)',
        '=' * 60,
        f'Firmware on this USB: {fw}',
        'Packages on this USB:',
        pkg_lines,
        '',
        'BEFORE YOU START',
        '  - Only use the firmware file if it is the SAME or a NEWER version than the',
        '    PS3 is on now (Settings > System Settings > System Information).',
        '  - Keep the PS3 plugged in and do not turn it off during the update.',
        '  - Do NOT sign in to PSN while modded.',
        '',
    ]
    if fw_kind == 'CFW':
        steps += [
            'CFW INSTALL (compatible consoles ONLY)',
            '  1. Confirm with minver checker that your console minimum version is 3.56 or lower.',
            '  2. Dump your NOR/NAND flash and verify it on a PC (PyPS3checker). Keep that file safe.',
            '  3. Follow the CFW guide for your model to patch the flash, then install this',
            '     PS3UPDAT.PUP from Recovery Mode or System Update.',
            f'     Guide: {LINKS["cfw_guide"]}',
            '  4. Continue at "INSTALL PACKAGES" below.',
        ]
    else:
        steps += [
            'STEP 1 - Install HFW (Hybrid Firmware)',
            '  1. Plug this USB into the PS3 port closest to the disc drive.',
            '  2. XMB > Settings > System Update > Update via Storage Media.',
            '  3. Let it finish and restart.',
            '',
            'STEP 2 - Install PS3HEN',
            '  1. Set the PS3 date/time correctly and connect to the internet.',
            '  2. Open the PS3 web browser, go to the PS3HEN installer page from the guide',
            '     below, and choose "Install HEN". The console reboots when done.',
            f'     Guide: {LINKS["hen_guide"]}',
            '  3. After each reboot, enable HEN from the PS3HEN Enabler icon in the XMB,',
            '     or turn on HEN auto-enable in the HEN settings so it starts by itself.',
        ]
    steps += [
        '',
        'INSTALL PACKAGES (needs HEN enabled, or CFW)',
        '  1. Plug this USB in.',
        '  2. XMB > Game > Package Manager > Install Package Files > Standard.',
        '  3. Install each package. For webMAN MOD, hold L1 while it installs for the',
        '     full edition. Reboot (and re-enable HEN) afterwards.',
        '',
        'PLAY YOUR BACKUPS',
        '  - Games in PS3ISO / PS2ISO / PSXISO / GAMES on this drive appear under',
        '    Game > webMAN Games. On NTFS drives, run prepISO first.',
        '  - Use IRISMAN to copy your own game discs to the internal HDD.',
        '',
    ]
    return '\n'.join(steps)


def write_guide(root: str, fw_kind: str) -> str:
    path = os.path.join(root, GUIDE_NAME)
    with open(path, 'w', encoding='utf-8', newline='\r\n') as f:
        f.write(guide_text(fw_kind, drive_pup(root), installed_packages(root)))
    return path
