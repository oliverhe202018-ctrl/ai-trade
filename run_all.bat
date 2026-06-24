@echo off
chcp 65001 >nul

:: Force working directory to project root
cd /d "%~dp0"

set PYTHON_CMD=c:\users\a2515\appdata\local\hermes\hermes-agent\venv\Scripts\python.exe
if not exist "%PYTHON_CMD%" (
    echo [STARTUP_FAIL] Python executable not found: %PYTHON_CMD%
    pause
    exit /b 1
)

echo ==========================================
echo Starting AI Trader System...
echo ==========================================

echo [1/9] Starting LLM Monitor...
start "LLM Monitor" cmd /k "%PYTHON_CMD% scripts\llama_monitor.py || echo [STARTUP_FAIL] llama_monitor failed && pause"
echo Waiting 15 seconds for LLM to load into VRAM...
timeout /t 15 /nobreak >nul

echo [2/9] Starting Brain Node...
start "Brain Node" cmd /k "%PYTHON_CMD% brain_node.py || echo [STARTUP_FAIL] brain_node failed && pause"

echo [3/9] Starting AI Trader...
start "AI Trader" cmd /k "%PYTHON_CMD% ai_trader.py || echo [STARTUP_FAIL] ai_trader failed && pause"

echo [4/9] Starting Index Cache Updater...
start "Index Cache Updater" cmd /k "%PYTHON_CMD% feeds\index_updater.py || echo [STARTUP_FAIL] index_updater failed && pause"

echo [5/9] Starting News Fetcher...
start "News Fetcher" cmd /k "%PYTHON_CMD% datahub\news_fetcher.py || echo [STARTUP_FAIL] news_fetcher failed && pause"

echo [6/9] Starting Event Extractor...
start "Event Extractor" cmd /k "%PYTHON_CMD% nlp\event_extractor.py || echo [STARTUP_FAIL] event_extractor failed && pause"

echo [7/9] Starting Agent Daemon...
start "Agent Daemon" cmd /k "python core\agent_daemon.py || echo [STARTUP_FAIL] agent_daemon failed && pause"
timeout /t 3 /nobreak >nul

echo [8/9] Starting Web Dashboard...
start "Web Dashboard" cmd /k "streamlit run core\dashboard.py || echo [STARTUP_FAIL] web dashboard failed && pause"

echo.
echo ==========================================
echo System launched successfully!
echo Web UI: http://localhost:8888
echo ==========================================
echo.

echo [9/9] Starting Live Trader Scheduler...
:loop
for /f "tokens=1-2 delims=:" %%a in ("%time%") do (
    set /a "current_time=%%a*100+%%b"
)

:: 9:30 - 14:55 Trade Logic
if %current_time% geq 930 if %current_time% leq 1455 (
    c:\users\a2515\appdata\local\hermes\hermes-agent\venv\Scripts\python.exe live_trader.py
)

:: 15:00 Daily Settlement
if %current_time% geq 1500 if %current_time% leq 1505 (
    python daily_settlement.py
    exit
)

timeout /t 300
goto loop