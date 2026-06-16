@echo off
REM ===================================================================
REM Continuous CEX Level-2 order-book capture (Game 3).
REM Public market data only -- NO API keys. Records 3 US-clean exchanges
REM (Coinbase, Kraken, Binance.US) concurrently to engine\data\l2.
REM WARNING: L2 is HEAVY -- expect GBs/day. Watch disk; stop or narrow
REM --pairs / --exchanges if it grows too fast. Single-instance locked.
REM ===================================================================

cd /d C:\Users\jason\Desktop\Trading
set PY="C:\Users\jason\AppData\Local\Python\pythoncore-3.14-64\python.exe"

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value 2^>nul') do set DT=%%I
echo ==================== l2_capture start %DT% ==================== >> engine\data\l2_capture.log

%PY% -m engine.scripts.run_l2_capture --minutes 0 --exchanges coinbase,kraken,binanceus --pairs BTC-USD,ETH-USD >> engine\data\l2_capture.log 2>&1
echo exit_code=%ERRORLEVEL% >> engine\data\l2_capture.log
