@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem --- find a python.exe: workbuddy bundled first, then standard dirs, then PATH ---
set "PY="
for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
    if exist "%%D\python.exe" if not defined PY set "PY=%%D\python.exe"
)
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\*") do (
    if exist "%%D\python.exe" if not defined PY set "PY=%%D\python.exe"
)
if not defined PY (
    where python > "%TEMP%\DeskSwitch_py.txt" 2>nul
    if exist "%TEMP%\DeskSwitch_py.txt" set /p PY=<"%TEMP%\DeskSwitch_py.txt"
    del "%TEMP%\DeskSwitch_py.txt" 2>nul
)
if not defined PY set "PY=python"

echo Using interpreter: %PY%
"%PY%" widget.py
pause
