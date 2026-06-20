@echo off
cd /d "%~dp0"
title AI Trader System Launcher

set VENV_PYTHON=c:\users\a2515\appdata\local\hermes\hermes-agent\venv\Scripts\python.exe
set VENV_STREAMLIT=c:\users\a2515\appdata\local\hermes\hermes-agent\venv\Scripts\streamlit.exe

echo [1/3] Starting LLM Monitor (Daemon)...
start "LLM_Monitor" cmd /k "%VENV_PYTHON% llama_monitor.py"

echo Waiting 10 seconds for LLM to load into VRAM...
timeout /t 10 /nobreak >nul

echo [2/3] Starting Agent Daemon...
start "Agent_Daemon" cmd /k "%VENV_PYTHON% agent_daemon.py"

timeout /t 3 /nobreak >nul

echo [3/3] Starting Web Dashboard...
start "Web_Dashboard" cmd /k "%VENV_STREAMLIT% run dashboard.py --server.port 8888 --server.headless false"

echo.
echo ==========================================
echo System launched successfully!
echo URL: http://localhost:8888
echo ==========================================
echo.
pause