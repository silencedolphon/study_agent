REM Copyright (c) 2026 <2227663973@qq.com>. All rights reserved.
REM Proprietary - authorized testing only. No copying, distribution
REM or publication without written permission.
REM File-ID: SA-23-ed8aa02b   (project study_agent; keep this line)
@echo off
setlocal
title study_agent - Study Dashboard

REM ============================================================
REM  Study Dashboard launcher.  Double-click this file to open.
REM  Default URL: http://127.0.0.1:8757/
REM  Closing this window stops the dashboard.
REM
REM  NOTE 1: keep this file ASCII-only.  cmd.exe parses .cmd files with the
REM  system OEM code page - GBK on Chinese Windows - so UTF-8 Chinese text
REM  here gets mis-decoded byte-by-byte and corrupts command parsing.
REM
REM  NOTE 2: avoid round brackets inside if-blocks.  cmd treats a bracket in
REM  a REM line as a block boundary and breaks the whole block.
REM
REM  NOTE 3: port detection is delegated to PowerShell on purpose.
REM  netstat's state column is LOCALIZED - on this machine it is Chinese, not
REM  "LISTENING" - so matching that text never matches and every double-click
REM  tried to start a second instance.  The check below keys on the
REM  local-address field only, which is locale-independent.
REM
REM  NOTE 4: every path below is derived from %~dp0, the folder holding this
REM  script, so the project keeps working after it is moved or unzipped
REM  anywhere.  %~dp0 already ends with a backslash - never add a second
REM  one, and never hard-code a drive letter here.
REM ============================================================

set "ROOT=%~dp0"
set "PY=%ROOT%venv\Scripts\python.exe"
set "APP=%ROOT%tools\dashboard.py"
REM Optional: set SA_PORT before calling this file to use another port.
if not defined SA_PORT set "SA_PORT=8757"
set "PORT=%SA_PORT%"

REM 2026-11-03: local proxy 127.0.0.1:7897 can fail TLS handshake for DeepSeek.
REM Bypass the proxy for api.deepseek.com only; direct HTTPS is reachable.
if not defined NO_PROXY set "NO_PROXY=api.deepseek.com"
if not defined no_proxy set "no_proxy=api.deepseek.com"

if not exist "%PY%" (
    echo.
    echo   [ERROR] Python interpreter not found:
    echo           %PY%
    echo.
    echo   The virtual environment is missing or incomplete, or this folder
    echo   was moved on its own without the venv folder next to it.
    echo.
    echo   Fix it by running the installer that sits next to this file:
    echo           %ROOT%install.cmd
    echo.
    pause
    exit /b 1
)

if not exist "%APP%" (
    echo.
    echo   [ERROR] Dashboard program not found:
    echo           %APP%
    echo.
    echo   The download looks incomplete.  Unzip the package again keeping
    echo   the folder structure intact.
    echo.
    pause
    exit /b 1
)

REM Exit code 0 means the port is already listening.
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -State Listen -LocalPort %PORT% -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo   Port %PORT% is already in use. The dashboard is probably already running.
    echo   Opening: http://127.0.0.1:%PORT%/
    echo.
    echo   To run a second instance on another port:
    echo     "%PY%" "%APP%" --port 8758
    echo.
    start "" "http://127.0.0.1:%PORT%/"
    echo   Press any key to close this window. The running dashboard is unaffected.
    pause >nul
    exit /b 0
)

echo.
echo   Starting the study dashboard...
echo   URL: http://127.0.0.1:%PORT%/
echo.
echo   Your browser will open automatically.
echo   Close this window to stop the dashboard.
echo   --------------------------------------------------
echo.

REM dashboard.py opens the browser itself.
"%PY%" -X utf8 "%APP%" --port %PORT%

echo.
echo   Dashboard stopped.
pause
endlocal
