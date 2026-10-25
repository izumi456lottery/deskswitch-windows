@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 自动在 PATH 里找 python.exe（找不到就退化成裸命令）
set "PY="
where python > "%TEMP%\DeskSwitch_py.txt" 2>nul
if exist "%TEMP%\DeskSwitch_py.txt" set /p PY=<"%TEMP%\DeskSwitch_py.txt"
del "%TEMP%\DeskSwitch_py.txt" 2>nul
if not defined PY set "PY=python"

echo 使用解释器：%PY%
"%PY%" widget.py
pause
