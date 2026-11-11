"""Talk to webMAN MOD on the PS3: HTTP commands and FTP uploads to the internal HDD."""
from __future__ import annotations

import ftplib
import os
import urllib.parse
import urllib.request

from discinfo import GameInfo
from library import ALL_DIRS, CHUNK, Progress, cover_base, dest_dir_for, find_cover, SPLIT_RE

REMOTE_ROOT = '/dev_hdd0'


class ConcatReader:
    """File-like object that reads several files back to back (used to rejoin split ISOs)."""

    def __init__(self, paths):
        self.paths = list(paths)
        self.f = None

    def read(self, n=-1):
        while True:
            if self.f is None:
                if not self.paths:
                    return b''
                self.f = open(self.paths.pop(0), 'rb')
            data = self.f.read(n)
            if data:
                return data
            self.f.close()
            self.f = None

    def close(self):
        if self.f:
            self.f.close()


class PS3Client:
    def __init__(self, ip: str):
        self.ip = ip.strip()
        if not self.ip:
            raise ValueError('Enter your PS3\'s IP address first (XMB > Settings > Network Settings > Settings and Connection Status List)')

    # ---------------------------------------------------------------- HTTP
    def http(self, path: str, timeout: float = 6) -> str:
        url = f'http://{self.ip}{path}'
        req = urllib.request.Request(url, headers={'User-Agent': 'PS3DriveManager/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode('utf-8', 'replace')

    def test(self) -> bool:
        html = self.http('/')
        return 'webman' in html.lower() or 'wmm' in html.lower() or bool(html)

    def refresh(self):
        self.http('/refresh.ps3', timeout=60)

    def popup(self, message: str):
        self.http('/popup.ps3?' + urllib.parse.quote(message))

    # ---------------------------------------------------------------- FTP
    def _ftp(self) -> ftplib.FTP:
        ftp = ftplib.FTP()
        ftp.connect(self.ip, 21, timeout=30)
        ftp.login()  # webMAN accepts anonymous login
        ftp.set_pasv(True)
        return ftp

    @staticmethod
    def _ensure_dir(ftp: ftplib.FTP, path: str):
        cur = ''
        for part in path.strip('/').split('/'):
            cur += '/' + part
            try:
                ftp.mkd(cur)
            except ftplib.error_perm:
                pass

    @staticmethod
    def _names(ftp: ftplib.FTP, path: str) -> list[str]:
        try:
            return [os.path.basename(n.rstrip('/')) for n in ftp.nlst(path)]
        except ftplib.error_perm:
            return []

    def list_games(self) -> dict:
        ftp = self._ftp()
        try:
            out = {d: sorted((n for n in self._names(ftp, f'{REMOTE_ROOT}/{d}') if n not in ('.', '..')),
                             key=str.lower)
                   for d in ALL_DIRS if d != 'ROMS'}
            for sub in self._names(ftp, f'{REMOTE_ROOT}/ROMS'):
                if sub not in ('.', '..'):
                    out[f'ROMS/{sub}'] = sorted((n for n in self._names(ftp, f'{REMOTE_ROOT}/ROMS/{sub}')
                                                 if n not in ('.', '..')), key=str.lower)
            return out
        finally:
            ftp.quit()

    def upload_game(self, info: GameInfo, on_progress, cancel, log) -> bool:
        """Upload one game from the local drive to /dev_hdd0. Returns False if it was already there."""
        remote_dir = f'{REMOTE_ROOT}/{dest_dir_for(info)}'
        jobs = []  # (list of local source files, remote path)
        if info.fmt == 'Folder':
            remote_game = f'{remote_dir}/{os.path.basename(info.path)}'
            check_name = os.path.basename(info.path)
            for f in info.files:
                rel = os.path.relpath(f, info.path).replace('\\', '/')
                jobs.append(([f], f'{remote_game}/{rel}'))
        elif info.fmt == 'Split ISO':
            base = os.path.basename(info.path[:SPLIT_RE.search(info.path).start()])
            check_name = base + '.iso'
            jobs.append((info.files, f'{remote_dir}/{check_name}'))  # rejoined on the PS3
        else:
            check_name = os.path.basename(info.path)
            for f in info.files:
                jobs.append(([f], f'{remote_dir}/{os.path.basename(f)}'))
        cover = find_cover(info)
        if cover and cover_base(info):
            name = (check_name.rsplit('.', 1)[0] if '.' in check_name else check_name)
            jobs.append(([cover], f'{remote_dir}/{name}{os.path.splitext(cover)[1]}'))

        total = sum(os.path.getsize(f) for srcs, _ in jobs for f in srcs)
        prog = Progress(total, on_progress, cancel)
        ftp = self._ftp()
        current = None
        try:
            self._ensure_dir(ftp, remote_dir)
            if check_name in self._names(ftp, remote_dir):
                log(f'  Already on the PS3: {check_name}')
                return False
            made = set()
            for srcs, remote in jobs:
                parent = remote.rsplit('/', 1)[0]
                if parent not in made:
                    self._ensure_dir(ftp, parent)
                    made.add(parent)
                current = remote
                reader = ConcatReader(srcs)
                try:
                    ftp.storbinary(f'STOR {remote}', reader, blocksize=CHUNK // 4,
                                   callback=lambda block: prog.add(len(block)))
                finally:
                    reader.close()
            current = None
            return True
        finally:
            if current:  # interrupted mid-file: remove the partial upload
                try:
                    ftp.close()
                    ftp = self._ftp()
                    ftp.delete(current)
                except Exception:
                    pass
            try:
                ftp.quit()
            except Exception:
                ftp.close()
