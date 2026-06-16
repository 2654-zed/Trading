@echo off
REM ===================================================================
REM Continuous bloXroute ETH mempool capture (for Task Scheduler).
REM Runs until killed; the script has internal reconnect. Task Scheduler
REM is configured AtStartup/AtLogon + restart-on-failure + IgnoreNew, so
REM exactly one instance runs continuously and survives reboots.
REM Credentials: BLOXROUTE_AUTH_HEADER is read from the USER environment
REM (set via setx); a task running as the user inherits it. Never echoed.
REM ===================================================================

cd /d C:\Users\jason\Desktop\Trading
set PY="C:\Users\jason\AppData\Local\Python\pythoncore-3.14-64\python.exe"

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set DT=%%I
echo ==================== capture start %DT% ==================== >> engine\data\mempool_capture.log

REM --minutes 0 = run continuously until terminated.
%PY% -m engine.scripts.run_mempool_capture --minutes 0 --stats-interval 300 >> engine\data\mempool_capture.log 2>&1
echo exit_code=%ERRORLEVEL% >> engine\data\mempool_capture.log
