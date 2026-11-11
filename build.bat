@echo off
rem Rebuilds dist\PS3DriveManager.exe from the Python source.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe python -m venv .venv
.venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check pyinstaller pillow tkinterdnd2
.venv\Scripts\pyinstaller.exe --noconfirm --clean --onefile --windowed --name PS3DriveManager --collect-all tkinterdnd2 --icon assets\icon.ico --add-data "assets\icon.ico;assets" app.py
echo.
echo Built: %~dp0dist\PS3DriveManager.exe
pause
