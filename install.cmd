@echo off
setlocal
title study_agent - one-click installer

REM ============================================================
REM  study_agent - one-click installer for Windows.
REM
REM  Double-click this file. It prepares everything the dashboard
REM  needs, in this order:
REM    1. find a suitable Python interpreter
REM    2. create the project-local virtual environment venv
REM    3. install the dependencies listed in requirements.txt
REM    4. download the local embedding model, about 100 MB, once
REM    5. create the runtime folders
REM    6. run the environment self-check
REM
REM  Running it again is safe: every step skips what is already
REM  done, and nothing outside this project folder is touched.
REM
REM  Options:
REM    --check-only         only run the self-check, install nothing
REM    --no-pause           do not wait for a key press at the end
REM    --no-mirror          ignore the bundled pip.ini, use the default index
REM    --skip-model         do not download the embedding model
REM    --skip-deps          do not install dependencies nor run the self-check
REM    --no-pip-upgrade     do not upgrade pip inside a freshly created venv
REM    --requirements FILE  use another dependency list
REM    --python PATH        use this interpreter to create the venv
REM    --help               show this help
REM
REM  NOTE 1: keep this file ASCII-only.  cmd.exe parses .cmd files with the
REM  system OEM code page - GBK on Chinese Windows - so UTF-8 Chinese text
REM  here gets mis-decoded byte-by-byte and corrupts command parsing.
REM
REM  NOTE 2: avoid round brackets inside if-blocks.  cmd treats a bracket in
REM  a REM line as a block boundary and breaks the whole block.  The steps
REM  below therefore branch with goto instead of parenthesised if-blocks.
REM
REM  NOTE 3: every path below is derived from %~dp0, the folder holding
REM  this script, so the package keeps working after it is unzipped or
REM  copied anywhere.  %~dp0 already ends with a backslash - never add a
REM  second one, and never hard-code a drive letter here.
REM ============================================================

set "ROOT=%~dp0"
set "ROOTDIR=%ROOT:~0,-1%"
set "VENVDIR=%ROOT%venv"
set "PY=%ROOT%venv\Scripts\python.exe"
set "REQ=%ROOT%requirements.txt"
set "DEFAULT_MODEL=%ROOT%models\bge-small-zh-v1.5\onnx\model_quantized.onnx"
set "MINPY=3.12"

set "PAUSE_AT_END=1"
set "SKIP_MODEL="
set "SKIP_DEPS="
set "NO_PIP_UPGRADE="
set "CHECK_ONLY="
set "NO_MIRROR="
set "USER_PY="
set "PYCMD="
set "RC=0"

REM ------------------------------------------------------------
REM  Command line
REM ------------------------------------------------------------
:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--help" goto usage
if /i "%~1"=="-h" goto usage
if /i "%~1"=="--check-only" goto opt_check
if /i "%~1"=="--no-pause" goto opt_nopause
if /i "%~1"=="--no-mirror" goto opt_nomirror
if /i "%~1"=="--skip-model" goto opt_skipmodel
if /i "%~1"=="--skip-deps" goto opt_skipdeps
if /i "%~1"=="--no-pip-upgrade" goto opt_nopipup
if /i "%~1"=="--requirements" goto opt_req
if /i "%~1"=="--python" goto opt_python
echo [ERROR] Unknown option: %~1
echo.
goto usage_bad

:opt_check
set "CHECK_ONLY=1"
shift
goto parse

:opt_nopause
set "PAUSE_AT_END="
shift
goto parse

:opt_nomirror
set "NO_MIRROR=1"
shift
goto parse

:opt_skipmodel
set "SKIP_MODEL=1"
shift
goto parse

:opt_skipdeps
set "SKIP_DEPS=1"
shift
goto parse

:opt_nopipup
set "NO_PIP_UPGRADE=1"
shift
goto parse

:opt_req
if "%~2"=="" goto missing_value
set "REQ=%~2"
shift
shift
goto parse

:opt_python
if "%~2"=="" goto missing_value
set "USER_PY=%~2"
shift
shift
goto parse

:missing_value
echo [ERROR] %~1 needs a value, for example:
echo             install.cmd %~1 "some value"
echo.
goto usage_bad

:usage
echo study_agent - one-click installer
echo.
echo   install.cmd [options]
echo.
echo   --check-only         only run the self-check, install nothing
echo   --no-pause           do not wait for a key press at the end
echo   --no-mirror          ignore the bundled pip.ini, use the default index
echo   --skip-model         do not download the embedding model
echo   --skip-deps          do not install dependencies nor run the self-check
echo   --no-pip-upgrade     do not upgrade pip inside a freshly created venv
echo   --requirements FILE  use another dependency list
echo   --python PATH        use this interpreter to create the venv
echo   --help               show this help
echo.
echo   Double-clicking this file runs the full setup.
echo.
exit /b 0

:usage_bad
echo   install.cmd --help   prints the full option list.
exit /b 1

REM ------------------------------------------------------------
REM  Start
REM ------------------------------------------------------------
:parsed
echo ============================================================
echo  study_agent - one-click installer
echo ============================================================
echo  Project folder: %ROOTDIR%
echo.
pushd "%ROOTDIR%"

if defined CHECK_ONLY goto check_only

REM ------------------------------------------------------------
REM  1. Python interpreter
REM ------------------------------------------------------------
echo [1/6] Looking for Python %MINPY% or newer...
if exist "%PY%" goto have_venv
if defined USER_PY goto use_user_py
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 goto use_py312
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 goto use_py3
python -c "import sys" >nul 2>&1
if not errorlevel 1 goto use_python
goto no_python

:use_py312
set "PYCMD=py -3.12"
goto verify_python

:use_py3
set "PYCMD=py -3"
goto verify_python

:use_python
set "PYCMD=python"
goto verify_python

:use_user_py
if not exist "%USER_PY%" goto user_py_missing
set PYCMD="%USER_PY%"
goto verify_python

:user_py_missing
echo.
echo   [ERROR] The interpreter given with --python does not exist:
echo           %USER_PY%
echo   Check the path and run this installer again.
echo.
set "RC=1"
goto done

:verify_python
%PYCMD% -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,12) else 1)" >nul 2>&1
if errorlevel 1 goto python_too_old
%PYCMD% -c "import sys; print('      OK, this Python will be used: ' + sys.version.split()[0])"
echo.
goto create_venv

:python_too_old
echo.
echo   [ERROR] The Python that was found is too old:
%PYCMD% -c "import sys; print('            ' + sys.version.split()[0])"
echo            Required: %MINPY% or newer.
echo.
echo   The pinned dependencies need it: numpy 2.5 requires Python 3.12+ and
echo   onnxruntime 1.30 requires Python 3.11+.
echo.
echo   How to fix:
echo     1. Install Python 3.13 from https://www.python.org/downloads/windows/
echo     2. Tick "Add python.exe to PATH" in the setup program.
echo     3. Open a NEW terminal - PATH changes need a new window - and run
echo        this installer again.
echo   Nothing was changed on this machine.
echo.
set "RC=1"
goto done

:no_python
echo.
echo   [ERROR] No usable Python interpreter was found.
echo.
echo   This project needs Python %MINPY% or newer - 3.13 recommended.
echo   Nothing was changed on this machine.
echo.
echo   How to fix:
echo     1. Install Python 3.13 from https://www.python.org/downloads/windows/
echo     2. Tick "Add python.exe to PATH" in the setup program.
echo     3. Open a NEW terminal - PATH changes need a new window - and run
echo        this installer again.
echo.
echo   Python already installed but still not found?
echo     Give this installer the full path of the interpreter:
echo         install.cmd --python "your-python-folder\python.exe"
echo     The official installer also ships the py launcher.  If "py -0"
echo     lists a 3.12 or newer version, open a new window and retry.
echo.
set "RC=1"
goto done

REM ------------------------------------------------------------
REM  2. Virtual environment
REM ------------------------------------------------------------
:have_venv
echo [2/6] Virtual environment... already present, skipping.
echo           %VENVDIR%
echo.
goto deps

:create_venv
echo [2/6] Creating the virtual environment...
echo           %VENVDIR%
%PYCMD% -m venv "%VENVDIR%"
if errorlevel 1 goto venv_failed
if not exist "%PY%" goto venv_failed
if defined NO_PIP_UPGRADE goto venv_no_pip_upgrade
echo       Upgrading pip inside the new environment - optional, best effort.
"%PY%" -m pip install --upgrade pip --disable-pip-version-check --no-input
if errorlevel 1 echo       [WARN] pip upgrade failed, continuing with the bundled pip.
goto venv_ready

:venv_no_pip_upgrade
echo       Skipped the pip upgrade on request: --no-pip-upgrade

:venv_ready
echo.
goto deps

:venv_failed
echo.
echo   [ERROR] Could not create the virtual environment:
echo           %VENVDIR%
echo.
echo   Common causes:
echo     - the folder is read-only, for example a folder still inside a ZIP
echo       preview window
echo     - antivirus software blocked "python -m venv"
echo     - the disk is full
echo   Move the project to a normal folder such as Desktop or Documents,
echo   then run this installer again.  Nothing was changed outside this
echo   folder.
echo.
set "RC=1"
goto done

REM ------------------------------------------------------------
REM  3. Dependencies
REM ------------------------------------------------------------
:deps
echo [3/6] Python dependencies...
if defined SKIP_DEPS goto deps_skipped
if not exist "%REQ%" goto req_missing
if defined NO_MIRROR goto deps_index_ready
if defined PIP_CONFIG_FILE goto deps_index_ready
if not exist "%ROOT%pip.ini" goto deps_index_ready
set "PIP_CONFIG_FILE=%ROOT%pip.ini"
echo       Using the mirror configuration shipped with the project: pip.ini
:deps_index_ready
echo       Installing from: %REQ%
echo       This downloads about 100 MB of wheels and can take a few minutes.
"%PY%" -m pip install -r "%REQ%" --disable-pip-version-check --no-input
if errorlevel 1 goto deps_failed
echo       Dependencies are in place.
echo.
goto model

:deps_skipped
echo       Skipped on request: --skip-deps
echo       The self-check will be skipped too, because it would fail.
echo.
goto model

:deps_failed
echo.
echo   [ERROR] Installing the dependencies failed.  Nothing else was changed.
echo   The pip output above says why - in most cases it is the network.
echo.
echo   What to try, in order:
echo     1. Run this installer again.  pip keeps everything it has already
echo        downloaded, so the retry continues where it stopped.
echo     2. Switch the download index.  This package ships a China mirror in
echo        pip.ini.  If that mirror is unreachable from your network, run:
echo            install.cmd --no-mirror
echo        Behind a corporate proxy, set it before running the installer:
echo            set HTTPS_PROXY=http://user:pass@proxy-host:port
echo     3. Make sure the disk has about 2 GB free and that antivirus is not
echo        blocking pip.
echo   No system-wide package was installed and no PATH entry was written.
echo.
set "RC=1"
goto done

:req_missing
echo.
echo   [ERROR] Dependency list not found:
echo           %REQ%
echo   The downloaded package looks incomplete.  Unzip it again keeping the
echo   folder structure intact, then run this installer.
echo.
set "RC=1"
goto done

REM ------------------------------------------------------------
REM  4. Local embedding model
REM ------------------------------------------------------------
:model
echo [4/6] Local embedding model...
if defined SKIP_MODEL goto model_skipped
if not exist "%DEFAULT_MODEL%" goto model_download
if not exist "%ROOT%models\bge-small-zh-v1.5\tokenizer.json" goto model_download
echo       Already downloaded, skipping.  Search works fully offline.
echo.
goto folders

:model_download
echo       Not found yet.  Fetching about 100 MB from ModelScope, one time.
echo       tools\download_model.py reads the real target from .env, so a
echo       custom EMBED_MODEL_DIR is honoured here.
"%PY%" -X utf8 "%ROOT%tools\download_model.py"
if errorlevel 1 goto model_failed
echo       Model downloaded.
echo.
goto folders

:model_skipped
echo       Skipped on request: --skip-model
echo.
goto folders

:model_failed
echo.
echo   [ERROR] The embedding model could not be downloaded.
echo   It is a one-time download of about 100 MB from https://www.modelscope.cn
echo.
echo   What to try:
echo     1. Check the network, or set a proxy first:
echo            set HTTPS_PROXY=http://user:pass@proxy-host:port
echo     2. Run this installer again - already fetched files are kept.
echo     3. To finish the rest of the setup without the model, run:
echo            install.cmd --skip-model
echo   Without this model the dashboard still starts, but search and ingest
echo   cannot compute embeddings.
echo.
set "RC=1"
goto done

REM ------------------------------------------------------------
REM  5. Runtime folders
REM ------------------------------------------------------------
:folders
echo [5/6] Creating the runtime folders...
if not exist "%ROOT%raw" mkdir "%ROOT%raw"
if not exist "%ROOT%notes" mkdir "%ROOT%notes"
if not exist "%ROOT%index" mkdir "%ROOT%index"
if not exist "%ROOT%logs" mkdir "%ROOT%logs"
if not exist "%ROOT%models" mkdir "%ROOT%models"
if not exist "%ROOT%index" goto folders_failed
echo       raw  notes  index  logs  models
echo.
goto check

:folders_failed
echo.
echo   [ERROR] Could not create the folder:
echo           %ROOT%index
echo   The project folder seems to be read-only.  Move the project to a
echo   writable folder such as Desktop or Documents and run it again.
echo.
set "RC=1"
goto done

REM ------------------------------------------------------------
REM  6. Self-check
REM ------------------------------------------------------------
:check
echo [6/6] Environment self-check - dependencies, model, database...
if defined SKIP_DEPS goto check_skipped
"%PY%" -X utf8 "%ROOT%tools\study.py" check
if errorlevel 1 goto check_failed
echo.
goto done_ok

:check_skipped
echo       Skipped: --skip-deps was used, so the dependencies are not
echo       installed and the self-check would fail by design.
echo       Run this installer again without --skip-deps to finish setup.
echo.
goto done_ok

:check_failed
echo.
echo   [ERROR] The self-check reported problems - see the FAIL lines above.
echo   The environment is not ready yet.
echo.
echo   Most common causes:
echo     - dependencies missing: run this installer again without
echo       --skip-deps and read the pip output for the reason
echo     - model download incomplete: run this installer again
echo   No system-wide change was made.
echo.
set "RC=1"
goto done

REM ------------------------------------------------------------
REM  --check-only
REM ------------------------------------------------------------
:check_only
echo [check-only] Environment self-check...
if not exist "%PY%" goto check_only_no_venv
echo       Interpreter: %PY%
"%PY%" -X utf8 "%ROOT%tools\study.py" check
set "RC=%errorlevel%"
goto done

:check_only_no_venv
echo.
echo   [ERROR] No virtual environment found:
echo           %VENVDIR%
echo   Run this installer first, without --check-only:
echo           install.cmd
echo.
set "RC=1"
goto done

REM ------------------------------------------------------------
REM  Finish
REM ------------------------------------------------------------
:done_ok
echo ============================================================
echo  DONE - the environment is ready.
echo ============================================================
echo.
echo  NEXT STEPS
echo.
echo  1. API key - needed only for the AI features.
echo     Start the dashboard, click the settings button in the top right
echo     corner and paste your DeepSeek API key there.  It applies at once.
echo     Alternatively copy .env.example to .env, put your key into
echo     DEEPSEEK_API_KEY and restart the dashboard.
echo.
echo  2. Start the dashboard, any of these:
echo       double-click  dashboard.cmd
echo       double-click the Chinese-named launcher sitting next to it
echo       or run:  venv\Scripts\python.exe -X utf8 tools\dashboard.py
echo     Then open http://127.0.0.1:8756/
echo.
echo  3. Add your own material: drop files into the raw folder and ingest
echo     them from the dashboard Data page, or run:
echo       venv\Scripts\python.exe -X utf8 tools\study.py ingest
echo.
echo  Re-running this installer is safe - it skips what is already done.
echo.
goto done

:done
popd
echo.
if not "%RC%"=="0" echo This run ended with an error.  Exit code: %RC%
if "%PAUSE_AT_END%"=="1" echo Press any key to close this window.
if "%PAUSE_AT_END%"=="1" pause
exit /b %RC%
