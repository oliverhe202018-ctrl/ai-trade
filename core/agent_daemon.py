"""
实盘交易守护进程 (Agent Daemon)
遵循 A 股物理时间轴调度：
- 14:40 信号生成与路由计算（由 brain_node.py 慢脑节点负责）
- 14:50 订单执行（由 live_trader.py 快手节点通过 ZeroMQ 负责）
- 15:10 日终结算与状态落盘
"""
import os
import sys
import time
import schedule
from datetime import datetime

# 将项目根目录加入 sys.path，确保 core 和 feeds 模块可被引用
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from core.logger_config import logger
from feeds.notifier import send_notification


def is_trading_day():
    """
    判断今日是否为交易日（过滤周末）。
    注：未包含法定节假日判断，如需完整支持可接入交易日历 API。
    """
    return datetime.now().weekday() < 5  # 0=周一, 4=周五


def safe_execute(phase_name, phase_func):
    """
    安全执行包装器：捕获所有异常，推送预警，严禁守护进程退出。
    """
    if not is_trading_day():
        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 非交易日，跳过 {phase_name}")
        return

    try:
        logger.info(f"\n{'='*60}")
        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始执行: {phase_name}")
        logger.info(f"{'='*60}")
        phase_func()
        logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] {phase_name} 执行完毕")
    except Exception as e:
        error_msg = f"[{phase_name}] 执行异常: {e}"
        logger.exception(f"[ERROR] {error_msg}")
        send_notification("守护进程预警", error_msg)
        # 异常后 sleep 防止快速重试
        time.sleep(60)


def run_daemon():
    """
    启动守护进程主循环。
    按 A 股物理时间轴调度三阶段任务。
    注：信号生成由 brain_node.py（慢脑）通过 ZeroMQ 广播，
        订单执行由 live_trader.py（快手）通过 ZeroMQ 监听，
        守护进程仅负责日终结算和整体协调。
    """
    logger.info(f"\n{'='*60}")
    logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 实盘守护进程启动")
    logger.info(f"调度计划:")
    logger.info(f"  - 14:40 信号生成（由 brain_node.py 慢脑节点负责）")
    logger.info(f"  - 14:50 订单执行（由 live_trader.py 快手节点负责）")
    logger.info(f"  - 15:10 日终结算与状态落盘")
    logger.info(f"{'='*60}\n")

    # 注册定时任务
    schedule.every().day.at("14:40").do(
        lambda: safe_execute("阶段一：信号生成", _phase_signal_generation)
    )
    schedule.every().day.at("14:50").do(
        lambda: safe_execute("阶段二：订单执行", _phase_order_execution)
    )
    schedule.every().day.at("15:10").do(
        lambda: safe_execute("阶段三：日终结算", _phase_daily_settlement)
    )

    # 主循环：持续检查并执行到期任务
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except Exception as e:
            # 顶层防崩溃：主循环异常捕获
            error_msg = f"守护进程主循环异常: {e}"
            logger.exception(f"[ERROR] {error_msg}")
            send_notification("守护进程崩溃预警", error_msg)
            time.sleep(60)


def _phase_signal_generation():
    """阶段一：信号生成 - 由 brain_node.py 慢脑节点通过 ZeroMQ 广播，此处仅做状态确认"""
    logger.info("信号生成由 brain_node.py 慢脑节点独立运行，守护进程跳过直接调度。")


def _phase_order_execution():
    """阶段二：订单执行 - 由 live_trader.py 快手节点通过 ZeroMQ 监听执行，此处仅做状态确认"""
    logger.info("订单执行由 live_trader.py 快手节点独立运行，守护进程跳过直接调度。")


def _phase_daily_settlement():
    """阶段三：日终结算 - 独立状态快照，不依赖 live_trader"""
    from core.logger_config import logger
    from pathlib import Path
    import json
    from datetime import datetime
    
    logger.info("[日终结算] 开始执行投资组合状态落盘...")
    try:
        cache_file = Path("./data_cache/live_portfolio.json")
        if cache_file.exists():
            with open(cache_file, "r", encoding="utf-8") as f:
                portfolio = json.load(f)
            
            # 日终快照备份
            date_str = datetime.now().strftime("%Y%m%d")
            snapshot_file = Path(f"./data_cache/settlement_{date_str}.json")
            with open(snapshot_file, "w", encoding="utf-8") as f:
                portfolio["settlement_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                json.dump(portfolio, f, ensure_ascii=False, indent=2)
            logger.info(f"[日终结算] 状态快照已落盘: {snapshot_file}")
        else:
            logger.warning("[日终结算] live_portfolio.json 不存在，跳过结算")
    except Exception as e:
        logger.exception(f"[日终结算] 落盘失败: {e}")


if __name__ == "__main__":
    run_daemon()
