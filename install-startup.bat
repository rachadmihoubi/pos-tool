@echo off
REM ===========================================================================
REM  install-startup.bat - makes the tool start on its own with the computer.
REM
REM  Creates two entries in Windows Task Scheduler:
REM
REM    Shop Analysis - Dashboard   starts the watcher and the dashboard when
REM                                you log in
REM    Shop Analysis - Digest      sends the daily digest at the hour set in
REM                                config.yaml, even if the dashboard is not
REM                                running
REM
REM  Run this by RIGHT-CLICKING it and choosing "Run as administrator".
REM  To undo it later, run  uninstall-startup.bat
REM ===========================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo  ============================================================
echo    Starting automatically with Windows
echo  ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo  [X] The tool is not set up yet. Run setup.bat first.
    echo.
    pause
    exit /b 1
)

REM --- Read the digest time out of config.yaml -------------------------------
for /f "tokens=*" %%h in ('".venv\Scripts\python.exe" -c "from poslib.config import get_config;c=get_config();print('%%02d:%%02d'%%(int(c.get('digest.hour',20)),int(c.get('digest.minute',0))))"') do set "DIGEST_TIME=%%h"

if not defined DIGEST_TIME (
    echo  [!] Could not read the digest time from config.yaml. Using 20:00.
    set "DIGEST_TIME=20:00"
)

set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

echo  Folder      : %HERE%
echo  Digest time : %DIGEST_TIME%
echo.

REM --- 1. Dashboard and watcher, at log in ------------------------------------
echo  [1/2] Creating "Shop Analysis - Dashboard"...
schtasks /create /f ^
  /tn "Shop Analysis - Dashboard" ^
  /tr "\"%HERE%\start-quiet.bat\"" ^
  /sc onlogon ^
  /rl limited ^
  /delay 0000:30 >nul 2>&1

if errorlevel 1 (
    echo        Could not create it. Did you right-click this file and
    echo        choose "Run as administrator"?
    echo.
    pause
    exit /b 1
)
echo        done.

REM --- 2. Daily digest --------------------------------------------------------
echo  [2/2] Creating "Shop Analysis - Digest" for %DIGEST_TIME% daily...
schtasks /create /f ^
  /tn "Shop Analysis - Digest" ^
  /tr "\"%HERE%\.venv\Scripts\pythonw.exe\" \"%HERE%\watcher.py\" --digest-now" ^
  /sc daily ^
  /st %DIGEST_TIME% ^
  /rl limited >nul 2>&1

if errorlevel 1 (
    echo        Could not create the digest task.
) else (
    echo        done.
)

echo.
echo  ============================================================
echo    Set up. The tool will now start by itself when you log in.
echo.
echo    To check or change these, open the Start menu and search
echo    for "Task Scheduler". They are called:
echo        Shop Analysis - Dashboard
echo        Shop Analysis - Digest
echo.
echo    To remove them, run  uninstall-startup.bat
echo  ============================================================
echo.
pause
