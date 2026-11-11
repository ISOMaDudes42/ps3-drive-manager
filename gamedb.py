"""Official PS1/PS2 titles and PS2-on-PS3 compatibility hints, cached locally.

Sources (downloaded once, refreshed every few weeks):
  - PS2 titles: PCSX2 GameIndex.yaml
  - PS1 titles: DuckStation gamedb.yaml
  - PS2 compatibility configs: the PS2CONFIG folder of the webMAN MOD repository
    (which games webMAN has emulator fixes for, per PS3 emulator type)
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request

USER_AGENT = 'PS3DriveManager/1.0'
MAX_AGE = 21 * 24 * 3600
COMPAT_LIST_URL = 'https://www.psdevwiki.com/ps3/PS2_Classics_Emulator_Compatibility_List'

SOURCES = {
    'ps2': 'https://raw.githubusercontent.com/PCSX2/pcsx2/master/bin/resources/GameIndex.yaml',
    'ps1': 'https://raw.githubusercontent.com/stenzek/duckstation/master/data/resources/gamedb.yaml',
    'ps2cfg': 'https://api.github.com/repos/aldostools/webMAN-MOD/git/trees/master?recursive=1',
}

NAME_RE = re.compile(r'^([A-Z]{4}-\d{5}):[ \t]*\r?\n[ \t]+name:[ \t]*(?:"((?:[^"\\]|\\.)*)"|(.+?))[ \t]*\r?$', re.M)
CFG_RE = re.compile(r'PS2CONFIG/USRDIR/CONFIG/(NET|ENC|GX|SOFT|CUSTOM)/([A-Z]{4})_(\d{3})\.(\d{2})\.(?:CONFIG|ENC)$', re.I)

PS3_MODELS = {
    'none': 'No PS2 hardware (most models)',
    'semi': 'Partial PS2 hardware (CECH-C/E)',
    'bc': 'Full PS2 hardware (CECH-A/B)',
}


def _get(url: str, accept: str | None = None) -> bytes:
    headers = {'User-Agent': USER_AGENT}
    if accept:
        headers['Accept'] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        return r.read()


def parse_names(text: str) -> dict:
    out = {}
    for m in NAME_RE.finditer(text):
        name = m.group(2) if m.group(2) is not None else m.group(3)
        name = name.replace('\\"', '"').strip()
        if name:
            out.setdefault(m.group(1), name)
    return out


def parse_configs(tree_json: bytes) -> dict:
    out: dict[str, set] = {}
    for entry in json.loads(tree_json).get('tree', []):
        m = CFG_RE.search(entry.get('path', ''))
        if m:
            kind = m.group(1).upper()
            kind = 'NET' if kind == 'ENC' else kind
            out.setdefault(f'{m.group(2).upper()}-{m.group(3)}{m.group(4)}', set()).add(kind)
    return {k: sorted(v) for k, v in out.items()}


class GameDB:
    def __init__(self, cache_dir: str):
        self.path = os.path.join(cache_dir, 'gamedb.json')
        self.data = {'ps1': {}, 'ps2': {}, 'ps2cfg': {}, 'updated': 0}
        self.lock = threading.Lock()
        try:
            with open(self.path, encoding='utf-8') as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    @property
    def ready(self) -> bool:
        return bool(self.data['ps2'] or self.data['ps1'])

    def stale(self) -> bool:
        return not self.ready or time.time() - self.data.get('updated', 0) > MAX_AGE

    def refresh(self, log=lambda *_: None) -> str:
        new = dict(self.data)
        results = []
        for key, label in (('ps2', 'PS2 titles'), ('ps1', 'PS1 titles'), ('ps2cfg', 'PS2 compatibility configs')):
            try:
                if key == 'ps2cfg':
                    new[key] = parse_configs(_get(SOURCES[key], 'application/vnd.github+json'))
                else:
                    new[key] = parse_names(_get(SOURCES[key]).decode('utf-8', 'replace'))
                results.append(f'{len(new[key])} {label}')
            except Exception as e:  # noqa: BLE001 - keep whatever we had before
                log(f'  Could not update {label}: {e}', 'warn')
        new['updated'] = time.time()
        with self.lock:
            self.data = new
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(new, f)
        os.replace(tmp, self.path)
        return ', '.join(results)

    # ------------------------------------------------------------------ lookups
    def title(self, system: str, game_id: str) -> str:
        if not game_id:
            return ''
        return self.data.get(system.lower(), {}).get(game_id.upper(), '') if system in ('PS1', 'PS2') else ''

    def ps2_hint(self, game_id: str, model: str):
        """Return (short note for the list, longer explanation) for a PS2 game on this PS3 model."""
        cfg = set(self.data.get('ps2cfg', {}).get((game_id or '').upper(), []))
        if model == 'bc':
            return '', 'Full PS2 hardware: runs almost perfectly.'
        if not self.data.get('ps2cfg'):
            return '', 'PS2 compatibility data not downloaded yet.'
        fix_text = 'webMAN has a fix for this game. Install "PS2 configs" (Mod kit tab).'
        if model == 'semi':
            if 'GX' in cfg:
                return 'PS2 fix available', fix_text
            return '', 'No fix listed; most games run well on this model.'
        if cfg & {'NET', 'SOFT', 'CUSTOM'}:
            return 'PS2 fix available', fix_text
        return 'No known PS2 fix', 'No fix listed. Many games still run; check online.'
