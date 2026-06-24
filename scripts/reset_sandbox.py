import os
import json
from pathlib import Path
from datetime import datetime

def main():
    print("🚀 正在初始化 V3.0 实盘/仿真演习沙盒...")
    
    # 自动推导项目根目录 (兼容在根目录或 scripts 目录下运行)
    current_dir = Path.cwd()
    if (current_dir / "data_cache").exists():
        root_dir = current_dir
    elif (current_dir.parent / "data_cache").exists():
        root_dir = current_dir.parent
    else:
        root_dir = current_dir
        (root_dir / "data_cache").mkdir(exist_ok=True)

    data_cache = root_dir / "data_cache"
    
    # 1. 重置系统风控状态机 (system_state.json)
    state_file = data_cache / "system_state.json"
    state_data = {
        "status": "RUNNING",
        "message": "System reset to initial state for V3.0 simulation",
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state_data, f, ensure_ascii=False, indent=4)
    print("✅ [系统状态] 已重置为: 🟢 RUNNING (解除一切封锁)")

    # 2. 重置资金账本 (live_portfolio.json)
    portfolio_file = data_cache / "live_portfolio.json"
    portfolio_data = {
        "balance": 1000000.0,  # 初始仿真资金，可根据 QMT 实际情况修改
        "locked_funds": 0.0,
        "positions": {},
        "history": []
    }
    with open(portfolio_file, 'w', encoding='utf-8') as f:
        json.dump(portfolio_data, f, ensure_ascii=False, indent=4)
    print("✅ [资金账本] 已重置: 初始资金 1,000,000.00，持仓已清空")

    # 3. 清空流水与队列文件 (.jsonl)
    # 这些是高频追加写入的文件，重置只需清空内容
    files_to_clear = [
        "order_queue.jsonl",
        "fills.jsonl",
        "signal_history.jsonl"
    ]
    for filename in files_to_clear:
        filepath = data_cache / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            pass # 仅打开并覆盖为空文件
        print(f"✅ [队列清理] 已彻底清空: {filename}")

    # 4. 清理底层崩溃日志 (如果有)
    crash_log = root_dir / "mcp_crash.log"
    if crash_log.exists():
        crash_log.unlink()
        print("✅ [日志清理] 已删除残留的报错日志: mcp_crash.log")

    print("\n🎉 沙盒净化完毕！系统已恢复至最纯净的出厂待命状态。")
    print("👉 接下来，你可以安心唤醒 Hermes 或是直接启动 live_trader.py 了！")

if __name__ == "__main__":
    main()