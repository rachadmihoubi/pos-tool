@echo off
REM ===========================================================================
REM  setup.bat - run this ONCE, the first time.
REM
REM  It creates a private Python environment inside this folder and installs
REM  what the tool needs. It does not touch anything else on the computer,
REM  and it never touches your POS database.
REM
REM  If anything goes wrong the window stays open so you can read why.
REM ===========================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo  ============================================================
echo    Shop Analysis - first-time setup
echo  ============================================================
echo.

REM --- 1. Find Python -------------------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo  [X] Python is not installed on this computer.
    echo.
    echo      Install it from:  https://www.python.org/downloads/
    echo      IMPORTANT: on the first screen of the installer, tick
    echo                 "Add Python to PATH" before pressing Install.
    echo.
    echo      Then run this setup.bat again.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('%PY% --version 2^>^&1') do set "PYVER=%%v"
echo  [1/5] Found !PYVER!

REM --- 2. Create the private environment -------------------------------------
if exist ".venv\Scripts\python.exe" (
    echo  [2/5] Environment already exists - reusing it.
) else (
    echo  [2/5] Creating the private Python environment...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo.
        echo  [X] Could not create the environment.
        echo      Check that you have permission to write to this folder.
        echo.
        pause
        exit /b 1
    )
)

REM --- 3. Install what it needs ----------------------------------------------
echo  [3/5] Installing the pieces it needs. This takes a minute...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo.
    echo  [X] Could not install the required pieces.
    echo      If this computer has no internet connection, connect it and
    echo      run setup.bat again. Internet is needed ONLY for this step -
    echo      the tool itself works offline afterwards.
    echo.
    pause
    exit /b 1
)

REM --- 4. Fonts for Arabic ----------------------------------------------------
REM  Optional. If it fails, Windows' own Arabic font is used instead and
REM  everything still displays correctly.
echo  [4/5] Fetching the Arabic font (optional)...
if not exist "static\fonts" mkdir "static\fonts"
".venv\Scripts\python.exe" tools\get_fonts.py
if errorlevel 1 (
    echo        Could not fetch it. Not a problem - Windows' own Arabic
    echo        font will be used instead.
)

REM --- 5. Settings and first read --------------------------------------------
if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo  [5/5] Created a blank .env for your passwords.
    )
) else (
    echo  [5/5] Your .env already exists - left alone.
)

echo.
echo  Checking your settings and reading your POS database once...
echo.
".venv\Scripts\python.exe" -m poslib.etl --force
if errorlevel 1 (
    echo.
    echo  [X] The database could not be read.
    echo.
    echo      Open config.yaml and check this line points at your real
    echo      POS database file:
    echo.
    echo         database:
    echo           path: "...\your database.dblx"
    echo.
    echo      Then run setup.bat again.
    echo.
    pause
    exit /b 1
)

echo.
echo  ============================================================
echo    Setup finished.
echo.
echo    To start the tool:        double-click  start.bat
echo    To start it automatically
echo    when the computer starts: double-click  install-startup.bat
echo.
echo    Passwords for email or Telegram go in the file called .env
echo    Settings and thresholds go in           config.yaml
echo  ============================================================
echo.
pause
