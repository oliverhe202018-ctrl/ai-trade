@echo off
chcp 65001 >nul

:: Force working directory to project root
cd /d "%~dp0"

echo ==========================================
echo Starting AI Trader System...
echo ==========================================

echo [1/5] Starting LLM Monitor...
start "LLM Monitor" cmd /k "c:\users\a2515\appdata\local\hermes\hermes-agent\venv\Scripts\python.exe scripts\llama_monitor.py"
echo Waiting 15 seconds for LLM to load into VRAM...
timeout /t 15 /nobreak >nul

echo [2/5] Starting Brain Node (Slow Brain)...
start "Brain Node" cmd /k "c:\users\a2515\appdata\local\hermes\hermes-agent\venv\Scripts\python.exe brain_node.py"

echo [3/5] Starting Agent Daemon...
start "Agent Daemon" cmd /k "python core\agent_daemon.py"
timeout /t 3 /nobreak >nul

echo [4/5] Starting Web Dashboard...
start "Web Dashboard" cmd /k "streamlit run core\dashboard.py"

echo.
echo ==========================================
echo System launched successfully!
echo Web UI: http://localhost:8888
echo ==========================================
echo.

echo [5/5] Starting Live Trader Scheduler...
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