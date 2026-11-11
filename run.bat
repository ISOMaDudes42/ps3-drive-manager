@echo off
rem Runs from source. Prefers the build environment (has Pillow for JPG cover previews).
cd /d "%~dp0"
if exist .venv\Scripts\pythonw.exe (
    start "" .venv\Scripts\pythonw.exe app.py
) else (
    start "" pythonw app.py
)
