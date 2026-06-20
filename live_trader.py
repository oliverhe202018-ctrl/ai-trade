"""
实盘快手节点 (Fast Hand)
职责：毫秒级监听 ZeroMQ 战术总线，瞬间执行 QMT 交易，15:05 自动日终结算。
"""
import os
import sys
import time
import json
import zmq
from datetime import datetime
from collections import OrderedDict
from dotenv import load_dotenv

# 添加项目根目录到 Python 路径，确保 core 和 feeds 模块可被引用
PROJECT_ROOT = os.path.dirname(__file__)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv()
from core.logger_config import logger
from core.state_manager import load_portfolio, save_portfolio
from core.trade_engine import MockBroker, QMTBroker, BaseBroker
from core.backtester import CACHE_DIR, download_historical_data

# 券商网关 (保持你的 MockBroker 设定)
broker: BaseBroker = MockBroker(
    state_file=os.path.join(CACHE_DIR, "live_portfolio.json"),
    position_key="positions",
    slippage_rate=0.001
)

def execute_single_order(order):
    """原子化交易执行器"""
    code = order["code"]
    action = order["action"]
    shares = order["shares"]
    fill_price = order["signal_price"]
    name = order.get("name", code)
    reason = order.get("reason", "")

    if action == "SELL":
        result = broker.sell(code=code, shares=shares, price=fill_price, name=name, reason=reason)
        if result["success"]:
            logger.info(f"✅ [卖出成功] {code} @ {fill_price:.2f} x {shares}股")
    elif action == "BUY":
        result = broker.buy(code=code, shares=shares, price=fill_price, name=name, reason=reason)
        if result["success"]:
            logger.info(f"✅ [买入成功] {code} @ {fill_price:.2f} x {shares}股")

def phase_daily_settlement():
    """阶段三：15:05 日终结算"""
    logger.info(f"\n[{datetime.now().strftime('%H:%M:%S')}] === 日终资产结算 ===")
    portfolio = load_portfolio()
    daily_market_value = 0
    
    # 极速获取一次收盘价用于计算净值
    for code, pos in portfolio["positions"].items():
        try:
            _, quotes, _ = download_historical_data(code, days=1)
            latest_price = quotes[0]["price"] if quotes else pos["avg_price"]
            daily_market_value += pos["shares"] * latest_price
            pos["holding_days"] = pos.get("holding_days", 0) + 1
        except Exception:
            daily_market_value += pos["shares"] * pos["avg_price"]

    total_equity = portfolio["cash"] + daily_market_value
    save_portfolio(portfolio)
    logger.info(f"结算完成。当日净值: ¥{total_equity:.2f} | 现金: ¥{portfolio['cash']:.2f}")

def run_fast_hand():
    logger.info("=" * 70)
    logger.info("⚡ 实盘快手节点 (Fast Hand) 已启动")
    logger.info("=" * 70)

    # 挂载 ZeroMQ 监听器
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.connect("tcp://127.0.0.1:5555")
    socket.setsockopt_string(zmq.SUBSCRIBE, "TRADE_SIGNAL")

    settled_today = False
    logger.info("[快手] 正在监听大模型战术指挥网 (TCP 5555)...")

    executed_ids = OrderedDict()
    max_cache_size = 500

    while True:
        now = datetime.now()
        
        # 1. 极速非阻塞监听 (0.01秒心跳)
        try:
            message = socket.recv_string(flags=zmq.NOBLOCK)
            _, payload = message.split(" ", 1)
            order = json.loads(payload)
            
            # 校验指纹 ID 幂等性
            order_id = order.get("order_id")
            if order_id:
                if order_id in executed_ids:
                    executed_ids.move_to_end(order_id)
                    logger.warning(f"⚠️ [幂等拦截] 截获重复指令 ID: {order_id}，已自动丢弃并忽略")
                    continue
                executed_ids[order_id] = True
                if len(executed_ids) > max_cache_size:
                    executed_ids.popitem(last=False)
            
            logger.info(f"\n🎯 [瞬时截获指令] {order['action']} {order['code']} ({order['reason']})")
            
            # 瞬间开火
            execute_single_order(order)
        except zmq.Again:
            pass # 当前无信号，放行

        # 2. 触发日终结算 (15:05)
        if now.hour == 15 and now.minute >= 5 and not settled_today:
            phase_daily_settlement()
            settled_today = True

        # 3. 跨日重置
        if now.hour == 9 and now.minute < 10:
            settled_today = False

        time.sleep(0.01)

if __name__ == "__main__":
    run_fast_hand()