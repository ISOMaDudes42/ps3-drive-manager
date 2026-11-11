"""PS3 Drive Manager: prepare USB drives for webMAN MOD and manage PS3 / PS2 / PS1 backups."""
from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except ImportError:  # running from source without Pillow: PNG-only previews
    Image = ImageTk = None

try:  # drag and drop from Explorer
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _TkBase = TkinterDnD.Tk
except Exception:  # noqa: BLE001 - optional
    DND_FILES, TkinterDnD = None, None
    _TkBase = tk.Tk

import covers
import discinfo
import gamedb
import library
import modkit
import ps3net
import retro
import theme_store
import themes
from gridview import CoverGrid
from library import fmt_size

APP_NAME = 'PS3 Drive Manager'
SETTINGS_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'PS3DriveManager')
SETTINGS_PATH = os.path.join(SETTINGS_DIR, 'settings.json')
DEFAULT_SETTINGS = {
    'ps3_ip': '',
    'auto_covers': True,
    'official_names': True,
    'ps3_model': 'none',
    'view': 'grid',
    'last_drive': '',
    'fw_kind': 'HFW',
    'cover_templates': covers.DEFAULT_TEMPLATES,
}

C = dict(bg='#14161a', panel='#1b1e24', field='#242931', hover='#2d333d', fg='#e6e8eb',
         muted='#8b93a1', accent='#3b82f6', accent_hi='#2563eb', warn='#f59e0b',
         err='#f87171', ok='#4ade80', border='#2c313a', sel='#1e3a66')

SYSTEM_COLORS = {'PS3': '#60a5fa', 'PS2': '#a78bfa', 'PS1': '#9ca3af', 'RETRO': '#f472b6', '?': '#f87171'}
FILTERS = {'All': None, 'PS3': 'PS3', 'PS2': 'PS2', 'PS1': 'PS1', 'Retro': 'RETRO'}


def load_settings() -> dict:
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s: dict):
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
            json.dump(s, f, indent=2)
    except OSError:
        pass


def system_label(g) -> str:
    return g.platform if g.system == 'RETRO' else g.system


class App(_TkBase):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry(f'1180x{min(860, self.winfo_screenheight() - 160)}+40+20')
        self.minsize(900, 600)
        self.configure(bg=C['bg'])
        self.settings = load_settings()
        self.q: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self._pending_rescan = False
        self.drives: list[library.DriveInfo] = []
        self.drive: library.DriveInfo | None = None
        self.games: list[discinfo.GameInfo] = []
        self.db = gamedb.GameDB(os.path.join(SETTINGS_DIR, 'cache'))

        self._style()
        self._build()
        self._setup_dnd()
        self.refresh_drives()
        self.after(100, self._poll)
        self.after(800, self._maybe_update_db)
        self.protocol('WM_DELETE_WINDOW', self._on_close)

    # ================================================================== game database
    def _maybe_update_db(self, force=False):
        if not (force or self.db.stale()):
            return

        def work():
            self.log('Updating game databases (titles and PS2 compatibility)...', 'muted')
            summary = self.db.refresh(self.log)
            self.log(f'Game databases updated: {summary}.', 'ok')
            self.post('db')
        threading.Thread(target=work, daemon=True).start()

    # ================================================================== style
    def _style(self):
        st = ttk.Style(self)
        st.theme_use('clam')
        font = ('Segoe UI', 10)
        st.configure('.', background=C['bg'], foreground=C['fg'], fieldbackground=C['field'],
                     bordercolor=C['border'], lightcolor=C['border'], darkcolor=C['border'],
                     troughcolor=C['field'], font=font, focuscolor=C['accent'])
        st.configure('TFrame', background=C['bg'])
        st.configure('Panel.TFrame', background=C['panel'])
        st.configure('TLabel', background=C['bg'], foreground=C['fg'])
        st.configure('Panel.TLabel', background=C['panel'])
        st.configure('Muted.TLabel', foreground=C['muted'])
        st.configure('PanelMuted.TLabel', background=C['panel'], foreground=C['muted'])
        st.configure('Title.TLabel', font=('Segoe UI Semibold', 16))
        st.configure('TButton', background=C['field'], foreground=C['fg'], padding=(12, 6),
                     borderwidth=0, relief='flat')
        st.map('TButton', background=[('disabled', C['panel']), ('pressed', C['border']),
                                      ('active', C['hover'])],
               foreground=[('disabled', C['muted'])])
        st.configure('Small.TButton', padding=(4, 6))
        st.configure('Accent.TButton', background=C['accent'], foreground='white')
        st.map('Accent.TButton', background=[('disabled', C['panel']), ('pressed', C['accent_hi']),
                                             ('active', C['accent_hi'])])
        st.configure('TCheckbutton', background=C['panel'], foreground=C['fg'])
        st.map('TCheckbutton', background=[('active', C['panel'])])
        st.configure('TRadiobutton', background=C['panel'], foreground=C['fg'])
        st.map('TRadiobutton', background=[('active', C['panel'])])
        st.configure('Section.TLabel', background=C['panel'], font=('Segoe UI Semibold', 11))
        st.configure('Bold.TCheckbutton', font=('Segoe UI Semibold', 10))
        st.configure('TEntry', fieldbackground=C['field'], foreground=C['fg'], insertcolor=C['fg'],
                     padding=5)
        st.configure('TCombobox', fieldbackground=C['field'], background=C['field'],
                     foreground=C['fg'], arrowcolor=C['fg'], padding=5)
        st.map('TCombobox', fieldbackground=[('readonly', C['field'])],
               foreground=[('readonly', C['fg'])], selectbackground=[('readonly', C['field'])],
               selectforeground=[('readonly', C['fg'])])
        self.option_add('*TCombobox*Listbox.background', C['field'])
        self.option_add('*TCombobox*Listbox.foreground', C['fg'])
        self.option_add('*TCombobox*Listbox.selectBackground', C['sel'])
        st.configure('TNotebook', background=C['bg'], borderwidth=0, tabmargins=(0, 0, 0, 0))
        st.configure('TNotebook.Tab', background=C['bg'], foreground=C['muted'], padding=(18, 8),
                     borderwidth=0)
        st.map('TNotebook.Tab', background=[('selected', C['panel'])],
               foreground=[('selected', C['fg'])])
        st.configure('Treeview', background=C['panel'], fieldbackground=C['panel'],
                     foreground=C['fg'], rowheight=28, borderwidth=0)
        st.map('Treeview', background=[('selected', C['sel'])], foreground=[('selected', C['fg'])])
        st.configure('Treeview.Heading', background=C['bg'], foreground=C['muted'],
                     relief='flat', padding=(6, 6), font=('Segoe UI Semibold', 9))
        st.map('Treeview.Heading', background=[('active', C['bg'])])
        st.configure('Horizontal.TProgressbar', background=C['accent'], troughcolor=C['field'],
                     borderwidth=0, thickness=8)
        st.configure('Vertical.TScrollbar', background=C['field'], troughcolor=C['panel'],
                     arrowcolor=C['muted'], borderwidth=0)

    # ================================================================== layout
    def _build(self):
        outer = ttk.Frame(self, padding=(16, 14, 16, 12))
        outer.pack(fill='both', expand=True)

        drive_row = ttk.Frame(outer)
        drive_row.pack(fill='x', pady=(0, 4))
        ttk.Label(drive_row, text=APP_NAME, style='Title.TLabel').pack(side='left', padx=(0, 28))
        ttk.Label(drive_row, text='Drive').pack(side='left', padx=(0, 8))
        self.drive_var = tk.StringVar()
        self.drive_box = ttk.Combobox(drive_row, textvariable=self.drive_var, state='readonly', width=62)
        self.drive_box.pack(side='left')
        self.drive_box.bind('<<ComboboxSelected>>', lambda e: self.on_drive_selected())
        ttk.Button(drive_row, text='Refresh', command=self.refresh_drives).pack(side='left', padx=6)
        self.prep_btn = ttk.Button(drive_row, text='Prepare drive', command=self.on_prepare)
        self.prep_btn.pack(side='left')

        self.drive_note = tk.Label(outer, text='', anchor='w', justify='left', bg=C['bg'],
                                   fg=C['muted'], font=('Segoe UI', 9))
        self.drive_note.pack(fill='x', pady=(0, 10))

        # progress + log are packed from the bottom first so they always stay visible;
        # the notebook gets whatever height is left.
        self.log_box = tk.Text(outer, height=4, bg=C['panel'], fg=C['fg'], relief='flat',
                               font=('Consolas', 9), wrap='word', padx=8, pady=6,
                               highlightthickness=0, state='disabled')
        self.log_box.pack(side='bottom', fill='x')
        for tag, col in (('warn', C['warn']), ('err', C['err']), ('ok', C['ok']), ('muted', C['muted'])):
            self.log_box.tag_configure(tag, foreground=col)
        self.progress = ttk.Progressbar(outer, mode='determinate', maximum=1000)
        self.progress.pack(side='bottom', fill='x', pady=(6, 6))
        bottom = ttk.Frame(outer)
        bottom.pack(side='bottom', fill='x', pady=(10, 0))
        self.status_var = tk.StringVar(value='Ready')
        ttk.Label(bottom, textvariable=self.status_var, style='Muted.TLabel').pack(side='left')
        self.cancel_btn = ttk.Button(bottom, text='Cancel', command=self.cancel.set, state='disabled')
        self.cancel_btn.pack(side='right')

        nb = self.notebook = ttk.Notebook(outer)
        nb.pack(fill='both', expand=True)
        nb.add(self._build_modkit(nb), text='Mod kit')
        nb.add(self._build_library(nb), text='Drive library')
        nb.add(self._build_themes(nb), text='Themes')
        nb.add(self._build_console(nb), text='Send to PS3')
        nb.bind('<<NotebookTabChanged>>', lambda e: self._maybe_load_store())

    def _build_modkit(self, parent):
        tab = ttk.Frame(parent, style='Panel.TFrame', padding=14)
        tab.columnconfigure(0, weight=1, uniform='col')
        tab.columnconfigure(1, weight=1, uniform='col')
        left = ttk.Frame(tab, style='Panel.TFrame')
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 18))
        right = ttk.Frame(tab, style='Panel.TFrame')
        right.grid(row=0, column=1, sticky='nsew')
        self.kit_buttons = []
        wrap = 500

        def section(parent, text, sub):
            ttk.Label(parent, text=text, style='Section.TLabel').pack(anchor='w')
            ttk.Label(parent, text=sub, style='PanelMuted.TLabel', wraplength=wrap,
                      justify='left').pack(anchor='w', pady=(0, 6))

        # --- 1. firmware (left)
        section(left, '1.  Firmware', 'HFW + PS3HEN works on every PS3 model. CFW is only for compatible '
                'consoles (minimum version 3.56 or lower). Download the firmware from the official page, then '
                'add it here. It is checked and copied to PS3\\UPDATE\\PS3UPDAT.PUP.')
        self.fw_kind = tk.StringVar(value=self.settings.get('fw_kind', 'HFW'))
        ttk.Radiobutton(left, text='HFW + HEN (any model, recommended)', value='HFW', variable=self.fw_kind,
                        command=self.on_fw_kind).pack(anchor='w')
        ttk.Radiobutton(left, text='CFW (advanced, compatible consoles only)', value='CFW',
                        variable=self.fw_kind, command=self.on_fw_kind).pack(anchor='w', pady=(2, 0))
        row = ttk.Frame(left, style='Panel.TFrame')
        row.pack(fill='x', pady=(8, 0))
        self.kit_buttons += [
            ttk.Button(row, text='Download guide', command=self.on_fw_guide),
            ttk.Button(row, text='Sony firmware page',
                       command=lambda: webbrowser.open(modkit.LINKS['sony_firmware'])),
            ttk.Button(row, text='Add firmware file...', style='Accent.TButton', command=self.on_add_firmware),
        ]
        for b in self.kit_buttons[-3:]:
            b.pack(side='left', padx=(0, 6))
        self.fw_status = tk.Label(left, text='', bg=C['panel'], fg=C['muted'], font=('Segoe UI', 10),
                                  anchor='w')
        self.fw_status.pack(fill='x', pady=(6, 0))

        # --- 3. build (left, under firmware)
        ttk.Frame(left, style='Panel.TFrame', height=22).pack()
        section(left, '3.  Build the USB', 'Downloads the checked packages and writes PS3_MOD_GUIDE.txt '
                '(step-by-step instructions) to the drive. Then plug the USB into the PS3 and follow the guide.')
        row = ttk.Frame(left, style='Panel.TFrame')
        row.pack(fill='x')
        self.kit_buttons += [
            ttk.Button(row, text='Build mod kit', style='Accent.TButton', command=self.on_build_kit),
            ttk.Button(row, text='Open guide', command=self.on_open_guide),
        ]
        for b in self.kit_buttons[-2:]:
            b.pack(side='left', padx=(0, 6))

        # --- 2. packages (right)
        section(right, '2.  Homebrew packages', 'Downloaded from the official GitHub releases, verified, and '
                'placed in the root of the drive so the PS3 Package Manager can install them.')
        self.pkg_vars, self.pkg_status = {}, {}
        for c in modkit.COMPONENTS:
            r = ttk.Frame(right, style='Panel.TFrame')
            r.pack(fill='x', pady=(4, 0))
            v = tk.BooleanVar(value=c['default'])
            self.pkg_vars[c['id']] = v
            top = ttk.Frame(r, style='Panel.TFrame')
            top.pack(fill='x')
            ttk.Checkbutton(top, text=c['name'], variable=v, style='Bold.TCheckbutton').pack(side='left')
            s = tk.Label(top, text='', bg=C['panel'], fg=C['muted'], font=('Segoe UI', 9))
            s.pack(side='right')
            self.pkg_status[c['id']] = s
            ttk.Label(r, text=c['desc'], style='PanelMuted.TLabel', wraplength=wrap,
                      justify='left').pack(anchor='w', padx=(22, 0))
        return tab

    def _kit_drive_ok(self) -> bool:
        if not self.drive:
            messagebox.showwarning(APP_NAME, 'Select your USB drive first.')
            return False
        if self.drive.kind == 'System':
            messagebox.showwarning(APP_NAME, 'That is your Windows system drive. Select your USB drive.')
            return False
        if not (self.drive.fs or '').upper().startswith('FAT'):
            messagebox.showwarning(APP_NAME, (
                f'This drive is {self.drive.fs or "an unknown format"}. The PS3 system menu can only read '
                'FAT32 USB drives for firmware updates and package installs.\n\n'
                'Use a FAT32 USB stick for the mod kit (it can be a different, small one). Windows only offers '
                'FAT32 for drives up to 32 GB; for larger drives use a FAT32 formatting tool.'))
            return False
        return True

    def refresh_kit(self):
        if not hasattr(self, 'fw_status'):
            return
        root = self.drive.root if self.drive else None
        pup = modkit.drive_pup(root) if root else None
        if pup is None:
            self.fw_status.configure(text='No firmware on this drive yet', fg=C['muted'])
        elif pup.version == 'invalid':
            self.fw_status.configure(text='PS3UPDAT.PUP on the drive is damaged; add it again', fg=C['err'])
        else:
            self.fw_status.configure(text=f'Ready: firmware {pup.version} on the drive', fg=C['ok'])
        have = modkit.installed_packages(root) if root else {}
        for cid, lbl in self.pkg_status.items():
            lbl.configure(text=f'on drive: {have[cid]}' if cid in have else '', fg=C['ok'])

    def on_fw_kind(self):
        if self.fw_kind.get() == 'CFW' and not messagebox.askyesno(APP_NAME, (
                'CFW is only for PS3s whose minimum firmware version is 3.56 or lower '
                '(check with minver checker under HEN first).\n\n'
                'Installing CFW on an incompatible console, or without a verified NOR/NAND dump, '
                'can permanently brick it.\n\nUse CFW mode?'), icon='warning'):
            self.fw_kind.set('HFW')
        self.settings['fw_kind'] = self.fw_kind.get()
        self._save_opts()

    def on_fw_guide(self):
        webbrowser.open(modkit.LINKS['cfw_guide' if self.fw_kind.get() == 'CFW' else 'hen_guide'])

    def on_add_firmware(self):
        if not self._kit_drive_ok():
            return
        path = filedialog.askopenfilename(title='Choose the firmware file you downloaded',
                                          filetypes=[('PS3 firmware', '*.PUP'), ('All files', '*.*')])
        if not path:
            return
        try:
            info = modkit.read_pup(path)
        except (modkit.KitError, OSError) as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        existing = modkit.drive_pup(self.drive.root)
        msg = f'Firmware version {info.version} ({fmt_size(info.size)}). Copy it to the drive as PS3UPDAT.PUP?'
        if existing:
            msg += f'\n\nThis replaces the firmware already on the drive ({existing.version}).'
        if not messagebox.askyesno(APP_NAME, msg):
            return
        root, kind = self.drive.root, self.fw_kind.get()

        def task():
            self.log(f'Copying firmware {info.version} to the drive...')
            done = modkit.install_pup(path, root, self._progress_cb('Firmware'), self.cancel)
            self.log(f'  Firmware {done.version} copied and verified.', 'ok')
            self.log(f'  MD5: {done.md5}. Compare this with the MD5 on the download page.', 'warn')
            modkit.write_guide(root, kind)
        self.run_task('Copying firmware...', task)

    def on_build_kit(self):
        if not self._kit_drive_ok():
            return
        comps = [c for c in modkit.COMPONENTS if self.pkg_vars[c['id']].get()]
        root, kind = self.drive.root, self.fw_kind.get()

        def task():
            cache, have = {}, modkit.installed_packages(root)
            for n, c in enumerate(comps, 1):
                self.log(f'[{n}/{len(comps)}] {c["name"]}: checking latest release...')
                asset = modkit.resolve(c, cache)
                if have.get(c['id']) == asset['name']:
                    self.log(f'  Up to date ({asset["name"]}).', 'ok')
                    continue
                if shutil.disk_usage(root).free < asset['size'] + 8 * 1024 * 1024:
                    raise modkit.KitError('Not enough free space on the drive')
                modkit.download(asset, root, self._progress_cb(f'Downloading {asset["name"]}'), self.cancel)
                modkit.remove_older(root, c, asset['name'])
                self.log(f'  Downloaded and verified {asset["name"]}.', 'ok')
            library.prepare_drive(root)
            guide = modkit.write_guide(root, kind)
            self.log(f'Guide written: {guide}', 'ok')
            if not modkit.drive_pup(root):
                self.log('Still needed: add the firmware file (step 1) before taking the USB to the PS3.', 'warn')
            else:
                self.log('Mod kit is ready. Plug the USB into the PS3 and follow PS3_MOD_GUIDE.txt.', 'ok')
        self.run_task('Building mod kit...', task)

    def on_open_guide(self):
        if not self.drive:
            return
        path = os.path.join(self.drive.root, modkit.GUIDE_NAME)
        if not os.path.isfile(path):
            if not self._kit_drive_ok():
                return
            modkit.write_guide(self.drive.root, self.fw_kind.get())
        os.startfile(path)

    def _build_library(self, parent):
        tab = ttk.Frame(parent, style='Panel.TFrame', padding=12)
        bar = ttk.Frame(tab, style='Panel.TFrame')
        bar.pack(fill='x', pady=(0, 8))
        self.lib_buttons = [
            ttk.Button(bar, text='Add games...', style='Accent.TButton', command=self.on_add_files),
            ttk.Button(bar, text='Add folder...', command=self.on_add_folder),
            ttk.Button(bar, text='Remove', command=self.on_remove),
            ttk.Button(bar, text='Get covers', command=self.on_covers),
            ttk.Button(bar, text='Fix names', command=self.on_fix_names),
            ttk.Button(bar, text='Rescan', command=self.rescan),
        ]
        for b in self.lib_buttons:
            b.pack(side='left', padx=(0, 6))

        # right side: view toggle, filter, search
        self.view_var = tk.StringVar(value=self.settings.get('view', 'grid'))
        self.view_btns = {}
        for v, label in (('list', 'List'), ('grid', 'Grid')):
            b = ttk.Button(bar, text=label, style='Small.TButton', width=6, command=lambda v=v: self.set_view(v))
            self.view_btns[v] = b
        self.view_btns['grid'].pack(side='right')
        self.view_btns['list'].pack(side='right', padx=(10, 2))
        self.filter_var = tk.StringVar(value='All')
        fbox = ttk.Combobox(bar, textvariable=self.filter_var, state='readonly', width=6, values=list(FILTERS))
        fbox.pack(side='right')
        fbox.bind('<<ComboboxSelected>>', lambda e: self._fill_tree())
        self.search_var = tk.StringVar()
        search = ttk.Entry(bar, textvariable=self.search_var, width=18)
        search.pack(side='right', padx=(0, 6))
        self.search_var.trace_add('write', lambda *_: self._schedule_fill())
        ttk.Label(bar, text='Search', style='PanelMuted.TLabel').pack(side='right', padx=(0, 6))

        # footer is packed from the bottom first so it never gets squeezed out
        foot = ttk.Frame(tab, style='Panel.TFrame')
        foot.pack(side='bottom', fill='x', pady=(8, 0))

        # storage bar
        self.storage = tk.Canvas(tab, height=30, bg=C['panel'], highlightthickness=0, bd=0)
        self.storage.pack(fill='x', pady=(0, 8))
        self.storage.bind('<Configure>', lambda e: self._draw_storage())

        body = ttk.Frame(tab, style='Panel.TFrame')
        body.pack(fill='both', expand=True)
        self._build_preview(body)
        self.list_frame = ttk.Frame(body, style='Panel.TFrame')
        cols = ('system', 'title', 'id', 'format', 'size', 'cover', 'notes')
        self.tree = ttk.Treeview(self.list_frame, columns=cols, show='headings', selectmode='extended')
        widths = {'system': 70, 'title': 330, 'id': 100, 'format': 80, 'size': 80, 'cover': 55, 'notes': 280}
        for c in cols:
            self.tree.heading(c, text=c.upper() if c == 'id' else c.capitalize(), anchor='w')
            self.tree.column(c, width=widths[c], anchor='w', stretch=c in ('title', 'notes'))
        for sysname, col in SYSTEM_COLORS.items():
            self.tree.tag_configure(sysname, foreground=col)
        self.tree.tag_configure('warn', foreground=C['warn'])
        sb = ttk.Scrollbar(self.list_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.tree.bind('<<TreeviewSelect>>', lambda e: self._on_selection())
        self.tree.bind('<Button-3>', self._tree_context)
        self.grid_view = CoverGrid(body, C, self._load_image, self._grid_click, self._show_context)
        self.set_view(self.view_var.get(), save=False)

        self.menu = tk.Menu(self, tearoff=False, bg=C['field'], fg=C['fg'], activebackground=C['sel'],
                            activeforeground=C['fg'], bd=0)
        self.menu.add_command(label='Show in Explorer', command=self.on_explore)
        self.menu.add_command(label='Re-download cover', command=self.on_redownload_cover)
        self.menu.add_command(label='Use official name', command=self.on_fix_names)
        self.menu.add_separator()
        self.menu.add_command(label='Remove from drive', command=self.on_remove)

        self.count_var = tk.StringVar(value='')
        ttk.Label(foot, textvariable=self.count_var, style='PanelMuted.TLabel').pack(side='left')
        self.auto_cover_var = tk.BooleanVar(value=self.settings.get('auto_covers', True))
        ttk.Checkbutton(foot, text='Get covers', variable=self.auto_cover_var,
                        command=self._save_opts).pack(side='right')
        self.official_var = tk.BooleanVar(value=self.settings.get('official_names', True))
        ttk.Checkbutton(foot, text='Official names', variable=self.official_var,
                        command=self._save_opts).pack(side='right', padx=(0, 12))
        ttk.Label(foot, text='When adding:', style='PanelMuted.TLabel').pack(side='right', padx=(0, 8))
        self.model_var = tk.StringVar(value=gamedb.PS3_MODELS.get(self.settings.get('ps3_model'),
                                                                  gamedb.PS3_MODELS['none']))
        model = ttk.Combobox(foot, textvariable=self.model_var, state='readonly', width=32,
                             values=list(gamedb.PS3_MODELS.values()))
        model.pack(side='right', padx=(0, 24))
        model.bind('<<ComboboxSelected>>', lambda e: (self._save_opts(), self._fill_tree()))
        ttk.Label(foot, text='My PS3:', style='PanelMuted.TLabel').pack(side='right', padx=(0, 6))
        return tab

    @property
    def ps3_model(self) -> str:
        label = self.model_var.get()
        return next((k for k, v in gamedb.PS3_MODELS.items() if v == label), 'none')

    def set_view(self, view, save=True):
        self.view_var.set(view)
        if view == 'grid':
            self.list_frame.pack_forget()
            self.grid_view.pack(side='left', fill='both', expand=True)
        else:
            self.grid_view.pack_forget()
            self.list_frame.pack(side='left', fill='both', expand=True)
        for v, b in self.view_btns.items():
            b.configure(style='Accent.TButton' if v == view else 'Small.TButton')
        if save:
            self.settings['view'] = view
            self._save_opts()

    # ------------------------------------------------------------------ selection (list + grid)
    def _on_selection(self):
        self.show_preview()
        self.grid_view.set_selection(self.tree.selection())

    def _grid_click(self, iid, mode):
        if mode == 'toggle':
            self.tree.selection_toggle(iid)
        elif mode == 'range' and self.tree.selection():
            order = list(self.tree.get_children())
            a = order.index(self.tree.selection()[0]) if self.tree.selection()[0] in order else 0
            b = order.index(iid)
            self.tree.selection_set(order[min(a, b):max(a, b) + 1])
        else:
            self.tree.selection_set(iid)

    def _tree_context(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self._show_context(iid, e)

    def _show_context(self, _iid, e):
        g = self._preview_game
        can_rename = bool(g and self._official_title(g) and self._official_title(g) != g.title)
        self.menu.entryconfigure('Use official name', state='normal' if can_rename and not self.busy else 'disabled')
        self.menu.entryconfigure('Re-download cover',
                                 state='normal' if (g and library.cover_base(g) and not self.busy) else 'disabled')
        self.menu.entryconfigure('Remove from drive', state='disabled' if self.busy else 'normal')
        self.menu.tk_popup(e.x_root, e.y_root)

    def _official_title(self, g) -> str:
        return self.db.title(g.system, g.game_id)

    def _game_notes(self, g) -> str:
        notes = list(g.warnings)
        if g.system == 'PS2':
            short, _ = self.db.ps2_hint(g.game_id, self.ps3_model)
            if short:
                notes.insert(0, short)
        return '; '.join(notes)

    # ------------------------------------------------------------------ storage bar
    def _draw_storage(self):
        cv = self.storage
        cv.delete('all')
        w = cv.winfo_width()
        if not self.drive or w < 50:
            return
        try:
            import shutil
            usage = shutil.disk_usage(self.drive.root)
        except OSError:
            return
        sizes = [(label, sum(g.size for g in self.games if g.system == s), SYSTEM_COLORS[s])
                 for label, s in (('PS3', 'PS3'), ('PS2', 'PS2'), ('PS1', 'PS1'), ('Retro', 'RETRO'))]
        games_total = sum(s for _, s, _ in sizes)
        other = max(0, usage.used - games_total)
        segs = sizes + [('Other files', other, '#4b5563')]
        x, h = 0.0, 10
        cv.create_rectangle(0, 0, w, h, fill=C['field'], width=0)
        for _label, size, col in segs:
            seg = w * size / max(usage.total, 1)
            if seg >= 1:
                cv.create_rectangle(x, 0, x + seg, h, fill=col, width=0)
            x += seg
        lx = 0
        for label, size, col in segs:
            if not size:
                continue
            cv.create_rectangle(lx, 18, lx + 9, 27, fill=col, width=0)
            t = cv.create_text(lx + 14, 22, text=f'{label} {fmt_size(size)}', anchor='w', fill=C['muted'],
                               font=('Segoe UI', 9))
            lx = cv.bbox(t)[2] + 16
        cv.create_text(w, 22, text=f'{fmt_size(usage.free)} free of {fmt_size(usage.total)}', anchor='e',
                       fill=C['fg'], font=('Segoe UI', 9))

    # ================================================================== cover preview
    PREVIEW_W = 220  # panel content width; the image height adapts to the space available

    def _build_preview(self, parent):
        pane = tk.Frame(parent, bg=C['bg'], width=self.PREVIEW_W + 28)
        pane.pack(side='right', fill='y', padx=(12, 0))
        pane.pack_propagate(False)
        inner = tk.Frame(pane, bg=C['bg'], padx=14, pady=14)
        inner.pack(fill='both', expand=True)
        # bottom-up: buttons, details, title always visible; the cover takes whatever is left
        btns = tk.Frame(inner, bg=C['bg'])
        btns.pack(side='bottom', fill='x')
        self.prev_link = tk.Label(inner, text='', bg=C['bg'], fg=C['accent'], font=('Segoe UI', 9, 'underline'),
                                  cursor='hand2', anchor='w')
        self.prev_link.pack(side='bottom', fill='x', pady=(4, 0))
        self.prev_link.bind('<Button-1>', lambda e: self.prev_link.cget('text') and
                            webbrowser.open(gamedb.COMPAT_LIST_URL))
        self.prev_meta = tk.Label(inner, text='', bg=C['bg'], fg=C['muted'], font=('Segoe UI', 9),
                                  wraplength=self.PREVIEW_W, justify='left', anchor='w')
        self.prev_meta.pack(side='bottom', fill='x')
        self.prev_title = tk.Label(inner, text='', bg=C['bg'], fg=C['fg'], font=('Segoe UI Semibold', 11),
                                   wraplength=self.PREVIEW_W, justify='left', anchor='w')
        self.prev_title.pack(side='bottom', fill='x', pady=(8, 2))
        self.cover_img_label = tk.Label(inner, bg=C['field'], fg=C['muted'], font=('Segoe UI', 10),
                                        text='Select a game\nto see its cover', justify='center',
                                        bd=0, highlightthickness=0, padx=0, pady=0)
        self.cover_img_label.pack(side='top', fill='both', expand=True)
        self._cover_path = None
        self._rendered_size = None
        self._render_job = None
        self.cover_img_label.bind('<Configure>', lambda e: self._schedule_render())
        self.cover_buttons = [
            ttk.Button(btns, text='Re-download cover', command=self.on_redownload_cover),
            ttk.Button(btns, text='Choose image...', command=self.on_choose_cover),
            ttk.Button(btns, text='Remove cover', command=self.on_remove_cover),
        ]
        btns.columnconfigure((0, 1, 2), weight=1, uniform='b')
        for col, (b, text) in enumerate(zip(self.cover_buttons, ('Download', 'Choose...', 'Remove'))):
            b.configure(text=text, style='Small.TButton')
            b.grid(row=0, column=col, sticky='ew', pady=(6, 0), padx=(0 if col == 0 else 2, 0 if col == 2 else 2))
        self._preview_game = None
        self._update_cover_buttons()

    def _set_cover_text(self, text):
        self._preview_img = None
        self.cover_img_label.configure(image='', text=text)

    def _schedule_render(self):
        if self._render_job:
            self.after_cancel(self._render_job)
        self._render_job = self.after(60, self._render_cover)

    def _render_cover(self, force=False):
        """Draw the current cover scaled to fit the space the label has been given."""
        self._render_job = None
        path = self._cover_path
        if not path:
            return
        w, h = self.cover_img_label.winfo_width(), self.cover_img_label.winfo_height()
        if w < 40 or h < 40:
            w, h = self.PREVIEW_W, int(self.PREVIEW_W * 1.4)
        if not force and self._rendered_size == (w, h):
            return
        try:
            self._preview_img = self._load_image(path, w, h)
            self.cover_img_label.configure(image=self._preview_img, text='')
            self._rendered_size = (w, h)
        except Exception:  # noqa: BLE001 - corrupt or unreadable image
            self._set_cover_text('Cover image could not\nbe displayed')

    def _load_image(self, path, w, h):
        if Image is not None:
            img = Image.open(path)
            img.load()
            img = img.convert('RGBA')
            img.thumbnail((w, h), Image.LANCZOS)
            canvas = Image.new('RGBA', (w, h), C['bg'])
            canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2), img)
            return ImageTk.PhotoImage(canvas)
        if not path.lower().endswith('.png'):
            raise ValueError('JPG preview needs Pillow (pip install pillow)')
        img = tk.PhotoImage(file=path)
        factor = max(1, -(-img.width() // w), -(-img.height() // h))
        return img.subsample(factor)

    @staticmethod
    def _image_size(path):
        if Image is not None:
            with Image.open(path) as img:
                return img.size
        img = tk.PhotoImage(file=path)
        return img.width(), img.height()

    def _update_cover_buttons(self):
        g = self._preview_game
        state = 'normal' if (g and library.cover_base(g) and not self.busy) else 'disabled'
        for b in self.cover_buttons:
            b.configure(state=state)
        if g and g.cover is None and state == 'normal':
            self.cover_buttons[2].configure(state='disabled')
        if g and not g.game_id and g.system != 'RETRO':
            self.cover_buttons[0].configure(state='disabled')

    def show_preview(self):
        sel = self.tree.selection()
        g = self.games[int(sel[0])] if sel else None
        self._preview_game = g
        self._cover_path = None
        self.prev_link.configure(text='')
        if g is None:
            self._set_cover_text('Select a game\nto see its cover')
            self.prev_title.configure(text='')
            self.prev_meta.configure(text='')
        else:
            if g.system == 'RETRO':
                meta = [f'{retro.platform_name(g.platform)}  |  {fmt_size(g.size)}',
                        'Plays through RetroArch on the PS3']
            else:
                meta = [f'{g.system}  |  {g.display_id}  |  {g.fmt}  |  {fmt_size(g.size)}']
            official = self._official_title(g)
            if official and official != g.title:
                meta.append(f'Official name: {official}')
            if g.system == 'PS2':
                _short, long = self.db.ps2_hint(g.game_id, self.ps3_model)
                meta.append(long)
                if self.ps3_model != 'bc':
                    self.prev_link.configure(text='Check PS2 compatibility online')
            if g.cover:
                try:
                    w, h = self._image_size(g.cover)
                    meta.append(f'Cover: {w} x {h} {os.path.splitext(g.cover)[1][1:].upper()}')
                    if library.cover_base(g) is None:
                        meta.append('Folder games show their own icon')
                    self._cover_path = g.cover
                    self._render_cover(force=True)
                except Exception as e:  # noqa: BLE001 - corrupt or unreadable image
                    self._set_cover_text('Cover image could not\nbe displayed')
                    meta.append(f'Cover file problem: {e}')
            else:
                self._set_cover_text('No cover yet')
                meta.append('No cover file' if library.cover_base(g) else 'No ICON0.PNG in this game folder')
            self.prev_title.configure(text=g.title)
            self.prev_meta.configure(text='\n'.join(meta))
        self._update_cover_buttons()

    @staticmethod
    def _delete_covers(base):
        for ext in library.COVER_EXTS:
            if os.path.isfile(base + ext):
                os.remove(base + ext)

    def _cover_changed(self, g):
        old = g.cover
        g.cover = library.find_cover(g)
        for p in {old, g.cover} - {None}:
            self.grid_view.forget_thumbs(p)
        iid = str(self.games.index(g))
        if self.tree.exists(iid):
            self.tree.set(iid, 'cover', 'yes' if g.cover else '-')
        self._fill_grid()
        if self._preview_game is g:
            self.show_preview()

    def on_redownload_cover(self):
        g = self._preview_game
        if not g:
            return
        base = library.cover_base(g)
        templates = self.settings.get('cover_templates')

        def task():
            self.log(f'Downloading cover for {g.title} ({g.game_id})...')
            new = covers.fetch_cover(g, base + '.__new', templates)
            if not new:
                self.log('  No cover found online. Use "Choose image..." to pick one yourself.', 'warn')
                return
            self._delete_covers(base)
            os.replace(new, base + os.path.splitext(new)[1])
            self.log('  Cover replaced.', 'ok')
            self.post('cover', g)
        self.run_task('Downloading cover...', task)

    def on_choose_cover(self):
        g = self._preview_game
        if not g:
            return
        types = '*.jpg *.jpeg *.png' + (' *.webp *.bmp *.gif' if Image is not None else '')
        path = filedialog.askopenfilename(title=f'Choose a cover for {g.title}',
                                          filetypes=[('Images', types), ('All files', '*.*')])
        if not path:
            return
        base = library.cover_base(g)
        try:
            if Image is not None:  # normalise everything to a reasonably sized JPG
                img = Image.open(path)
                img.load()
                img = img.convert('RGB')
                img.thumbnail((1000, 1000), Image.LANCZOS)
                self._delete_covers(base)
                img.save(base + '.jpg', 'JPEG', quality=90)
            else:
                ext = '.png' if path.lower().endswith('.png') else '.jpg'
                with open(path, 'rb') as f:
                    data = f.read()
                self._delete_covers(base)
                with open(base + ext, 'wb') as f:
                    f.write(data)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f'Could not use that image: {e}')
            return
        self.log(f'Cover set for {g.title}.', 'ok')
        self._cover_changed(g)

    def on_remove_cover(self):
        g = self._preview_game
        if not g or not g.cover:
            return
        if messagebox.askyesno(APP_NAME, f'Remove the cover for {g.title}?'):
            self._delete_covers(library.cover_base(g))
            self.log(f'Cover removed for {g.title}.', 'ok')
            self._cover_changed(g)

    # ================================================================== themes
    def _build_themes(self, parent):
        outer_tab = ttk.Frame(parent, style='Panel.TFrame', padding=12)
        switch = ttk.Frame(outer_tab, style='Panel.TFrame')
        switch.pack(fill='x', pady=(0, 10))
        self.theme_mode_btns = {}
        for mode, label in (('browse', 'Browse themes'), ('drive', 'On this drive')):
            b = ttk.Button(switch, text=label, command=lambda m=mode: self.set_theme_mode(m))
            b.pack(side='left', padx=(0, 4))
            self.theme_mode_btns[mode] = b
        ttk.Label(switch, style='PanelMuted.TLabel', text=(
            'On the PS3: Settings > Theme Settings > Theme, then pick from the USB drive (FAT32 only).')
        ).pack(side='right')
        self.theme_pages = {'browse': self._build_theme_browser(outer_tab)}

        tab = ttk.Frame(outer_tab, style='Panel.TFrame')
        self.theme_pages['drive'] = tab
        bar = ttk.Frame(tab, style='Panel.TFrame')
        bar.pack(fill='x')
        self.theme_buttons = [
            ttk.Button(bar, text='Add themes from PC...', style='Accent.TButton', command=self.on_add_themes),
            ttk.Button(bar, text='Remove', command=self.on_remove_theme),
            ttk.Button(bar, text='Show in Explorer', command=self.on_explore_themes),
        ]
        for b in self.theme_buttons:
            b.pack(side='left', padx=(0, 6))
        ttk.Label(tab, style='PanelMuted.TLabel', justify='left', wraplength=1050, text=(
            'Themes (.p3t files) are copied to PS3\\THEME on the drive. You can also drag .p3t files onto this window.')
        ).pack(fill='x', pady=(10, 10))

        body = ttk.Frame(tab, style='Panel.TFrame')
        body.pack(fill='both', expand=True)
        side = tk.Frame(body, bg=C['bg'], width=360)
        side.pack(side='right', fill='y', padx=(12, 0))
        side.pack_propagate(False)
        self.theme_info = tk.Label(side, bg=C['bg'], fg=C['muted'], font=('Segoe UI', 9), anchor='w',
                                   justify='left', wraplength=330)
        self.theme_info.pack(side='bottom', fill='x', padx=12, pady=(0, 12))
        self.theme_img = tk.Label(side, bg=C['bg'], fg=C['muted'], text='Select a theme', bd=0)
        self.theme_img.pack(side='top', fill='both', expand=True, padx=12, pady=12)
        self._theme_photo = None

        frame = ttk.Frame(body, style='Panel.TFrame')
        frame.pack(side='left', fill='both', expand=True)
        self.theme_tree = ttk.Treeview(frame, columns=('name', 'size'), show='headings', selectmode='extended')
        self.theme_tree.heading('name', text='Theme', anchor='w')
        self.theme_tree.heading('size', text='Size', anchor='w')
        self.theme_tree.column('size', width=90, stretch=False)
        sb = ttk.Scrollbar(frame, orient='vertical', command=self.theme_tree.yview)
        self.theme_tree.configure(yscrollcommand=sb.set)
        self.theme_tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.theme_tree.bind('<<TreeviewSelect>>', lambda e: self._theme_preview())
        self.set_theme_mode('browse')
        return outer_tab

    def set_theme_mode(self, mode):
        for m, page in self.theme_pages.items():
            if m == mode:
                page.pack(fill='both', expand=True)
            else:
                page.pack_forget()
        for m, b in self.theme_mode_btns.items():
            b.configure(style='Accent.TButton' if m == mode else 'TButton')
        self._theme_mode = mode
        self._maybe_load_store()

    def _maybe_load_store(self):
        """Load the theme catalogue the first time the Browse view is actually shown."""
        nb = getattr(self, 'notebook', None)
        on_tab = nb is not None and nb.select() and nb.index(nb.select()) == 2
        if on_tab and self._theme_mode == 'browse' and not self.store_items and not self._store_loading:
            self.after(50, self.store_search)

    # ------------------------------------------------------------------ theme browser (Themezer)
    STORE_TILE = (200, 112)

    def _build_theme_browser(self, parent):
        page = ttk.Frame(parent, style='Panel.TFrame')
        self.store_items: list[theme_store.ThemeItem] = []
        self.store_page = 0
        self.store_pages = 1
        self._store_loading = False
        self._store_sel = None
        self._store_photo = None

        bar = ttk.Frame(page, style='Panel.TFrame')
        bar.pack(fill='x', pady=(0, 8))
        ttk.Label(bar, text='Search', style='PanelMuted.TLabel').pack(side='left', padx=(0, 6))
        self.store_query = tk.StringVar()
        q = ttk.Entry(bar, textvariable=self.store_query, width=26)
        q.pack(side='left')
        q.bind('<Return>', lambda e: self.store_search())
        self.store_sort = tk.StringVar(value='Most downloaded')
        sort = ttk.Combobox(bar, textvariable=self.store_sort, state='readonly', width=16,
                            values=list(theme_store.SORTS))
        sort.pack(side='left', padx=(8, 0))
        sort.bind('<<ComboboxSelected>>', lambda e: self.store_search())
        self.store_flags = {k: tk.BooleanVar() for k in ('dynamic', 'icons', 'sfx')}
        for k, label in (('dynamic', 'Animated'), ('icons', 'Custom icons'), ('sfx', 'Custom sounds')):
            ttk.Checkbutton(bar, text=label, variable=self.store_flags[k],
                            command=self.store_search).pack(side='left', padx=(12, 0))
        ttk.Button(bar, text='Search', style='Small.TButton', width=8,
                   command=self.store_search).pack(side='left', padx=(12, 0))
        self.store_count = tk.StringVar(value='')
        ttk.Label(bar, textvariable=self.store_count, style='PanelMuted.TLabel').pack(side='right')

        body = ttk.Frame(page, style='Panel.TFrame')
        body.pack(fill='both', expand=True)

        # details panel (right)
        side = tk.Frame(body, bg=C['bg'], width=348)
        side.pack(side='right', fill='y', padx=(12, 0))
        side.pack_propagate(False)
        inner = tk.Frame(side, bg=C['bg'], padx=14, pady=12)
        inner.pack(fill='both', expand=True)
        btns = tk.Frame(inner, bg=C['bg'])
        btns.pack(side='bottom', fill='x', pady=(8, 0))
        self.store_add_btn = ttk.Button(btns, text='Add to drive', style='Accent.TButton', command=self.on_store_add)
        self.store_add_btn.pack(side='left', fill='x', expand=True, padx=(0, 4))
        self.store_page_btn = ttk.Button(btns, text='View on Themezer', command=self.on_store_page)
        self.store_page_btn.pack(side='left', fill='x', expand=True)
        self._store_blank = tk.PhotoImage(width=self.STORE_PREVIEW[0], height=self.STORE_PREVIEW[1])
        self.store_img = tk.Label(inner, bg=C['field'], fg=C['muted'], text='Select a theme to preview it',
                                  image=self._store_blank, compound='center', bd=0)
        self.store_img.pack(fill='x')
        self.store_title = tk.Label(inner, text='', bg=C['bg'], fg=C['fg'], font=('Segoe UI Semibold', 11),
                                    anchor='w', justify='left', wraplength=320)
        self.store_title.pack(fill='x', pady=(8, 0))
        self.store_meta = tk.Label(inner, text='', bg=C['bg'], fg=C['muted'], font=('Segoe UI', 9),
                                   anchor='w', justify='left', wraplength=320)
        self.store_meta.pack(fill='x', pady=(0, 4))
        self.store_desc = tk.Text(inner, bg=C['bg'], fg=C['fg'], relief='flat', wrap='word', height=4,
                                  font=('Segoe UI', 9), highlightthickness=0, state='disabled', cursor='arrow')
        self.store_desc.pack(fill='both', expand=True)
        self._store_buttons_state()

        # results grid (left) + load more
        left = ttk.Frame(body, style='Panel.TFrame')
        left.pack(side='left', fill='both', expand=True)
        more_row = ttk.Frame(left, style='Panel.TFrame')
        more_row.pack(side='bottom', fill='x', pady=(6, 0))
        self.store_more = ttk.Button(more_row, text='Load more', command=lambda: self.store_search(more=True))
        self.store_more.pack(side='right')
        ttk.Label(more_row, style='PanelMuted.TLabel', text=(
            'Community themes from themezer.net (adult themes filtered out). Double-click to add.')
        ).pack(side='left')
        self.store_grid = CoverGrid(left, C, self._load_image, self._store_click, lambda *_: None,
                                    on_double=lambda iid: self.on_store_add(), tile=self.STORE_TILE,
                                    empty_text='No preview')
        self.store_grid.pack(fill='both', expand=True)
        return page

    STORE_PREVIEW = (320, 180)

    def store_search(self, more=False):
        if self._store_loading:
            return
        page = self.store_page + 1 if more else 1
        if more and page > self.store_pages:
            return
        args = dict(query=self.store_query.get(), sort=theme_store.SORTS[self.store_sort.get()], page=page,
                    dynamic=self.store_flags['dynamic'].get(), icons=self.store_flags['icons'].get(),
                    sfx=self.store_flags['sfx'].get())
        self._store_loading = True
        self.store_more.configure(state='disabled', text='Loading...')
        if not more:
            self.store_count.set('Loading themes...')
        cache = os.path.join(SETTINGS_DIR, 'cache', 'themes')

        def work():
            try:
                items, total, pages = theme_store.search(**args)
                theme_store.fetch_thumbs(items, cache)
                self.post('store', items, total, pages, page, more)
            except theme_store.StoreError as e:
                self.post('store_error', str(e))
        threading.Thread(target=work, daemon=True).start()

    def _store_results(self, items, total, pages, page, more):
        self._store_loading = False
        self.store_page, self.store_pages = page, pages
        start = len(self.store_items) if more else 0
        if more:
            self.store_items += items
        else:
            self.store_items = items
            self.store_grid.canvas.yview_moveto(0)
        rows = [(str(start + n), it.name, f'by {it.creator}  |  {it.downloads:,} downloads', it.thumb_path,
                 C['muted']) for n, it in enumerate(items)]
        if more:
            self.store_grid.append_items(rows)
        else:
            self.store_grid.set_items(rows)
            self._store_select(None)
        self.store_count.set(f'{len(self.store_items):,} of {total:,} themes' if total else 'No themes found')
        self.store_more.configure(state='normal' if page < pages else 'disabled',
                                  text='Load more' if page < pages else 'No more themes')

    def _store_click(self, iid, _mode):
        self._store_select(iid)

    def _store_select(self, iid):
        self.store_grid.set_selection([iid] if iid is not None else [])
        item = self.store_items[int(iid)] if iid is not None else None
        self._store_sel = item
        self._store_buttons_state()
        self.store_desc.configure(state='normal')
        self.store_desc.delete('1.0', 'end')
        if item is None:
            self.store_img.configure(image=self._store_blank, text='Select a theme to preview it')
            self.store_title.configure(text='')
            self.store_meta.configure(text='')
        else:
            self.store_title.configure(text=item.name)
            tags = ', '.join(t for t in item.tags if t.lower() != 'archived')
            self.store_meta.configure(text=f'by {item.creator}  |  {item.downloads:,} downloads\n{item.features}')
            desc = item.clean_description
            if tags:
                desc = f'Tags: {tags}\n\n{desc}'
            self.store_desc.insert('1.0', desc)
            self._show_store_image(item.thumb_path, loading=True)
            cache = os.path.join(SETTINGS_DIR, 'cache', 'themes')

            def work():
                path = theme_store.fetch_image(item.hd_url, cache, 'hd')
                self.post('store_hd', item, path)
            threading.Thread(target=work, daemon=True).start()
        self.store_desc.configure(state='disabled')

    def _show_store_image(self, path, loading=False):
        if not path:
            self.store_img.configure(image=self._store_blank, text='Loading preview...' if loading else 'No preview')
            return
        try:
            self._store_photo = self._load_image(path, *self.STORE_PREVIEW)
            self.store_img.configure(image=self._store_photo, text='')
        except Exception:  # noqa: BLE001
            self.store_img.configure(image=self._store_blank, text='No preview')

    def _store_buttons_state(self):
        state = 'normal' if self._store_sel is not None and not self.busy else 'disabled'
        self.store_add_btn.configure(state=state)
        self.store_page_btn.configure(state='normal' if self._store_sel is not None else 'disabled')

    def on_store_page(self):
        if self._store_sel:
            webbrowser.open(self._store_sel.page_url)

    def on_store_add(self):
        item = self._store_sel
        if item is None or not self._themes_drive_ok():
            return
        d = themes.theme_dir(self.drive.root)
        dest = os.path.join(d, library.safe_name(item.name, 100) + '.p3t')
        if os.path.exists(dest):
            messagebox.showinfo(APP_NAME, f'"{item.name}" is already on the drive.')
            return

        def task():
            os.makedirs(d, exist_ok=True)
            self.log(f'Downloading theme "{item.name}" by {item.creator}...')
            theme_store.download_theme(item, dest, self._progress_cb(f'Theme {item.name}'), self.cancel)
            self.log(f'  Added to the drive: {os.path.relpath(dest, self.drive.root)}', 'ok')
            hd = theme_store.fetch_image(item.hd_url, os.path.join(SETTINGS_DIR, 'cache', 'themes'), 'hd')
            if hd:  # remember the preview so the "On this drive" view can show it
                os.makedirs(self._theme_preview_dir(), exist_ok=True)
                shutil.copyfile(hd, self._theme_preview_path(dest))
            self.post('themes')
        self.run_task('Downloading theme...', task)

    def refresh_themes(self):
        if not hasattr(self, 'theme_tree'):
            return
        self.theme_tree.delete(*self.theme_tree.get_children())
        if self.drive:
            for p in themes.list_themes(self.drive.root):
                self.theme_tree.insert('', 'end', iid=p, values=(os.path.splitext(os.path.basename(p))[0],
                                                                  fmt_size(os.path.getsize(p))))
        self._theme_preview()

    def _theme_preview(self):
        sel = self.theme_tree.selection()
        self._theme_photo = None
        if not sel:
            n = len(self.theme_tree.get_children())
            self.theme_img.configure(image='', text='Select a theme' if n else 'No themes on this drive yet')
            self.theme_info.configure(text='')
            return
        path = sel[0]
        info = [os.path.basename(path), fmt_size(os.path.getsize(path))]
        saved = self._theme_preview_path(path)
        if os.path.isfile(saved):
            try:
                self._theme_photo = self._load_image(saved, 330, 186)
                self.theme_img.configure(image=self._theme_photo, text='')
                self.theme_info.configure(text='\n'.join(info + ['Preview from themezer.net']))
                return
            except Exception:  # noqa: BLE001 - fall back to the embedded image
                pass
        try:
            data = themes.preview_bytes(path) if Image is not None else None
            if data:
                import io
                img = Image.open(io.BytesIO(data))
                img.load()
                img = img.convert('RGBA')
                img.thumbnail((330, 330), Image.LANCZOS)
                self._theme_photo = ImageTk.PhotoImage(img)
                self.theme_img.configure(image=self._theme_photo, text='')
                info.append('Preview: an image found inside the theme')
            else:
                self.theme_img.configure(image='', text='No preview image found in this theme')
        except Exception:  # noqa: BLE001 - best-effort preview only
            self.theme_img.configure(image='', text='No preview image found in this theme')
        self.theme_info.configure(text='\n'.join(info))

    @staticmethod
    def _theme_preview_dir():
        return os.path.join(SETTINGS_DIR, 'cache', 'theme_previews')

    def _theme_preview_path(self, p3t_path):
        return os.path.join(self._theme_preview_dir(),
                            os.path.splitext(os.path.basename(p3t_path))[0].lower() + '.webp')

    def _themes_drive_ok(self) -> bool:
        if not self.drive:
            messagebox.showwarning(APP_NAME, 'Select your USB drive first.')
            return False
        if not (self.drive.fs or '').upper().startswith('FAT'):
            return messagebox.askyesno(APP_NAME, (
                f'This drive is {self.drive.fs or "an unknown format"}. The PS3 can only install themes from '
                'FAT32 USB drives. Copy the themes anyway?'))
        return True

    def on_add_themes(self, paths=None):
        if not self._themes_drive_ok():
            return
        paths = paths or filedialog.askopenfilenames(title='Choose PS3 themes',
                                                     filetypes=[('PS3 themes', '*.p3t'), ('All files', '*.*')])
        if not paths:
            return
        root = self.drive.root

        def task():
            added = 0
            for n, p in enumerate(paths, 1):
                self.post('progress', n - 1, len(paths), f'Copying theme {n}/{len(paths)}')
                try:
                    themes.add_theme(p, root)
                    added += 1
                    self.log(f'Theme added: {os.path.basename(p)}', 'ok')
                except (themes.ThemeError, OSError) as e:
                    self.log(f'Skipped {os.path.basename(p)}: {e}', 'warn')
            self.post('themes')
        self.run_task('Adding themes...', task)

    def on_remove_theme(self):
        sel = self.theme_tree.selection()
        if sel and messagebox.askyesno(APP_NAME, f'Delete {len(sel)} theme(s) from the drive?'):
            for p in sel:
                os.remove(p)
                self.log(f'Theme removed: {os.path.basename(p)}', 'ok')
            self.refresh_themes()

    def on_explore_themes(self):
        if self.drive:
            d = themes.theme_dir(self.drive.root)
            os.startfile(d if os.path.isdir(d) else self.drive.root)

    # ================================================================== drag and drop
    def _setup_dnd(self):
        if TkinterDnD is None:
            return
        for w in (self.tree, self.grid_view.canvas, self.theme_tree, self.notebook):
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind('<<Drop>>', self._on_drop)
            except Exception:  # noqa: BLE001
                pass

    def _on_drop(self, event):
        paths = [p for p in self.tk.splitlist(event.data) if os.path.exists(p)]
        if not paths:
            return
        theme_files = [p for p in paths if p.lower().endswith('.p3t')]
        other = [p for p in paths if p not in theme_files]
        if theme_files:
            self.notebook.select(2)
            self.set_theme_mode('drive')
            self.on_add_themes(theme_files)
        elif other:
            if not self._check_drive_ready():
                return
            self.notebook.select(1)

            def task():
                found = []
                for p in other:
                    found += discinfo.find_candidates(p) if os.path.isdir(p) and not os.path.isfile(
                        os.path.join(p, 'PS3_GAME', 'PARAM.SFO')) else [p]
                self._import_task(found)
            self.run_task('Adding games...', task)
        return event.action

    def _build_console(self, parent):
        tab = ttk.Frame(parent, style='Panel.TFrame', padding=12)
        row = ttk.Frame(tab, style='Panel.TFrame')
        row.pack(fill='x')
        ttk.Label(row, text='PS3 IP address', style='Panel.TLabel').pack(side='left', padx=(0, 8))
        self.ip_var = tk.StringVar(value=self.settings.get('ps3_ip', ''))
        ip = ttk.Entry(row, textvariable=self.ip_var, width=18)
        ip.pack(side='left')
        ip.bind('<FocusOut>', lambda e: self._save_opts())
        self.net_buttons = [
            ttk.Button(row, text='Test connection', command=self.on_test),
            ttk.Button(row, text='Send selected games', style='Accent.TButton', command=self.on_send),
            ttk.Button(row, text='List games on PS3', command=self.on_list_remote),
            ttk.Button(row, text='Refresh webMAN', command=self.on_refresh_webman),
        ]
        for b in self.net_buttons:
            b.pack(side='left', padx=(6, 0))

        ttk.Label(tab, style='PanelMuted.TLabel', justify='left', text=(
            'Copies games from the drive library over webMAN\'s FTP server to the PS3\'s internal HDD '
            '(/dev_hdd0). Select games in the Drive library tab first. Split ISOs are rejoined on the PS3.\n'
            'The PS3 must be on, on the same network, with webMAN MOD loaded.')
        ).pack(fill='x', pady=(10, 10))

        frame = ttk.Frame(tab, style='Panel.TFrame')
        frame.pack(fill='both', expand=True)
        self.remote_tree = ttk.Treeview(frame, columns=('name',), show='tree headings')
        self.remote_tree.heading('#0', text='Folder', anchor='w')
        self.remote_tree.heading('name', text='Game', anchor='w')
        self.remote_tree.column('#0', width=200, stretch=False)
        sb = ttk.Scrollbar(frame, orient='vertical', command=self.remote_tree.yview)
        self.remote_tree.configure(yscrollcommand=sb.set)
        self.remote_tree.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        return tab

    # ================================================================== helpers
    def _save_opts(self):
        self.settings['ps3_ip'] = self.ip_var.get().strip()
        self.settings['auto_covers'] = bool(self.auto_cover_var.get())
        self.settings['official_names'] = bool(self.official_var.get())
        self.settings['ps3_model'] = self.ps3_model
        if self.drive:
            self.settings['last_drive'] = self.drive.root
        save_settings(self.settings)

    def post(self, *msg):
        self.q.put(msg)

    def log(self, text, level=''):
        self.post('log', text, level)

    def _progress_cb(self, label):
        def cb(done, total):
            self.post('progress', done, total, f'{label}  {fmt_size(done)} / {fmt_size(total)}')
        return cb

    def selected_games(self) -> list[discinfo.GameInfo]:
        return [self.games[int(i)] for i in self.tree.selection()]

    def run_task(self, label, func, *args):
        if self.busy:
            messagebox.showinfo(APP_NAME, 'Please wait for the current task to finish (or cancel it).')
            return
        self.busy = True
        self.cancel.clear()
        self._set_busy(True, label)

        def target():
            try:
                func(*args)
            except library.Cancelled:
                self.log('Cancelled.', 'warn')
            except Exception as e:  # noqa: BLE001 - report everything to the log
                self.log(f'Error: {e}', 'err')
            finally:
                self.post('done')
        threading.Thread(target=target, daemon=True).start()

    def _set_busy(self, busy, label='Ready'):
        self.status_var.set(label)
        self.cancel_btn.configure(state='normal' if busy else 'disabled')
        for b in self.lib_buttons + self.net_buttons + self.kit_buttons + self.theme_buttons + [self.prep_btn]:
            b.configure(state='disabled' if busy else 'normal')
        if not busy:
            self.progress['value'] = 0
        self.busy = busy
        self._update_cover_buttons()
        self._store_buttons_state()

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == 'log':
                    self.log_box.configure(state='normal')
                    self.log_box.insert('end', msg[1] + '\n', msg[2])
                    self.log_box.see('end')
                    self.log_box.configure(state='disabled')
                elif kind == 'progress':
                    _, done, total, text = msg
                    self.progress['value'] = 1000 * done / max(total, 1)
                    self.status_var.set(text)
                elif kind == 'games':
                    self.games = msg[1]
                    self._fill_tree()
                elif kind == 'remote':
                    self._fill_remote(msg[1])
                elif kind == 'cover':
                    if msg[1] in self.games:
                        self._cover_changed(msg[1])
                elif kind == 'rescan':
                    self._pending_rescan = True
                elif kind == 'db':
                    self._fill_tree()
                elif kind == 'themes':
                    self.refresh_themes()
                elif kind == 'store':
                    self._store_results(*msg[1:])
                elif kind == 'store_error':
                    self._store_loading = False
                    self.store_count.set('')
                    self.store_more.configure(state='normal', text='Retry')
                    self.log(msg[1], 'err')
                elif kind == 'store_hd':
                    if msg[1] is self._store_sel and msg[2]:
                        self._show_store_image(msg[2])
                elif kind == 'call':  # run a function on the UI thread for a worker
                    _, func, box, done = msg
                    try:
                        box.append(func())
                    finally:
                        done.set()
                elif kind == 'done':
                    self.busy = False
                    self._set_busy(False)
                    self.refresh_drive_note()
                    self.refresh_kit()
                    if self._pending_rescan:
                        self._pending_rescan = False
                        self.rescan()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ================================================================== drives
    def refresh_drives(self):
        self.drives = library.list_drives()
        self.drive_box['values'] = [d.describe() for d in self.drives]
        want = self.drive.root if self.drive else self.settings.get('last_drive', '')
        idx = next((i for i, d in enumerate(self.drives) if d.root == want), None)
        if idx is None:
            idx = next((i for i, d in enumerate(self.drives) if d.kind != 'System'), None)
        if idx is not None:
            self.drive_box.current(idx)
            self.on_drive_selected()
        else:
            self.drive = None
            self.drive_var.set('')
            self.drive_note.configure(text='No drives found. Plug in a USB drive and press Refresh.', fg=C['warn'])

    def on_drive_selected(self):
        i = self.drive_box.current()
        if i < 0:
            return
        self.drive = self.drives[i]
        self._save_opts()
        self.refresh_drive_note()
        self.refresh_kit()
        self.refresh_themes()
        self.rescan()

    def call_in_ui(self, func):
        """From a worker thread: run func on the UI thread and return its result."""
        box, done = [], threading.Event()
        self.post('call', func, box, done)
        done.wait()
        return box[0] if box else None

    def refresh_drive_note(self):
        d = self.drive
        if not d:
            return
        fs = (d.fs or '').upper()
        missing = [x for x in library.ALL_DIRS if x != 'ROMS' and not os.path.isdir(os.path.join(d.root, x))]
        notes, color = [], C['muted']
        if d.kind == 'System':
            notes.append('This is your Windows system drive; pick your USB drive instead.')
            color = C['warn']
        if fs.startswith('FAT'):
            notes.append('FAT32: works with every game type. PS3 ISOs over 4 GB are split automatically.')
        elif fs == 'NTFS':
            notes.append('NTFS: ISO games only (no folder games). After adding games, run prepISO on the PS3.')
        elif fs == 'EXFAT':
            notes.append('exFAT: only supported by recent webMAN MOD builds. FAT32 or NTFS are safer.')
            color = C['warn']
        elif fs:
            notes.append(f'{d.fs}: the PS3 cannot read this format. Use FAT32 or NTFS.')
            color = C['err']
        if missing:
            notes.append(f'Missing webMAN folders: {", ".join(missing)}. Click "Prepare drive".')
            color = C['warn'] if color == C['muted'] else color
        try:
            import shutil
            d.free = shutil.disk_usage(d.root).free
        except OSError:
            pass
        self.drive_note.configure(text='   '.join(notes), fg=color)

    def on_prepare(self):
        if not self.drive:
            return
        if self.drive.kind == 'System' and not messagebox.askyesno(
                APP_NAME, 'This is your Windows system drive. Create the webMAN folders here anyway?'):
            return
        created = library.prepare_drive(self.drive.root)
        self.log(f'Created: {", ".join(created)}' if created else 'All webMAN folders already exist.', 'ok')
        self.refresh_drive_note()
        self.rescan()

    # ================================================================== library
    def rescan(self):
        if not self.drive:
            return
        if self.busy:
            self._pending_rescan = True
            return

        def task(root):
            games, errors = library.scan_drive(root)
            for p, e in errors:
                self.log(f'Skipped {os.path.basename(p)}: {e}', 'warn')
            self.post('games', games)
        self.run_task('Scanning drive...', task, self.drive.root)

    def _schedule_fill(self):
        if getattr(self, '_fill_job', None):
            self.after_cancel(self._fill_job)
        self._fill_job = self.after(200, self._fill_tree)

    def _fill_tree(self):
        self._fill_job = None
        keep = getattr(self, '_preview_game', None)
        keep_path = os.path.normcase(keep.path) if keep else None
        self.tree.delete(*self.tree.get_children())
        want = FILTERS.get(self.filter_var.get())
        query = self.search_var.get().strip().lower()
        shown = total_size = 0
        for i, g in enumerate(self.games):
            if want and g.system != want:
                continue
            if query and query not in f'{g.title} {g.game_id} {g.platform} {self._official_title(g)}'.lower():
                continue
            shown += 1
            total_size += g.size
            tags = ('warn',) if g.warnings else (g.system,)
            self.tree.insert('', 'end', iid=str(i), tags=tags, values=(
                system_label(g), g.title, g.display_id if g.system != 'RETRO' else '',
                g.fmt if g.system != 'RETRO' else retro.platform_name(g.platform), fmt_size(g.size),
                'yes' if g.cover else '-', self._game_notes(g)))
        self._fill_grid()
        reselect = next((str(i) for i, g in enumerate(self.games)
                         if keep_path and os.path.normcase(g.path) == keep_path
                         and self.tree.exists(str(i))), None)
        if reselect:
            self.tree.selection_set(reselect)
            self.tree.see(reselect)
            self.grid_view.see(reselect)
        else:
            self.show_preview()
        counts = {s: sum(1 for g in self.games if g.system == s) for s in ('PS3', 'PS2', 'PS1', 'RETRO')}
        self.count_var.set(f'{shown} shown  |  PS3 {counts["PS3"]}  PS2 {counts["PS2"]}  '
                           f'PS1 {counts["PS1"]}  Retro {counts["RETRO"]}  |  {fmt_size(total_size)}')
        self._draw_storage()

    def _fill_grid(self):
        items = []
        for iid in self.tree.get_children():
            g = self.games[int(iid)]
            sub = retro.platform_name(g.platform) if g.system == 'RETRO' else f'{g.system}  {g.game_id}'
            items.append((iid, g.title, sub, g.cover, SYSTEM_COLORS.get(g.system, C['muted'])))
        self.grid_view.set_items(items)
        self.grid_view.set_selection(self.tree.selection())

    def _check_drive_ready(self) -> bool:
        if not self.drive:
            messagebox.showwarning(APP_NAME, 'Select a drive first.')
            return False
        if any(not os.path.isdir(os.path.join(self.drive.root, x)) for x in library.ALL_DIRS if x != 'ROMS'):
            if messagebox.askyesno(APP_NAME, 'This drive is missing the webMAN folders. Create them now?'):
                library.prepare_drive(self.drive.root)
                self.refresh_drive_note()
            else:
                return False
        return True

    def on_add_files(self):
        if not self._check_drive_ready():
            return
        rom_types = ' '.join('*' + e for e in retro.ROM_EXTS)
        paths = filedialog.askopenfilenames(title='Choose games', filetypes=[
            ('All supported games', '*.iso *.cue *.bin *.img *.0 ' + rom_types),
            ('PlayStation images', '*.iso *.cue *.bin *.img *.0'),
            ('Retro ROMs', rom_types), ('All files', '*.*')])
        if paths:
            self.run_task('Adding games...', self._import_task, list(paths))

    def on_add_folder(self):
        if not self._check_drive_ready():
            return
        folder = filedialog.askdirectory(title='Choose a PS3 game folder, or a folder full of games')
        if folder:
            def task():
                self.log(f'Searching {folder} ...', 'muted')
                found = discinfo.find_candidates(folder)
                if not found:
                    self.log('No games found in that folder.', 'warn')
                    return
                self._import_task(found)
            self.run_task('Adding games...', task)

    def _ask_platform(self, paths) -> str | None:
        """UI thread: ask which retro system some unrecognised ROMs belong to."""
        win = tk.Toplevel(self, bg=C['panel'], padx=18, pady=16)
        win.title('Which system?')
        win.transient(self)
        win.resizable(False, False)
        names = [os.path.basename(p) for p in paths]
        listing = '\n'.join(names[:8]) + (f'\n...and {len(names) - 8} more' if len(names) > 8 else '')
        ttk.Label(win, style='Panel.TLabel', justify='left', wraplength=420,
                  text=f'Could not tell which console these {len(paths)} file(s) are for:').pack(anchor='w')
        ttk.Label(win, style='PanelMuted.TLabel', justify='left', text=listing).pack(anchor='w', pady=(6, 10))
        choices = [f'{p.name}  ({p.folder})' for p in retro.PLATFORMS]
        var = tk.StringVar(value=choices[-2])  # FBNEO: most unrecognised .zip files are arcade sets
        ttk.Combobox(win, textvariable=var, values=choices, state='readonly', width=44).pack(anchor='w')
        result = {}
        row = ttk.Frame(win, style='Panel.TFrame')
        row.pack(fill='x', pady=(14, 0))
        ttk.Button(row, text='Add them', style='Accent.TButton',
                   command=lambda: (result.update(v=var.get()), win.destroy())).pack(side='left')
        ttk.Button(row, text='Skip these', command=win.destroy).pack(side='left', padx=6)
        win.grab_set()
        self.wait_window(win)
        if 'v' not in result:
            return None
        return retro.PLATFORMS[choices.index(result['v'])].folder

    def _import_task(self, paths):
        drive = self.drive
        infos, errors, needs = library.identify_many(paths)
        if needs:
            plat = self.call_in_ui(lambda: self._ask_platform(needs))
            if plat:
                more, more_err, _ = library.identify_many(needs, {os.path.abspath(p): plat for p in needs})
                infos += more
                errors += more_err
            else:
                self.log(f'Skipped {len(needs)} file(s) with an unknown system.', 'warn')
        for p, e in errors:
            self.log(f'Skipped {os.path.basename(p)}: {e}', 'warn')
        use_official = self.settings.get('official_names', True)
        added = 0
        for n, info in enumerate(infos, 1):
            official = self._official_title(info)
            if use_official and official:
                info.title = official
            label = retro.platform_name(info.platform) if info.system == 'RETRO' else \
                f'{info.system}, {info.display_id}, {info.fmt}'
            self.log(f'[{n}/{len(infos)}] {info.title}  ({label}, {fmt_size(info.size)})')
            for w in info.warnings:
                self.log(f'  ! {w}', 'warn')
            try:
                new = library.import_game(info, drive.root, drive.fs, self._progress_cb(f'Copying {info.title}'),
                                          self.cancel)
            except library.ImportProblem as e:
                self.log(f'  Not copied: {e}', 'err')
                continue
            added += 1
            self.log(f'  Copied to {new.path}', 'ok')
            if self.settings.get('auto_covers') and not new.cover:
                self._get_cover(new)
        self.log(f'Done: {added} of {len(infos)} game(s) added.', 'ok' if added else 'warn')
        if added and (drive.fs or '').upper() == 'NTFS':
            self.log('Reminder: run prepISO on the PS3 so webMAN can see the new games.', 'warn')
        if any(i.system == 'RETRO' for i in infos) and added:
            self.log('Retro games play through RetroArch, which must be installed on the PS3.', 'muted')
        self.post('rescan')

    def _get_cover(self, g) -> bool:
        base = library.cover_base(g)
        if not base or (not g.game_id and g.system != 'RETRO'):
            return False
        path = covers.fetch_cover(g, base, self.settings.get('cover_templates'))
        if path:
            self.log(f'  Cover saved: {os.path.basename(path)}', 'ok')
            return True
        self.log(f'  No cover found online for {g.game_id or g.title}', 'muted')
        return False

    def on_fix_names(self):
        pool = self.selected_games() or self.games
        todo = [(g, self._official_title(g)) for g in pool
                if g.system in ('PS1', 'PS2') and self._official_title(g) and self._official_title(g) != g.title]
        if not todo:
            msg = ('All PS1/PS2 games already use their official names.' if self.db.ready else
                   'The game databases are still downloading. Try again in a moment.')
            messagebox.showinfo(APP_NAME, msg)
            return
        preview = '\n'.join(f'  {g.title}  ->  {t}' for g, t in todo[:10]) + (
            f'\n  ...and {len(todo) - 10} more' if len(todo) > 10 else '')
        if not messagebox.askyesno(APP_NAME, f'Rename {len(todo)} game(s) to their official names?\n\n{preview}'):
            return

        def task():
            done = 0
            for g, title in todo:
                try:
                    library.rename_game(g, title)
                    done += 1
                except (library.ImportProblem, OSError) as e:
                    self.log(f'Could not rename {g.title}: {e}', 'warn')
            self.log(f'Renamed {done} game(s).', 'ok')
            self.post('rescan')
        self.run_task('Renaming...', task)

    def on_covers(self):
        todo = [g for g in (self.selected_games() or self.games) if not g.cover and library.cover_base(g)]
        if not todo:
            self.log('Every game already has a cover.', 'ok')
            return

        def task():
            got = 0
            for n, g in enumerate(todo, 1):
                if self.cancel.is_set():
                    raise library.Cancelled()
                self.post('progress', n - 1, len(todo), f'Covers {n}/{len(todo)}: {g.title}')
                got += self._get_cover(g)
            self.log(f'Downloaded {got} of {len(todo)} cover(s).', 'ok')
            self.post('rescan')
        self.run_task('Downloading covers...', task)

    def on_remove(self):
        sel = self.selected_games()
        if not sel:
            return
        names = '\n'.join(f'  {g.title}' for g in sel[:12]) + ('\n  ...' if len(sel) > 12 else '')
        if not messagebox.askyesno(APP_NAME, f'Permanently delete {len(sel)} game(s) from the drive?\n\n{names}',
                                   icon='warning'):
            return

        def task():
            for g in sel:
                library.remove_game(g)
                self.log(f'Removed {g.title}', 'ok')
            self.post('rescan')
        self.run_task('Removing...', task)

    def on_explore(self):
        sel = self.selected_games()
        if sel:
            os.startfile(os.path.dirname(sel[0].path))
        elif self.drive:
            os.startfile(self.drive.root)

    # ================================================================== PS3
    def _client(self):
        self._save_opts()
        try:
            return ps3net.PS3Client(self.ip_var.get())
        except ValueError as e:
            messagebox.showwarning(APP_NAME, str(e))
            return None

    def on_test(self):
        c = self._client()
        if not c:
            return

        def task():
            c.test()
            self.log(f'Connected to webMAN at {c.ip}.', 'ok')
            c.popup('PS3 Drive Manager connected')
        self.run_task('Connecting...', task)

    def on_send(self):
        sel = self.selected_games()
        if not sel:
            messagebox.showinfo(APP_NAME, 'Select the games to send in the Drive library tab first.')
            return
        c = self._client()
        if not c:
            return

        def task():
            sent = 0
            for n, g in enumerate(sel, 1):
                self.log(f'[{n}/{len(sel)}] Sending {g.title} ...')
                if c.upload_game(g, self._progress_cb(f'Sending {g.title}'), self.cancel, self.log):
                    sent += 1
                    self.log('  Sent.', 'ok')
            if sent:
                self.log('Refreshing webMAN game list...', 'muted')
                try:
                    c.refresh()
                    c.popup(f'{sent} game(s) added')
                except Exception as e:  # noqa: BLE001
                    self.log(f'  Could not refresh automatically ({e}); refresh from webMAN instead.', 'warn')
            self.log(f'Done: {sent} game(s) sent.', 'ok')
        self.run_task('Sending to PS3...', task)

    def on_list_remote(self):
        c = self._client()
        if c:
            self.run_task('Reading PS3 games...', lambda: self.post('remote', c.list_games()))

    def on_refresh_webman(self):
        c = self._client()
        if c:
            def task():
                c.refresh()
                self.log('webMAN game list refreshed.', 'ok')
            self.run_task('Refreshing webMAN...', task)

    def _fill_remote(self, listing: dict):
        self.remote_tree.delete(*self.remote_tree.get_children())
        for folder, names in listing.items():
            node = self.remote_tree.insert('', 'end', text=f'{folder}  ({len(names)})', open=True)
            for n in names:
                if not n.lower().endswith(('.jpg', '.png')):
                    self.remote_tree.insert(node, 'end', text='', values=(n,))

    def _on_close(self):
        if self.busy and not messagebox.askyesno(APP_NAME, 'A task is still running. Quit anyway?'):
            return
        self.cancel.set()
        self._save_opts()
        self.destroy()


if __name__ == '__main__':
    App().mainloop()
