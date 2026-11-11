"""Browse and download PS3 themes from Themezer (themezer.net), a community theme site with a public API."""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from library import CHUNK, Progress

API = 'https://api.themezer.net/graphql'
USER_AGENT = 'PS3DriveManager/1.0'
SITE = 'https://themezer.net/ps3/themes'

SORTS = {'Most downloaded': 'Downloads', 'Trending': 'Trending', 'Newest': 'Created',
         'Most saved': 'Saves', 'Recently updated': 'Updated'}

QUERY = '''query($p: PaginationInput, $sort: ItemSort, $q: String, $dyn: Boolean, $icons: Boolean, $sfx: Boolean) {
  ps3 { themes(paginationArgs: $p, sort: $sort, order: Desc, isNSFW: false, query: $q,
               isDynamic: $dyn, hasCustomIcons: $icons, hasCustomSfx: $sfx) {
    pageInfo { itemCount page pageCount }
    nodes { hexId name description downloadCount saveCount isDynamic hasCustomIcons hasCustomSfx
            hasCustomBackground downloadUrl pageUrl creator { username } tags { name }
            preview { thumbUrl hdUrl } }
  } } }'''


class StoreError(Exception):
    pass


@dataclass
class ThemeItem:
    hex_id: str
    name: str
    description: str
    creator: str
    downloads: int
    saves: int
    dynamic: bool
    icons: bool
    sfx: bool
    tags: list = field(default_factory=list)
    thumb_url: str = ''
    hd_url: str = ''
    download_url: str = ''
    page_url: str = ''
    thumb_path: str | None = None

    @property
    def features(self) -> str:
        f = [n for n, on in (('Animated', self.dynamic), ('Custom icons', self.icons), ('Custom sounds', self.sfx)) if on]
        return ', '.join(f) or 'Wallpaper and colors'

    @property
    def clean_description(self) -> str:
        text = re.sub(r'\n?Originally published on ps3-themes\.com\s*$', '', self.description or '').strip()
        return re.sub(r'\n{3,}', '\n\n', text)


def _request(url, data=None, timeout=30):
    headers = {'User-Agent': USER_AGENT}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=timeout)


def search(query='', sort='Downloads', page=1, limit=24, dynamic=False, icons=False, sfx=False):
    """Return (list of ThemeItem, total count, page count)."""
    variables = {'p': {'page': page, 'limit': limit}, 'sort': sort, 'q': query.strip() or None,
                 'dyn': True if dynamic else None, 'icons': True if icons else None, 'sfx': True if sfx else None}
    if variables['q'] and sort != 'Relevance':
        variables['sort'] = 'Relevance'
    body = json.dumps({'query': QUERY, 'variables': variables}).encode()
    try:
        with _request(API, body) as r:
            res = json.load(r)
    except Exception as e:  # noqa: BLE001
        raise StoreError(f'Could not reach Themezer: {e}') from e
    if res.get('errors'):
        raise StoreError(res['errors'][0].get('message', 'Themezer returned an error'))
    data = res['data']['ps3']['themes']
    items = []
    for n in data['nodes']:
        items.append(ThemeItem(
            hex_id=n['hexId'], name=(n['name'] or 'Untitled').strip(), description=n.get('description') or '',
            creator=(n.get('creator') or {}).get('username') or 'unknown', downloads=n.get('downloadCount') or 0,
            saves=n.get('saveCount') or 0, dynamic=bool(n.get('isDynamic')), icons=bool(n.get('hasCustomIcons')),
            sfx=bool(n.get('hasCustomSfx')), tags=[t['name'] for t in n.get('tags') or []],
            thumb_url=(n.get('preview') or {}).get('thumbUrl') or '', hd_url=(n.get('preview') or {}).get('hdUrl') or '',
            download_url=n.get('downloadUrl') or '', page_url=n.get('pageUrl') or f'{SITE}/{n["hexId"]}'))
    info = data['pageInfo']
    return items, info.get('itemCount', 0), info.get('pageCount', 1)


def _cache_path(cache_dir, url, kind):
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f'{kind}_{hashlib.sha1(url.encode()).hexdigest()[:20]}.webp')


def fetch_image(url, cache_dir, kind='thumb') -> str | None:
    if not url:
        return None
    path = _cache_path(cache_dir, url, kind)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    try:
        with _request(url, timeout=20) as r:
            data = r.read(10 * 1024 * 1024)
    except Exception:  # noqa: BLE001 - a missing preview is not fatal
        return None
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)
    return path


def fetch_thumbs(items, cache_dir):
    with ThreadPoolExecutor(max_workers=8) as pool:
        for item, path in zip(items, pool.map(lambda i: fetch_image(i.thumb_url, cache_dir), items)):
            item.thumb_path = path


def download_theme(item: ThemeItem, dest: str, on_progress, cancel) -> str:
    """Download the .p3t to dest (a full file path), verifying it really is a PS3 theme."""
    tmp = dest + '.part'
    try:
        with _request(item.download_url, timeout=60) as r:
            total = int(r.headers.get('Content-Length') or 0)
            prog = Progress(total or 1, on_progress, cancel)
            first = True
            with open(tmp, 'wb') as f:
                while True:
                    buf = r.read(CHUNK // 4)
                    if not buf:
                        break
                    if first and buf[:4] != b'P3TF':
                        raise StoreError('The download is not a PS3 theme file')
                    first = False
                    f.write(buf)
                    prog.add(len(buf))
        if total and os.path.getsize(tmp) != total:
            raise StoreError('Download incomplete')
        os.replace(tmp, dest)
        return dest
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
