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
import jsonschema

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
order_manager = OrderManager(broker, is_mock=(BROKER_TYPE == "mock"))

def final_execution_gate(order, current_state, source):
    from core.trading_state import TradingState
    code = order.get("code") or order.get("symbol")
    action = order.get("action")
    shares = order.get("shares", 100)
    
    if not code or not action:
        logger.warning(f"🛑 [FINAL_EXECUTION_GATE] 拦截: 缺少 code 或 action (Source: {source})")
        if source == "MANUAL_ORDER":
            logger.warning("🛑 [MANUAL_ORDER_BLOCK] 手动发单指令缺少必要字段")
        return False
        
    action_upper = str(action).upper()
    if action_upper not in ["BUY", "SELL", "REDUCE", "CONFIRM", "VETO"]:
        logger.warning(f"🛑 [FINAL_EXECUTION_GATE] 拦截: 非法 action {action} (Source: {source})")
        if source == "MANUAL_ORDER":
            logger.warning(f"🛑 [MANUAL_ORDER_BLOCK] 手动发单指令异常")
        return False
        
    if "shares" in order and shares <= 0:
        logger.warning(f"🛑 [FINAL_EXECUTION_GATE] 拦截: shares <= 0 (Source: {source})")
        if source == "MANUAL_ORDER":
            logger.warning("🛑 [MANUAL_ORDER_BLOCK] 交易数量非法")
        return False
        
    if current_state in [TradingState.FROZEN.value, TradingState.EMERGENCY.value, "FROZEN", "EMERGENCY"]:
        if action_upper in ["BUY", "CONFIRM"]:
            if "EMERGENCY" in str(current_state):
                logger.warning(f"🛑 [EMERGENCY_BLOCK] 紧急锁定，彻底阻断买入 {code} (Source: {source})")
            else:
                logger.warning(f"🛑 [FROZEN_BLOCK] 系统冻结，彻底阻断买入 {code} (Source: {source})")
                
            if source == "MANUAL_ORDER":
                logger.warning(f"🛑 [MANUAL_ORDER_BLOCK] 手动干预被当前状态 {current_state} 强制拦截！")
            return False
        elif action_upper in ["SELL", "REDUCE"]:
            logger.warning(f"⚠️ [FINAL_EXECUTION_GATE] 允许 {current_state} 状态下的减仓/卖出动作: {code} (Source: {source})")
            return True
        else:
            if "EMERGENCY" in str(current_state):
                logger.warning(f"🛑 [EMERGENCY_BLOCK] 阻断未知 action: {action} (Source: {source})")
            else:
                logger.warning(f"🛑 [FROZEN_BLOCK] 阻断未知 action: {action} (Source: {source})")
            return False
            
    return True

def execute_single_order(order):
    """原子化交易执行器（异步委托交由 OrderManager 处理）"""
    code = order.get("code") or order.get("symbol")
    action = order["action"]
    shares = order.get("shares", 100)
    fill_price = order.get("signal_price", 0.0)
    name = order.get("name", code)
    reason = order.get("reason", "")
    decision_id = order.get("decision_id", "")
    event_ids = order.get("event_ids", [])
    source = order.get("source", "unknown")
    trade_id = order.get("trade_id", "")
    
    if not trade_id:
        logger.warning(f"[TRADE_TRACE_MISSING_TRADE_ID] {action} {code} missing trade_id")

    price_type = "限价" if fill_price > 0 else "市价"
    
    # [新增风控阻断] 下单前强阻断逻辑
    # [新增风控阻断] 下单前强阻断逻辑
    try:
        asset_data = broker.get_balance()
        if not asset_data:
            logger.error(f"❌ [下单失败] 资金获取失败，无法获取真实的资产结构")
            return False
            
        # 根据 xtquant 或 dict 数据结构进行安全的属性/字典访问
        if isinstance(asset_data, dict):
            available_cash = asset_data.get('cash')
        else:
            available_cash = getattr(asset_data, 'm_dAvailable', getattr(asset_data, 'cash', None))
            
        if available_cash is None:
            logger.error(f"❌ [下单失败] 资金获取失败，不存在合法的可用资金字段: {asset_data}")
            return False
    except Exception as e:
        logger.error(f"❌ [下单失败] 资金校验异常: {e}")
        return False
        
    try:
        # 1. 提交物理委托
        order_id = broker.place_order(
            code=code, 
            action=action, 
            qty=shares, 
            price_type=price_type, 
            price=fill_price
        )
        
        import json
        audit_log = {
            "order_id": order_id,
            "trade_id": trade_id,
            "decision_id": decision_id,
            "event_ids": event_ids,
            "source": source,
            "symbol": code,
            "action": action,
            "shares": shares,
            "event_type": "TRADE_EXECUTION"
        }
        logger.info(f"[TRADE_TRACE] {json.dumps(audit_log, ensure_ascii=False)}")
        
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

    portfolio.setdefault("cash", 100_000.0)
    portfolio.setdefault("positions", {})
    total_equity = portfolio["cash"] + daily_market_value
    save_portfolio(portfolio)
    logger.info(f"结算完成。当日净值: ¥{total_equity:.2f} | 现金: ¥{portfolio['cash']:.2f}")

TRADE_SIGNAL_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "symbol": {"type": "string"},
        "action": {"type": "string", "enum": ["BUY", "SELL", "REDUCE", "VETO", "HOLD"]},
        "source": {"type": "string", "minLength": 1},
        "trade_id": {"type": "string", "minLength": 1},
        "decision_id": {"type": "string"},
        "event_ids": {"type": "array", "items": {"type": ["integer", "string"]}},
        "shares": {"type": ["number", "integer"], "minimum": 0},
        "signal_price": {"type": "number", "minimum": 0},
        "timestamp": {"type": "string"}
    },
    "required": ["action", "source", "trade_id", "decision_id", "event_ids", "shares", "signal_price", "timestamp"],
    "anyOf": [
        {"required": ["code"]},
        {"required": ["symbol"]}
    ]
}

executed_ids = OrderedDict()
MAX_CACHE_SIZE = 500

def process_trade_signal_message(topic, payload):
    try:
        order = json.loads(payload)
        jsonschema.validate(instance=order, schema=TRADE_SIGNAL_SCHEMA)
    except jsonschema.ValidationError as ve:
        logger.error(f"[TRADE_SIGNAL_SCHEMA_FAIL] ZMQ message schema invalid: {ve.message}")
        return
    except json.JSONDecodeError as e:
        logger.error(f"[TRADE_SIGNAL_SCHEMA_FAIL] Invalid JSON payload: {e}")
        return
        
    code = order.get("code") or order.get("symbol")
    if not code:
        logger.error("[TRADE_SIGNAL_SCHEMA_FAIL] Missing code/symbol")
        return
    order["code"] = code
    
    action = order.get("action", "").upper()
    shares = order.get("shares", 0)
    signal_price = order.get("signal_price", 0)
    
    if action in ["BUY", "SELL", "REDUCE"]:
        if shares <= 0:
            logger.error(f"[TRADE_SIGNAL_SCHEMA_FAIL] shares <= 0 for action {action}")
            return
        if signal_price <= 0:
            logger.error(f"[TRADE_SIGNAL_SCHEMA_FAIL] signal_price <= 0 for action {action}")
            return
            
    trade_id = order.get("trade_id")
    if not trade_id:
        logger.error("[TRADE_SIGNAL_SCHEMA_FAIL] Missing trade_id")
        return
        
    if order.get("event_ids") is None:
        logger.error("[TRADE_SIGNAL_SCHEMA_FAIL] event_ids cannot be null")
        return
        
    order["order_id"] = order.get("order_id", trade_id)
    
    try:
        current_state = get_trading_state()
    except Exception as e:
        logger.error(f"[TRADING_STATE_UNAVAILABLE] Failed to get trading state: {e}")
        return
        
    if not final_execution_gate(order, current_state, order.get("source", "TRADE_SIGNAL")):
        return
    
    if action == "VETO":
        logger.info(f"[{code}] Veto signal received, ignoring.")
        return
    
    if trade_id in executed_ids:
        executed_ids.move_to_end(trade_id)
        logger.warning(f"⚠️ [幂等拦截] 截获重复指令 ID: {trade_id}，已自动丢弃并忽略")
        return
    executed_ids[trade_id] = True
    if len(executed_ids) > MAX_CACHE_SIZE:
        executed_ids.popitem(last=False)
    
    logger.info(f"\n🎯 [瞬时截获指令] {action} {code} (Source: {order.get('source')})")
    
    execute_single_order(order)

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
    
    # 战术总线 (PUB/SUB 5555 and 5557)
    socket = context.socket(zmq.SUB)
    socket.setsockopt(zmq.RCVHWM, 1000)
    socket.setsockopt_string(zmq.SUBSCRIBE, "TRADE_SIGNAL")
    
    endpoints = ["tcp://127.0.0.1:5555", "tcp://127.0.0.1:5557"]
    connected_endpoints = []
    for ep in endpoints:
        try:
            socket.connect(ep)
            connected_endpoints.append(ep)
            logger.info(f"[ZMQ_CONNECT] Successfully connected to {ep}")
        except Exception as e:
            logger.error(f"[ZMQ_CONNECT_FAIL] Failed to connect to {ep}: {e}")

    # 控制总线 (REQ/REP 5556)
    control_socket = context.socket(zmq.REP)
    control_socket.bind("tcp://127.0.0.1:5556")

    settled_today = False
    logger.info(f"[快手] 正在监听战术指挥网节点: {connected_endpoints}")
    logger.info("[快手] 正在监听全局风控控制总线 (TCP 5556)...")

    _reconcile_fail_count = 0

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
                    current_state = get_trading_state()
                    if final_execution_gate(order, current_state, "MANUAL_ORDER"):
                        execute_single_order(order)
                        control_socket.send_string(json.dumps({"status": "ACK"}))
                    else:
                        control_socket.send_string(json.dumps({"status": "NACK", "reason": "Blocked by final_execution_gate"}))
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
            parts = message.split(" ", 1)
            if len(parts) == 2:
                topic, payload = parts
                process_trade_signal_message(topic, payload)
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
            # 增加一行调试日志打印真实结构
            asset_data = broker.get_balance()
            logger.info(f"Raw asset data: {asset_data}")
            
            order_manager.sync_orders()
            _reconcile_fail_count = 0
        except Exception as e:
            _reconcile_fail_count += 1
            logger.error(f"[OrderManager] 对账异常: {e}")
            if _reconcile_fail_count >= 5:
                logger.critical(f"🛑 [风控] 连续对账异常达5次，触发系统大盘熔断！")
                set_trading_state(TradingState.FROZEN)
                _reconcile_fail_count = 0

        time.sleep(0.01)

if __name__ == "__main__":
    run_fast_hand()