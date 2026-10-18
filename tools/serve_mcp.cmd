@echo off
REM study_agent MCP server launcher.
REM The .cmd wrapper exists so the DSH config command is a simple path with no
REM spaces, avoiding YAML quoting/escaping problems for paths that have spaces.
REM NOTE: stdout is the MCP protocol channel - never print anything else there.
REM Keep this file pure ASCII: cmd.exe parses .cmd as GBK, so non-ASCII bytes in
REM comments get misread and fragments can be executed as bogus commands.
"D:\study_agent\venv\Scripts\python.exe" -X utf8 "D:\study_agent\tools\serve_mcp.py" %*