@echo off
rem ============================================================
rem  BestdoriHelper - desktop GUI launcher
rem  Double-click this file to open the GUI.
rem  (ASCII only on purpose: cmd.exe mangles non-ASCII in .bat)
rem ============================================================
setlocal

cd /d "%~dp0"

set "BDH_PY=C:\Users\Frankieson\.workbuddy-ai\binaries\python\envs\bestdori\Scripts\python.exe"
if not exist "%BDH_PY%" set "BDH_PY=python"

set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

"%BDH_PY%" -m bestdori_helper.gui %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo [ERROR] BestdoriHelper GUI exited with code %RC%
    echo         Make sure PySide6 is installed:
    echo             "%BDH_PY%" -m pip install PySide6
    echo.
    pause
)

endlocal
