@echo off
REM ===================================================================
REM Continuous Tier-1 confirmation ingest (free public ETH RPC).
REM SEPARATE process from the bloXroute mempool capture — never touches
REM the WebSocket stream. Safe to run alongside run_mempool_capture.bat.
REM No credentials: endpoints are public, read-only.
REM ===================================================================

cd /d C:\Users\jason\Desktop\Trading
set PY="C:\Users\jason\AppData\Local\Python\pythoncore-3.14-64\python.exe"

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set DT=%%I
echo ==================== block_ingest start %DT% ==================== >> engine\data\block_ingest.log

REM --minutes 0 = run continuously until terminated.
%PY% -m engine.scripts.run_block_ingest --minutes 0 >> engine\data\block_ingest.log 2>&1
echo exit_code=%ERRORLEVEL% >> engine\data\block_ingest.log
