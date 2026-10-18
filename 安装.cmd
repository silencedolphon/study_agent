@echo off
REM ASCII-only alias for install.cmd, for users who prefer to double-click a
REM Chinese-named entry point.  The real installer logic lives in install.cmd
REM next to this file; this wrapper only forwards the arguments.
call "%~dp0install.cmd" %*
