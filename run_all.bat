@echo off
setlocal enabledelayedexpansion

:: 1. ������Ŀ��Ŀ¼�� Python ·�� (��ֹ ModuleNotFoundError)
set PROJECT_ROOT=%~dp0
echo Project Root is: %PROJECT_ROOT%
set PYTHONPATH=%PROJECT_ROOT%

:: 2. ����ר�����⻷�� (��������������Ҳ���������)
set VENV_PYTHON=c:\users\a2515\appdata\local\hermes\hermes-agent\venv\Scripts\python.exe
set VENV_STREAMLIT=c:\users\a2515\appdata\local\hermes\hermes-agent\venv\Scripts\streamlit.exe

echo ==========================================
echo Starting AI Trader System...
echo ==========================================

:: ��һ�׶Σ�����ģ�� (���ʱ���������������ȴ�)
echo [1/5] Starting LLM Monitor...
start "LLM Monitor" cmd /k "%VENV_PYTHON% "%PROJECT_ROOT%scripts\llama_monitor.py""
echo Waiting 15 seconds for LLM to load into VRAM...
timeout /t 15 /nobreak >nul

:: �ڶ��׶Σ�����ս��ָ������ֽڵ�
echo [2/5] Starting Brain Node (Slow Brain)...
start "Brain Node" cmd /k "%VENV_PYTHON% "%PROJECT_ROOT%brain_node.py""

echo [3/5] Starting Live Trader (Fast Hand)...
start "Live Trader" cmd /k "%VENV_PYTHON% "%PROJECT_ROOT%live_trader.py""

:: �����׶Σ�������̨�ܼ�
echo [4/5] Starting Agent Daemon...
start "Agent Daemon" cmd /k "%VENV_PYTHON% "%PROJECT_ROOT%core\agent_daemon.py""
timeout /t 3 /nobreak >nul

:: ���Ľ׶Σ��������ӻ����
echo [5/5] Starting Web Dashboard...
start "Web Dashboard" cmd /k "%VENV_STREAMLIT% run "%PROJECT_ROOT%core\dashboard.py" --server.port 8888 --server.headless false"

echo.
echo ==========================================
echo System launched successfully!
echo Web UI: http://localhost:8888
echo ==========================================
echo.
pause