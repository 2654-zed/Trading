@echo off
REM ===================================================================
REM H9 paper-trade scheduled runner.
REM Safe to call from Windows Task Scheduler (every 12h recommended).
REM Idempotent + crash-safe: re-running never double-counts; a power loss
REM mid-run leaves the prior state (or its .bak) intact.
REM ===================================================================

cd /d C:\Users\jason\Desktop\Trading

REM --- (1) OPTIONAL: refresh L3 data BEFORE trading. ---
REM The harness is only as fresh as surveillance.db. Put your delta-sync
REM command here so each cycle pulls new rows before the paper trade runs.
REM Example:
REM   python path\to\your_delta_sync.py
REM (pulls new org_transfer_events / liquidity_events from surveillance_new.db
REM  or the live prod dump into the working surveillance.db)

REM --- (2) Run the paper trade, appending timestamped output to a log. ---
REM Explicit Python path so Task Scheduler (which may have a bare PATH)
REM cannot silently fail to find the interpreter.
set PY="C:\Users\jason\AppData\Local\Python\pythoncore-3.14-64\python.exe"
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set DT=%%I
echo. >> engine\data\paper_trade_cron.log
echo ==================== run %DT% ==================== >> engine\data\paper_trade_cron.log
%PY% -m engine.scripts.paper_trade_h9 >> engine\data\paper_trade_cron.log 2>&1
echo exit_code=%ERRORLEVEL% >> engine\data\paper_trade_cron.log
