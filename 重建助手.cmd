@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title study_agent - rebuild helper

echo ============================================================
echo  study_agent rebuild helper  (double-click to run)
echo ============================================================
echo.

REM ---- 1. find a python ----
set "PY="
if exist "%~dp0venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
if not defined PY (
  where py >nul 2>nul
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY goto :nopython
echo [1] python: %PY%
%PY% -c "import sys;print('    version',sys.version.split()[0])"
echo.

REM ---- 2. build dashboard from rebuilt chunks ----
echo [2] assemble dashboard.py from the 170 AI-rebuilt function blocks
if not exist "D:\harness\rescue\build_dashboard.py" goto :nobuild
%PY% -X utf8 "D:\harness\rescue\build_dashboard.py" --dry-run
echo.
echo Press any key to run it for real (or close this window to stop)...
pause >nul
%PY% -X utf8 "D:\harness\rescue\build_dashboard.py"
echo.
echo     output: D:\harness\rescue\_work\built\dashboard.py
echo     only replace the source-tree dashboard.py if py_compile passed above
echo.

REM ---- 3. env materials ----
echo [3] copy model / prompts into the source tree, then health check
if not exist "D:\harness\rescue\setup_env.py" goto :noenv
%PY% -X utf8 "D:\harness\rescue\setup_env.py"
echo.

REM ---- 4. venv ----
echo [4] virtual environment
if exist "%~dp0venv\Scripts\python.exe" goto :havvenv
echo     not found - creating now (may take several minutes)...
%PY% -m venv "%~dp0venv"
if not exist "%~dp0venv\Scripts\python.exe" goto :novenv
"%~dp0venv\Scripts\python.exe" -m pip install -U pip
"%~dp0venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
goto :aftervenv
:havvenv
echo     already exists: %~dp0venv
:aftervenv
echo.

REM ---- 5. syntax check ----
echo [5] syntax check (core and tools)
"%~dp0venv\Scripts\python.exe" -m compileall -q "%~dp0core" "%~dp0tools"
echo     files listed above still have syntax errors; no output means all passed
echo.

REM ---- 6. next ----
echo ============================================================
echo  Next: double-click kanban.cmd (the Chinese-named one) to start.
echo  URL: http://127.0.0.1:8756/
echo  If it fails, send the last 20 lines of that window to the AI.
echo ============================================================
pause
exit /b 0

:nopython
echo [X] No Python found. Install Python 3.13 with "Add to PATH", or run install.cmd.
echo     Download: https://www.python.org/downloads/
pause
exit /b 1

:nobuild
echo [X] Missing D:\harness\rescue\build_dashboard.py
echo     The rescue workspace is not at D:\harness\rescue - edit the paths in this file.
pause
exit /b 1

:noenv
echo [X] Missing D:\harness\rescue\setup_env.py  (same as above)
pause
exit /b 1

:novenv
echo [X] venv creation failed. Run install.cmd instead, or manually:
echo     %PY% -m venv "%~dp0venv"
pause
exit /b 1
