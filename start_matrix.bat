@echo off
:: 设置 UTF-8 编码防止中文乱码
title 量化双擎总控台

echo ===================================================
echo ?? 正在唤醒 Shadow Oracle 异步量化双擎...
echo ===================================================
echo.

:: 1. 先启动"快手"节点 (颜色 0A: 黑底绿字)
:: 使用 start 命令会弹出一个全新的独立 cmd 窗口
echo [1/2] 正在挂载 Fast Hand 极速监听器...
start "Fast Hand (Live Trader) - 监听 TCP:5555" cmd /T:0A /k "cd /d C:\Users\a2515\ai-trader && python live_trader.py"

:: 极客微操：让系统强制冷静 3 秒，确保快手的 ZeroMQ 端口已经完全 bind 成功
timeout /t 3 /nobreak >nul

:: 2. 再启动"慢脑"节点 (颜色 0E: 黑底黄字)
echo [2/2] 正在唤醒 Slow Brain AI 指挥官...
start "Slow Brain (AI Commander) - 广播 TCP:5555" cmd /T:0E /k "cd /d C:\Users\a2515\ai-trader && python brain_node.py"

echo.
echo ? 双擎点火完毕！请并排监控弹出的两个控制台窗口。
echo 终端将在此刻功成身退。
timeout /t 3 >nul
exit