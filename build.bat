@echo off
rem Builds dist\M2Monitor.exe, a single-file Windows app.
rem Needs the packages from requirements.txt plus: python -m pip install pyinstaller
rem assets\icon.ico becomes the .exe icon and is bundled for the window icon.
set ICON=
if exist assets\icon.ico set ICON=--icon assets\icon.ico --add-data "assets\icon.ico;assets"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name M2Monitor %ICON% ^
    --collect-data customtkinter ^
    --collect-all windows_capture ^
    --collect-all winrt ^
    m2monitor.py
