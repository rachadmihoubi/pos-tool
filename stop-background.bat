@echo off
REM ===========================================================================
REM  stop-background.bat - stops the tool if it is running quietly in the
REM  background (started by start-quiet.bat, by hand or by Windows at login).
REM
REM  This does NOT remove the "start automatically" entries - for that, run
REM  uninstall-startup.bat. This just stops it running right now. You can
REM  start it again any time with start.bat or start-quiet.bat.
REM
REM  Safe to run even if nothing is running - it just says so.
REM ===========================================================================

setlocal
echo.
echo  Stopping Shop Analysis...
echo.

powershell -NoProfile -Command ^
  "$procs = Get-CimInstance Win32_Process | Where-Object { " ^
  "  ($_.CommandLine -match 'watcher\.py') -or " ^
  "  ($_.CommandLine -match 'app\.py' -and $_.CommandLine -match '--no-browser') " ^
  "};" ^
  "if (-not $procs) { Write-Host '  Nothing was running.'; exit 0 }" ^
  "foreach ($p in $procs) { " ^
  "  Write-Host ('  Stopping PID ' + $p.ProcessId + '...'); " ^
  "  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue " ^
  "}; " ^
  "Write-Host '  Done.'"

echo.
echo  The dashboard is no longer running. The tool will still start again
echo  next time you log in, unless you also run uninstall-startup.bat.
echo.
pause
