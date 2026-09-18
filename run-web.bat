@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ---------------------------------------------------------------
REM  启动网页版（docs/ 是纯静态站点）
REM  需要 Python 3 —— 只用到标准库，不装任何东西也能跑。
REM  双击本文件即可，浏览器会自动打开；关掉这个黑窗口就停止服务。
REM ---------------------------------------------------------------

set PY=
where python >nul 2>&1 && set PY=python
if "%PY%"=="" (
  where py >nul 2>&1 && set PY=py -3
)

if "%PY%"=="" (
  echo.
  echo   没有找到 Python。请先安装 Python 3 并勾选 "Add to PATH"，
  echo   或者用任意静态服务器打开 docs 目录也可以（网页版是纯静态的）。
  echo.
  pause
  exit /b 1
)

%PY% scripts\serve_web.py
echo.
pause
