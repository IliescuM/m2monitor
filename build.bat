@echo off
rem Builds dist\M2Monitor.exe, a single-file Windows app.
rem Needs the packages from requirements.txt plus: python -m pip install pyinstaller
rem Put an icon at assets\icon.ico to give the .exe its own icon.
set ICON=
if exist assets\icon.ico set ICON=--icon assets\icon.ico
python -m PyInstaller --noconfirm --clean --onefile --windowed --name M2Monitor %ICON% ^
    --collect-data customtkinter ^
    --collect-all windows_capture ^
    --collect-all winrt ^
    m2monitor.py
