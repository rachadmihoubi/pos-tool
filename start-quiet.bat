@echo off
REM ===========================================================================
REM  start-quiet.bat - starts the tool without any windows appearing.
REM
REM  This is what Task Scheduler runs when the computer starts. You would not
REM  normally run it yourself; use start.bat, which shows what is happening.
REM
REM  The dashboard is then waiting at http://127.0.0.1:8777 whenever you want
REM  it. No browser is opened automatically.
REM ===========================================================================

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" exit /b 1

REM pythonw.exe runs without a console window.
start "" /b ".venv\Scripts\pythonw.exe" watcher.py
start "" /b ".venv\Scripts\pythonw.exe" app.py --no-browser

exit /b 0
