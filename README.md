<p align="center"><img src="assets/icon.png" width="160" alt="PS3 Drive Manager logo"></p>

# PS3 Drive Manager

A Windows app for setting up a modded PS3 and managing your own game backups for **webMAN MOD**, in the spirit of TinyWiiBackupManager for the Wii. It prepares USB drives, builds a ready-to-use mod kit (firmware, HEN guide and homebrew packages), organises PS3, PS2, PS1 and retro games with cover art, and installs community XMB themes.

## Download
Get `PS3DriveManager.exe` from the [latest release](../../releases/latest) and run it. Nothing else needs to be installed.

Windows SmartScreen may warn about it the first time, because the `.exe` isn't digitally signed. Click **More info → Run anyway**.

## Run from source
You need Python 3.10 or newer on Windows. Then run:
```
pip install -r requirements.txt
python app.py
```
Both packages are optional. Pillow shows JPG and WebP covers and theme previews, and tkinterdnd2 adds drag and drop. Without them, the app still runs with fewer features.

To build the `.exe` yourself, run `build.bat`. It sets up a virtual environment and builds `dist\PS3DriveManager.exe` with PyInstaller.

## Mod kit (set up a USB that's ready to mod the PS3)
The drive must be **FAT32**, because the PS3 system menu can't read other formats.
1. **Firmware.** Choose HFW + HEN (works on every model) or CFW (compatible consoles only). The app opens the official download guide. Then pick the `.PUP` file you downloaded with **Add firmware file...**. The app checks it's a real PS3 firmware file, reads its version, copies it to `PS3\UPDATE\PS3UPDAT.PUP`, reads the copy back to verify it, and shows the MD5 so you can compare it with the download page.
2. **Packages.** It downloads webMAN MOD, prepISO, IRISMAN and, optionally, the PS2 Classics Launcher and PS2 configs from their official GitHub releases. Each download is checked for size, checksum and package header, then placed in the drive's root folder. Older versions on the drive are replaced.
3. **Build mod kit.** This writes `PS3_MOD_GUIDE.txt` to the drive, with step-by-step instructions for installing HFW, then HEN, then the packages.

PS3HEN itself is installed from the PS3's web browser after HFW is on the console. The guide walks through it.

The firmware check assumes the standard PUP layout. It has been tested with generated files, not a real firmware download.

## What it does
- **Prepares a drive.** It creates the `PS3ISO`, `PS2ISO`, `PSXISO` and `GAMES` folders that webMAN scans.
- **Adds games.** It reads each disc image to tell PS3, PS2 and PS1 games apart and to find the title and game ID. It names the file `Title [ID]` and copies it to the right folder.
  - Formats it accepts: PS3 `.iso`, split `.iso.0`, and game folders (`PS3_GAME\PARAM.SFO`); PS2 `.iso`; PS1 `.bin/.cue`, `.iso` and `.img`.
  - `Add folder...` searches a whole folder for games.
- **Handles FAT32.** PS3 ISOs larger than 4 GB are split into `.iso.0`, `.iso.1` and so on, which webMAN loads as one game.
- **Downloads covers.** Box art is saved next to each game file, which is where webMAN looks for it. It comes from GameTDB for PS3 and from the xlenore cover repositories on GitHub for PS1 and PS2.
- **Sends games to the PS3.** It uploads games over webMAN's FTP server to the internal HDD (`/dev_hdd0`), then refreshes webMAN's game list. Split ISOs are joined back into a single file on the PS3.

## Library
- **Grid or list view.** Switch with the buttons at the top right. Search matches title, game ID or system, and the **Show** filter limits the view to PS3, PS2, PS1 or Retro.
- **Storage bar.** Shows how much space each system's games use, plus free space.
- **Drag and drop.** Drop games, folders or `.p3t` themes from Explorer onto the window.
- **Right-click menu.** Show the game in Explorer, re-download its cover, use its official name, or remove it.
- **Official names.** PS1 and PS2 discs don't store a title, so the app looks it up from the game ID. PS2 names come from PCSX2's game database and PS1 names from DuckStation's. New games are named this way when added, and **Fix names** renames games already on the drive. The databases are downloaded once and refreshed every three weeks.
- **PS2 compatibility hints.** Set **My PS3** to your model. For each PS2 game, the app then shows whether webMAN MOD has a compatibility fix for it, using the PS2CONFIG data in webMAN's repository. The preview panel also links to the online compatibility list.

## Retro games
ROMs are copied to `ROMS\<SYSTEM>`, using the folder names webMAN MOD expects (`NES`, `SNES`, `GB`, `GBC`, `GBA`, `N64`, `GENESIS`, `MASTER`, `GG`, `PCE`, `NGPC`, `ATARI2600`, `LYNX`, `FBNEO`, `MAME` and more). The system is detected from the file extension, from inside `.zip` files, or from the Sega header in Genesis `.bin` files. When it can't be detected, for example with arcade sets, the app asks. Covers come from libretro's thumbnail server and match by ROM file name, so No-Intro names work best.

webMAN launches retro games through **RetroArch**, which has to be installed on the PS3 separately. Libretro doesn't publish PS3 builds anymore, so the app doesn't download it.

## Themes
- **Browse themes.** Search more than 10,000 community PS3 themes from [Themezer](https://themezer.net/ps3/themes), with preview pictures. You can sort by most downloaded, trending, newest or most saved, and filter to animated themes or themes with custom icons or sounds. Adult themes are always filtered out. Select a theme to see a large preview, its creator, download count and description. **Add to drive** (or double-click) downloads the `.p3t` file, checks it's a real PS3 theme, and saves it to `PS3\THEME`.
- **On this drive.** Lists the themes already on the drive. Themes added from Themezer show their Themezer preview. For other themes, the app tries to find a picture inside the theme file. You can also add `.p3t` files from your PC or drag them onto the window.

On the PS3, go to Settings > Theme Settings > Theme and pick the theme from the USB drive. The drive must be FAT32.

## Which drive format to use
| Format | Notes |
|---|---|
| FAT32 | Works with every game type. Large PS3 ISOs are split automatically. |
| NTFS  | ISO games only. Run **prepISO** (formerly prepNTFS) on the PS3 after adding games. |
| exFAT | Only works with recent webMAN MOD builds. |

## Settings
Settings are saved in `%APPDATA%\PS3DriveManager\settings.json`. They hold the PS3's IP address, the automatic-cover option, and the cover download URLs. If a cover source moves, you can edit its URL there. In those URLs, `{id}` stands for the game ID and `{region}` for the GameTDB region code.

## Files
- `app.py` is the user interface.
- `gridview.py` is the cover-art grid used by the library and the theme browser.
- `discinfo.py` reads ISO9660 disc images, PARAM.SFO and SYSTEM.CNF files to identify games.
- `library.py` handles drive layout, scanning, importing, splitting, renaming and removal.
- `retro.py` detects retro ROM systems and maps them to webMAN's `ROMS` folders.
- `gamedb.py` looks up official PS1 and PS2 titles and PS2 compatibility fixes.
- `covers.py` downloads cover art.
- `themes.py` and `theme_store.py` manage themes on the drive and browse Themezer.
- `modkit.py` handles firmware checking, GitHub package downloads and the guide.
- `ps3net.py` talks to webMAN over HTTP (refresh, popups) and FTP (uploads, listing).

## Credits and data sources
This app downloads data from these projects at runtime. None of their data is bundled.
- [webMAN MOD](https://github.com/aldostools/webMAN-MOD), [IRISMAN](https://github.com/aldostools/IRISMAN) and prepISO by aldostools. PS2 compatibility fixes come from webMAN MOD's PS2CONFIG data.
- [PS3HEN](https://github.com/PS3Xploit/PS3HEN) by the PS3Xploit team.
- PS2 titles from [PCSX2](https://github.com/PCSX2/pcsx2)'s GameIndex, and PS1 titles from [DuckStation](https://github.com/stenzek/duckstation)'s game database.
- Covers from [GameTDB](https://www.gametdb.com/), the [xlenore PS1 and PS2 cover repositories](https://github.com/xlenore), and [libretro-thumbnails](https://github.com/libretro-thumbnails).
- Themes from [Themezer](https://themezer.net/ps3/themes) and their creators.

## Disclaimer
Only use this with backups of games you own. Installing HFW, HEN or CFW is at your own risk. Always follow the official guides, and for CFW, keep a verified NOR/NAND dump. Don't use PSN while modded. This project isn't affiliated with Sony or with any of the projects above.

## License
[MIT](LICENSE)
