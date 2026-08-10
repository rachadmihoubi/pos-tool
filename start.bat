@echo off
REM ===========================================================================
REM  start.bat - starts the tool.
REM
REM  Starts two things:
REM    1. the watcher, which notices when your POS saves a sale and refreshes
REM       the figures, and which sends the daily digest at the set hour
REM    2. the dashboard, and opens it in your browser
REM
REM  Leave the window that appears open. Closing it stops the tool.
REM  Nothing here writes to your POS database.
REM ===========================================================================

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  The tool is not set up yet.
    echo  Double-click  setup.bat  first, then come back to this one.
    echo.
    pause
    exit /b 1
)

echo.
echo  Starting Shop Analysis...
echo.

REM The watcher runs quietly in its own window, minimised.
start "Shop Analysis - watcher" /min ".venv\Scripts\pythonw.exe" watcher.py

REM Give it a moment to read the database before the dashboard opens.
timeout /t 2 /nobreak >nul

REM The dashboard runs in this window, and opens the browser itself.
".venv\Scripts\python.exe" app.py

REM If the dashboard stops, stop the watcher too rather than leaving it
REM running invisibly in the background.
taskkill /f /fi "WINDOWTITLE eq Shop Analysis - watcher*" >nul 2>&1

echo.
echo  Shop Analysis has stopped.
echo.
pause
