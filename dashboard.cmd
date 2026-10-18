@echo off
REM ASCII-named launcher for the study dashboard.
REM
REM The real start-up logic lives in the Chinese-named launcher that sits
REM next to this file; this alias only exists so the package is usable on
REM systems whose locale cannot type or display that file name.
REM
REM NOTE: chcp 65001 must run BEFORE the call below.  cmd.exe decodes .cmd
REM files with the console code page - GBK on Chinese Windows - so without
REM it the UTF-8 file name here would be mis-decoded byte-by-byte and the
REM call would fail.  Set SA_PORT before calling to use another port.
REM
REM All paths are derived from %~dp0, so the folder can be moved anywhere.
chcp 65001 >nul
call "%~dp0看板.cmd" %*
