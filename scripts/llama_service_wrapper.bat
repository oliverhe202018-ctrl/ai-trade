@echo off
chcp 65001 >nul
title LLaMA Server Service Wrapper

:: 设置工作目录
cd /d "H:\llama-b9616-bin-win-cuda-12.4-x64"

:: 检查 llama-server 是否从不
python "C:\Users\a2515\ai-trader\llama_service_monitor.py" start

:: 如果监控服务失败，直接启动 llama-server
if %ERRORLEVEL% neq 0 (
    echo [WARN] Monitor service failed, starting llama-server directly...
    llama-server.exe ^
        -m "C:\Users\a2515\ai-trader\models\Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ4_NL.gguf" ^
        -ngl 32 ^
        --flash-attn on ^
        --jinja ^
        -c 32768 ^
        --ctx-size 32768 ^
        --parallel 1 ^
        --host 127.0.0.1 ^
        --port 8080 ^
        --no-chunked-preprocessing
)

pause