"""
实盘交易守护进程 (Agent Daemon)
遵循 A 股物理时间轴调度：
- 14:40 信号生成与路由计算
- 14:50 真实状态扣减与买卖订单执行
- 15:10 日终结算与状态落盘
"""
import time
import schedule
from datetime import datetime

from live_trader import phase_signal_generation, phase_order_execution, phase_daily_settlement
from notifier import send_notification


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
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 非交易日，跳过 {phase_name}")
        return

    try:
        print(f"\n{'='*60}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始执行: {phase_name}")
        print(f"{'='*60}")
        phase_func()
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {phase_name} 执行完毕")
    except Exception as e:
        error_msg = f"[{phase_name}] 执行异常: {e}"
        print(f"[ERROR] {error_msg}")
        send_notification("守护进程预警", error_msg)
        # 异常后 sleep 防止快速重试
        time.sleep(60)


def run_daemon():
    """
    启动守护进程主循环。
    按 A 股物理时间轴调度三阶段任务。
    """
    print(f"\n{'='*60}")
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 实盘守护进程启动")
    print(f"调度计划:")
    print(f"  - 14:40 信号生成与路由计算")
    print(f"  - 14:50 订单执行")
    print(f"  - 15:10 日终结算与状态落盘")
    print(f"{'='*60}\n")

    # 注册定时任务
    schedule.every().day.at("14:40").do(
        lambda: safe_execute("阶段一：信号生成", phase_signal_generation)
    )
    schedule.every().day.at("14:50").do(
        lambda: safe_execute("阶段二：订单执行", phase_order_execution)
    )
    schedule.every().day.at("15:10").do(
        lambda: safe_execute("阶段三：日终结算", phase_daily_settlement)
    )

    # 主循环：持续检查并执行到期任务
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except Exception as e:
            # 顶层防崩溃：主循环异常捕获
            error_msg = f"守护进程主循环异常: {e}"
            print(f"[ERROR] {error_msg}")
            send_notification("守护进程崩溃预警", error_msg)
            time.sleep(60)


if __name__ == "__main__":
    run_daemon()
