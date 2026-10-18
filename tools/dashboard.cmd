@echo off
REM Force UTF-8 first: cmd.exe decodes .cmd files with the console code page --
REM GBK on this machine -- which would mangle the Chinese target name below.
chcp 65001 >nul
REM Same as the root-level kanban launcher in the project root.
REM Kept here so it can be invoked directly from tools\.
REM The real startup logic lives in the root-level copy, to avoid maintaining two.
REM NOTE: the path is built from %~dp0, the folder holding this script, so the
REM project keeps working after it is moved or unzipped anywhere.
call "%~dp0..\看板.cmd" %*