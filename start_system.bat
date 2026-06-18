@echo off
cd /d "%~dp0"
title AI Trader System Launcher

set VENV_PYTHON=c:\users\a2515\appdata\local\hermes\hermes-agent\venv\Scripts\python.exe
set VENV_STREAMLIT=c:\users\a2515\appdata\local\hermes\hermes-agent\venv\Scripts\streamlit.exe

echo [1/2] Starting Agent Daemon...
start "Agent_Daemon" cmd /k "%VENV_PYTHON% agent_daemon.py"

timeout /t 3 /nobreak >nul

echo [2/2] Starting Web Dashboard...
start "Web_Dashboard" cmd /k "%VENV_STREAMLIT% run dashboard.py --server.port 8888 --server.headless false"

echo.
echo ==========================================
echo System launched successfully!
echo URL: http://localhost:8888
echo ==========================================
echo.
pause