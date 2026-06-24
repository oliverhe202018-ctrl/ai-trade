@echo off
chcp 65001
:: 强制将工作目录切换回项目根目录
cd /d "C:\Users\a2515\ai-trader"
title AI Trader System Launcher

set VENV_PYTHON=c:\users\a2515\appdata\local\hermes\hermes-agent\venv\Scripts\python.exe
set VENV_STREAMLIT=c:\users\a2515\appdata\local\hermes\hermes-agent\venv\Scripts\streamlit.exe

echo [1/3] Starting LLM Monitor (Daemon)...
start "LLM_Monitor" cmd /k "%VENV_PYTHON% scripts\llama_monitor.py"

echo Waiting 10 seconds for LLM to load into VRAM...
timeout /t 10 /nobreak >nul

echo [2/3] Starting Agent Daemon...
start "Agent Daemon" cmd /k "python core/agent_daemon.py"

timeout /t 3 /nobreak >nul

echo [3/3] Starting Web Dashboard...
start "Web Dashboard" cmd /k "streamlit run core/dashboard.py"

echo.
echo ==========================================
echo System launched successfully!
echo URL: http://localhost:8888
echo ==========================================
echo.
pause