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
from core.broker_adapter import MockBrokerAdapter, BaseBroker
from core.order_manager import OrderManager
from core.backtester import CACHE_DIR, download_historical_data
from core.trading_state import get_trading_state, set_trading_state, TradingState

# 动态加载 券商网关 Adapter
BROKER_TYPE = os.environ.get("BROKER_TYPE", "mock").lower()

if BROKER_TYPE == "qmt":
    from core.brokers.qmt_broker import QmtBroker
    qmt_path = os.environ.get("QMT_PATH", r"D:\qmt\userdata_mini")
    account_id = os.environ.get("QMT_ACCOUNT_ID", "")
    broker: BaseBroker = QmtBroker(qmt_path=qmt_path, account_id=account_id)
else:
    broker: BaseBroker = MockBrokerAdapter()

# 挂载全局订单生命周期管理器
order_manager = OrderManager(broker)

def execute_single_order(order):
    """原子化交易执行器（异步委托交由 OrderManager 处理）"""
    code = order["code"]
    action = order["action"]
    shares = order["shares"]
    fill_price = order.get("signal_price", 0.0)
    name = order.get("name", code)
    reason = order.get("reason", "")

    price_type = "限价" if fill_price > 0 else "市价"
    
    try:
        # 1. 提交物理委托
        order_id = broker.place_order(
            code=code, 
            action=action, 
            qty=shares, 
            price_type=price_type, 
            price=fill_price
        )
        # 2. 将订单交由状态机托管对账
        order_manager.add_order(order_id, code, action, shares, reason)
    except Exception as e:
        logger.error(f"❌ [下单失败] {action} {code}: {e}")

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
    logger.info(f"⚙️ 当前运行模式: {BROKER_TYPE.upper()}")
    logger.info("=" * 70)

    # ---------------- 预飞自检 (Pre-flight Check) ----------------
    if BROKER_TYPE == "qmt":
        logger.info("[Pre-flight] 正在进行 QMT 终端预飞自检...")
        try:
            balance_info = broker.get_balance()
            # 必须成功返回非空字典且不能抛出异常
            if balance_info is None:
                raise ValueError("查询资金返回 None")
            logger.info(f"✅ [OK] QMT 仿真/实盘接口已连接，当前可用资金: ¥{balance_info.get('cash', 0):.2f}")
        except Exception as e:
            logger.error(f"❌ [FATAL] QMT 客户端未连接或未登录，交易快手启动中止！原因: {e}")
            sys.exit(1)
    else:
        logger.info("✅ [OK] 运行在 Mock 模式，跳过 QMT 自检。")
    # -------------------------------------------------------------

    # 挂载 ZeroMQ 监听器
    context = zmq.Context()
    
    # 战术总线 (PUB/SUB 5555)
    socket = context.socket(zmq.SUB)
    socket.connect("tcp://127.0.0.1:5555")
    socket.setsockopt_string(zmq.SUBSCRIBE, "TRADE_SIGNAL")

    # 控制总线 (REQ/REP 5556)
    control_socket = context.socket(zmq.REP)
    control_socket.bind("tcp://127.0.0.1:5556")

    settled_today = False
    logger.info("[快手] 正在监听大模型战术指挥网 (TCP 5555)...")
    logger.info("[快手] 正在监听全局风控控制总线 (TCP 5556)...")

    executed_ids = OrderedDict()
    max_cache_size = 500

    while True:
        now = datetime.now()
        
        # 0. 优先级最高：控制总线监听
        try:
            ctrl_msg = control_socket.recv_string(flags=zmq.NOBLOCK)
            try:
                ctrl_data = json.loads(ctrl_msg)
                cmd = ctrl_data.get("command")
                if cmd == "FREEZE":
                    set_trading_state(TradingState.FROZEN)
                    logger.critical("🛑 [风控] 收到控制总线冻结指令，系统已紧急锁定！")
                    control_socket.send_string(json.dumps({"status": "ACK", "state": "FROZEN"}))
                elif cmd == "MANUAL_ORDER":
                    order = ctrl_data.get("order")
                    logger.warning(f"⚡ [风控] 收到手动干预直达订单: {order.get('action')} {order.get('code')}")
                    execute_single_order(order)
                    control_socket.send_string(json.dumps({"status": "ACK"}))
                else:
                    control_socket.send_string(json.dumps({"status": "NACK", "reason": "Unknown command"}))
            except Exception as e:
                logger.error(f"处理控制总线消息失败: {e}")
                control_socket.send_string(json.dumps({"status": "ERROR", "reason": str(e)}))
        except zmq.Again:
            pass

        # 1. 极速非阻塞监听 (0.01秒心跳)
        try:
            message = socket.recv_string(flags=zmq.NOBLOCK)
            _, payload = message.split(" ", 1)
            order = json.loads(payload)
            
            # 物理阻断检查
            current_state = get_trading_state()
            if current_state in [TradingState.FROZEN.value, TradingState.EMERGENCY.value]:
                if order.get("action") == "BUY":
                    logger.warning(f"🛑 [风控拦截] 系统状态为 {current_state}，已物理丢弃战术买入指令: {order.get('code')}")
                    continue
            
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

        # 4. 高频订单对账与状态跃迁
        try:
            order_manager.sync_orders()
        except Exception as e:
            logger.error(f"[OrderManager] 对账异常: {e}")

        time.sleep(0.01)

if __name__ == "__main__":
    run_fast_hand()