@echo off
REM ===========================================================================
REM  uninstall-startup.bat - stops the tool starting with Windows.
REM
REM  Removes the two Task Scheduler entries that install-startup.bat made.
REM  It does NOT delete the tool, your settings or any of your data - you can
REM  still start it by hand with start.bat.
REM
REM  Right-click this file and choose "Run as administrator".
REM ===========================================================================

setlocal
echo.
echo  Removing the automatic start entries...
echo.

schtasks /delete /f /tn "Shop Analysis - Dashboard" >nul 2>&1
if errorlevel 1 (echo    Dashboard : was not there) else (echo    Dashboard : removed)

schtasks /delete /f /tn "Shop Analysis - Digest" >nul 2>&1
if errorlevel 1 (echo    Digest    : was not there) else (echo    Digest    : removed)

echo.
echo  Done. The tool no longer starts by itself.
echo  You can still start it any time with start.bat
echo.
pause
