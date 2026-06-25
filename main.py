import time
import json
import os
import subprocess
import signal
from pathlib import Path
from core.logger_config import logger
from datetime import datetime

HEARTBEAT_FILE = Path("data_cache/heartbeats.json")
WATCHDOG_INTERVAL = 60
TIMEOUT_THRESHOLD = 300

# Webhook stub
def send_webhook_alert(msg: str):
    logger.info(f"[WEBHOOK_ALERT] (STUB) Sending alert: {msg}")
    # requests.post(WEBHOOK_URL, json={"msgtype": "text", "text": {"content": msg}})

def load_heartbeats():
    if not HEARTBEAT_FILE.exists():
        return {}
    try:
        with open(HEARTBEAT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取心跳文件异常: {e}")
        return {}

def start_process(name, cmd):
    logger.info(f"[WATCHDOG] 启动模块 {name}...")
    # Using Popen to launch the process and not block
    proc = subprocess.Popen(cmd, shell=True)
    return proc

def kill_process(proc):
    if proc and proc.poll() is None:
        try:
            logger.warning(f"[WATCHDOG] 正在强杀进程 PID={proc.pid}...")
            # Windows taskkill to ensure subprocesses are killed
            subprocess.call(['taskkill', '/F', '/T', '/PID', str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.error(f"[WATCHDOG] 强杀进程失败: {e}")

def run_watchdog():
    logger.info("=========================================")
    logger.info("🐺 全局看门狗 (Watchdog) 启动，接管核心系统...")
    logger.info("=========================================")
    
    # Initialize heartbeat file
    HEARTBEAT_FILE.parent.mkdir(exist_ok=True)
    with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
        json.dump({"brain_node": time.time(), "live_trader": time.time()}, f)

    # Launch modules
    modules = {
        "brain_node": {"cmd": "python brain_node.py", "proc": None},
        "live_trader": {"cmd": "python live_trader.py", "proc": None}
    }
    
    for name, m in modules.items():
        m["proc"] = start_process(name, m["cmd"])
        
    try:
        while True:
            time.sleep(WATCHDOG_INTERVAL)
            now = time.time()
            heartbeats = load_heartbeats()
            
            for name, m in modules.items():
                last_hb = heartbeats.get(name, now)
                
                # Check if process died
                if m["proc"].poll() is not None:
                    msg = f"🚨 [CRITICAL] 模块 {name} 进程意外退出！正在重启..."
                    logger.critical(msg)
                    send_webhook_alert(msg)
                    m["proc"] = start_process(name, m["cmd"])
                    # Reset heartbeat to prevent immediate re-trigger
                    heartbeats[name] = now
                    continue
                
                # Check heartbeat stall
                delay = now - last_hb
                if delay > TIMEOUT_THRESHOLD:
                    msg = f"🚨 [CRITICAL] 模块 {name} 假死或死锁！心跳延迟 {delay:.1f} 秒超过阈值 {TIMEOUT_THRESHOLD} 秒。正在强制隔离并重启！"
                    logger.critical(msg)
                    send_webhook_alert(msg)
                    
                    kill_process(m["proc"])
                    time.sleep(2)  # Wait for resources to free
                    m["proc"] = start_process(name, m["cmd"])
                    
                    # Reset heartbeat
                    heartbeats[name] = now
                    
            # Save reset heartbeats if any
            with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
                json.dump(heartbeats, f)

    except KeyboardInterrupt:
        logger.info("[WATCHDOG] 接收到退出信号，正在关闭所有子进程...")
        for m in modules.values():
            kill_process(m["proc"])
        logger.info("[WATCHDOG] 系统安全关闭。")

if __name__ == "__main__":
    run_watchdog()
