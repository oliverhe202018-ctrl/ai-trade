import subprocess
import time
import os
import sys

# 将项目根目录 ai-trader 加入 sys.path，确保核心模块可被引用
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from core.logger_config import logger

# ================= 配置区 =================
# 替换为你 llama.cpp 所在的真实绝对路径
LLAMA_DIR = r"H:\llama-b9616-bin-win-cuda-12.4-x64" 
BAT_SCRIPT = "222.bat"
# =========================================

def start_and_monitor():
    """启动大模型并进行无限存活监控"""
    # 强制切换工作目录，防止 bat 内部的相对路径找不到模型文件
    if not os.path.exists(LLAMA_DIR):
        logger.info(f"[致命错误] 找不到目录: {LLAMA_DIR}")
        sys.exit(1)
        
    os.chdir(LLAMA_DIR)

    while True:
        logger.info(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🚀 正在启动大模型服务: {BAT_SCRIPT} ...")

        try:
            # 启动进程，配置标准输入为 PIPE（管道），允许注入按键
            # stdout 和 stderr 保持默认，直接输出到当前控制台，方便你随时看日志
            process = subprocess.Popen(
                BAT_SCRIPT,
                stdin=subprocess.PIPE,
                text=True,
                shell=True 
            )

            # 暂停 2 秒，等待 bat 脚本加载出菜单界面
            time.sleep(2.0)
            
            logger.info(f"[{time.strftime('%H:%M:%S')}] 🤖 正在自动注入配置指令 '6'...")
            # 注入按键 '6' 并附带换行符（模拟回车键）
            process.stdin.write("6\n")
            process.stdin.flush()

            # 阻塞主线程，死死盯住这个进程，直到它异常退出或崩溃
            process.wait()

            logger.info(f"\n[{time.strftime('%H:%M:%S')}] ⚠️ 大模型进程已退出或崩溃 (退出码: {process.returncode})")

        except Exception as e:
            logger.exception(f"\n[{time.strftime('%H:%M:%S')}] ❌ 守护进程执行发生异常: {e}")

        # 冷却休眠：防止因显存不足等致命报错导致的一秒重启几百次的“死循环”
        logger.info(f"[{time.strftime('%H:%M:%S')}] ⏳ 系统将在 10 秒后自动重启大模型...")
        time.sleep(10)

if __name__ == "__main__":
    logger.info("========================================")
    logger.info("      AI Trader - LLM 守护进程启动       ")
    logger.info("========================================")
    start_and_monitor()