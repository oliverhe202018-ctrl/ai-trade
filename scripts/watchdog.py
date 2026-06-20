"""
Shadow Oracle Watchdog (影子神谕看门狗)
核心职责：
1. 端口心跳侦测：每 30 秒 Ping 一次大模型 API，超时无响应直接物理强杀并重启。
2. 显存潮汐管理：11:30-13:00 强制关闭大模型引擎，释放 16G 显存物理降温，12:55 提前预热重启。
"""

import os
import time
import psutil
import requests
import subprocess
from datetime import datetime, time as dt_time

# --- 极客配置区 ---
LLM_HOST = "http://127.0.0.1:8080"
HEALTH_ENDPOINT = f"{LLM_HOST}/health" # 或者 /v1/models 根据你的 llama.cpp 实际 endpoint 调整
START_SCRIPT = r"C:\Users\a2515\ai-trader\scripts\222.bat" # 替换为你的启动脚本绝对路径
PROCESS_NAME = "llama-server.exe" # llama.cpp 在 Windows 下的进程名，若是其他请在此修改

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [WATCHDOG] {msg}")

def kill_llm_process():
    """冷酷无情的物理强杀"""
    killed = False
    for proc in psutil.process_iter(['pid', 'name']):
        if proc.info['name'] == PROCESS_NAME or "llama.cpp" in str(proc.info['name']).lower():
            try:
                proc.kill()
                killed = True
                log(f"已强杀僵死进程: {proc.info['name']} (PID: {proc.info['pid']})")
            except Exception as e:
                log(f"强杀失败: {e}")
    if not killed:
        log("未发现运行中的大模型进程。")

def start_llm_process():
    """点火拉起大模型"""
    log("正在执行拉起脚本...")
    # 使用 creationflags 分离控制台，防止 Watchdog 被阻塞
    subprocess.Popen(START_SCRIPT, shell=True, creationflags=subprocess.CREATE_NEW_CONSOLE)
    time.sleep(10) # 给予加载 131K 上下文和权重的预填充时间

def check_health():
    """侦测心跳，极速超时设为 5 秒"""
    try:
        res = requests.get(HEALTH_ENDPOINT, timeout=5)
        return res.status_code == 200
    except requests.RequestException:
        return False

def is_noon_break():
    """判断是否处于 A股 中午休市物理降温期 (11:30 - 12:55)"""
    now = datetime.now().time()
    return dt_time(11, 30) <= now <= dt_time(12, 55)

def run_watchdog():
    log("影子神谕看门狗已上线，开始监控 4060 Ti 阵地...")
    is_cooling_down = False

    while True:
        try:
            # 1. 潮汐显存管理优先
            if is_noon_break():
                if not is_cooling_down:
                    log("进入午盘休市期。执行指令：切断引擎，释放 4060 Ti 显存进行物理降温...")
                    kill_llm_process()
                    is_cooling_down = True
                time.sleep(60)
                continue
            else:
                if is_cooling_down:
                    log("休市降温期结束。执行指令：提前 5 分钟点火预热大模型...")
                    start_llm_process()
                    is_cooling_down = False

            # 2. 正常交易时段的心跳保活
            if not is_cooling_down:
                if not check_health():
                    log("⚠️ 警告：大模型心跳停止或 API 假死！启动紧急重启预案...")
                    kill_llm_process()
                    time.sleep(2)
                    start_llm_process()
                else:
                    log("心跳正常，引擎轰鸣中。")

            time.sleep(30) # 监控轮询间隔

        except KeyboardInterrupt:
            log("Watchdog 收到退出指令，结束监控。")
            break
        except Exception as e:
            log(f"Watchdog 自身发生异常 (已忽略): {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_watchdog()