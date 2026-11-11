"""Scrollable cover-art grid for the game library (a Canvas of tiles, thumbnails loaded lazily)."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

TILE_W, TILE_H = 132, 186      # cover image box
TEXT_H = 42                    # title + subtitle under the cover
GAP = 14
PAD = 12


class CoverGrid(tk.Frame):
    def __init__(self, parent, colors: dict, load_thumb, on_click, on_context, on_double=None,
                 tile=(TILE_W, TILE_H), empty_text='No cover'):
        super().__init__(parent, bg=colors['panel'])
        self.c = colors
        self.tw, self.th = tile
        self.empty_text = empty_text
        self.load_thumb = load_thumb      # (path, w, h) -> PhotoImage
        self.on_click = on_click          # (iid, mode) where mode in 'set', 'toggle', 'range'
        self.on_context = on_context      # (iid, event)
        self.on_double = on_double
        self.canvas = tk.Canvas(self, bg=colors['panel'], highlightthickness=0, bd=0, yscrollincrement=24)
        sb = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.items = []                   # (iid, title, subtitle, cover_path, color)
        self.selected: set[str] = set()
        self.tiles = {}                   # iid -> dict(rect=..., img=...)
        self.thumbs = {}                  # (path, mtime) -> PhotoImage
        self._cols = 0
        self._queue = []
        self._load_job = None
        self.canvas.bind('<Configure>', self._on_resize)
        self.canvas.bind('<Button-1>', lambda e: self._click(e, 'set'))
        self.canvas.bind('<Control-Button-1>', lambda e: self._click(e, 'toggle'))
        self.canvas.bind('<Shift-Button-1>', lambda e: self._click(e, 'range'))
        self.canvas.bind('<Double-Button-1>', self._double)
        self.canvas.bind('<Button-3>', self._context)
        self.canvas.bind('<Enter>', lambda e: self.canvas.bind_all('<MouseWheel>', self._wheel))
        self.canvas.bind('<Leave>', lambda e: self.canvas.unbind_all('<MouseWheel>'))

    # ------------------------------------------------------------------ public
    def set_items(self, items):
        self.items = items
        self._layout(force=True)

    def set_selection(self, iids):
        self.selected = set(iids)
        for iid, t in self.tiles.items():
            self.canvas.itemconfigure(t['rect'], outline=self.c['accent'] if iid in self.selected else self.c['field'],
                                      width=3 if iid in self.selected else 1)

    def see(self, iid):
        t = self.tiles.get(iid)
        if not t:
            return
        _x0, y0, _x1, y1 = self.canvas.bbox(t['rect'])
        region = self.canvas.cget('scrollregion').split()
        total = max(1.0, float(region[3])) if len(region) == 4 else 1.0
        top, bottom = (f * total for f in self.canvas.yview())
        if y0 < top or y1 > bottom:
            self.canvas.yview_moveto(max(0, y0 - PAD) / total)

    def forget_thumbs(self, path=None):
        if path is None:
            self.thumbs.clear()
        else:
            for k in [k for k in self.thumbs if k[0] == path]:
                del self.thumbs[k]

    # ------------------------------------------------------------------ layout
    def _on_resize(self, _e):
        self._layout()

    def _layout(self, force=False):
        tw, th = self.tw, self.th
        width = max(self.canvas.winfo_width(), tw + 2 * PAD)
        cols = max(1, (width - 2 * PAD + GAP) // (tw + GAP))
        if not force and cols == self._cols:
            return
        self._cols = cols
        self.canvas.delete('all')
        self.tiles.clear()
        self._queue.clear()
        used = cols * tw + (cols - 1) * GAP
        left = max(PAD, (width - used) // 2)
        row_h = th + TEXT_H + GAP
        for n, (iid, title, sub, cover, color) in enumerate(self.items):
            r, col = divmod(n, cols)
            x, y = left + col * (tw + GAP), PAD + r * row_h
            rect = self.canvas.create_rectangle(x - 2, y - 2, x + tw + 2, y + th + 2,
                                                fill=self.c['field'], outline=self.c['field'], tags=(f't{iid}',))
            img = self.canvas.create_image(x + tw // 2, y + th // 2, tags=(f't{iid}',))
            if not cover:
                self.canvas.create_text(x + tw // 2, y + th // 2, text=self.empty_text, fill=self.c['muted'],
                                        font=('Segoe UI', 9), tags=(f't{iid}',))
            self.canvas.create_text(x, y + th + 8, text=self._fit(title, tw), anchor='nw',
                                    fill=self.c['fg'], font=('Segoe UI Semibold', 9), tags=(f't{iid}',))
            self.canvas.create_text(x, y + th + 25, text=self._fit(sub, tw), anchor='nw', fill=color,
                                    font=('Segoe UI', 8), tags=(f't{iid}',))
            self.tiles[iid] = {'rect': rect, 'img': img, 'cover': cover}
            if cover:
                self._queue.append(iid)
        rows = -(-len(self.items) // cols) if self.items else 0
        self.canvas.configure(scrollregion=(0, 0, width, PAD * 2 + rows * row_h))
        self.set_selection(self.selected)
        if self._load_job is None:
            self._load_job = self.after(1, self._load_some)

    def append_items(self, items):
        self.items = self.items + list(items)
        self._layout(force=True)

    def _fit(self, text, px):
        max_chars = px // 7
        return text if len(text) <= max_chars else text[:max_chars - 1] + '…'

    def _load_some(self):
        self._load_job = None
        for _ in range(8):
            if not self._queue:
                return
            iid = self._queue.pop(0)
            t = self.tiles.get(iid)
            if not t:
                continue
            path = t['cover']
            try:
                key = (path, os.path.getmtime(path))
                if key not in self.thumbs:
                    self.thumbs[key] = self.load_thumb(path, self.tw, self.th)
                self.canvas.itemconfigure(t['img'], image=self.thumbs[key])
            except Exception:  # noqa: BLE001 - unreadable image: leave the tile blank
                pass
        self._load_job = self.after(1, self._load_some)

    # ------------------------------------------------------------------ events
    def _hit(self, e):
        x, y = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        for item in self.canvas.find_overlapping(x, y, x, y):
            for tag in self.canvas.gettags(item):
                if tag.startswith('t'):
                    return tag[1:]
        return None

    def _click(self, e, mode):
        self.canvas.focus_set()
        iid = self._hit(e)
        if iid is not None:
            self.on_click(iid, mode)
        return 'break'

    def _double(self, e):
        iid = self._hit(e)
        if iid is not None and self.on_double:
            self.on_double(iid)

    def _context(self, e):
        iid = self._hit(e)
        if iid is not None:
            if iid not in self.selected:
                self.on_click(iid, 'set')
            self.on_context(iid, e)

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120) * 2, 'units')
