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
import jsonschema

# 添加项目根目录到 Python 路径，确保 core 和 feeds 模块可被引用
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── .env 安全加载（防 null 字符 ValueError，Windows <frozen os> 限制）──
try:
    from dotenv import dotenv_values
    for _k, _v in dotenv_values().items():
        if _v is not None and "\x00" not in _k and "\x00" not in _v:
            os.environ.setdefault(_k, _v)
except Exception as _e:
    print(f"[DOTENV_ERROR] .env 加载异常，继续运行: {_e}")

from core.logger_config import logger
from core.utils import retry_with_backoff
from core.state_manager import load_portfolio, save_portfolio
from core.broker_adapter import MockBrokerAdapter, BaseBroker
from core.order_manager import OrderManager
import traceback
from core.trading_state import set_trading_state, TradingState
from feeds.qmt_market_provider import QMTMarketProvider

try:
    market_provider = QMTMarketProvider()
except Exception as e:
    logger.critical(f"行情通道初始化失败，系统启动降级！{e}")
    set_trading_state(TradingState.FROZEN)
    market_provider = None

from core.backtester import CACHE_DIR, download_historical_data
from core.trading_state import get_trading_state, set_trading_state, TradingState

# 动态加载 券商网关 Adapter
BROKER_TYPE = os.environ.get("BROKER_TYPE", "mock").lower()

if BROKER_TYPE == "mock":
    broker: BaseBroker = MockBrokerAdapter()
    logger.info("[BROKER] Mock 模式启动，使用虚拟撮合引擎。")
else:
    try:
        from core.qmt_adapter import QMTBrokerAdapter
        broker: BaseBroker = QMTBrokerAdapter()
        logger.info("[BROKER] QMT 实盘适配器加载成功。")
    except ImportError as e:
        logger.critical(f"[BROKER] QMT 适配器加载失败，回退 Mock 模式: {e}")
        broker: BaseBroker = MockBrokerAdapter()

order_manager = OrderManager(broker)

# ==========================================
# 📋 JSON Schema 订单验证
# ==========================================
ORDER_SCHEMA = {
    "type": "object",
    "required": ["code", "action", "quantity", "price"],
    "properties": {
        "code":     {"type": "string", "minLength": 8, "maxLength": 12},
        "action":   {"type": "string", "enum": ["BUY", "SELL"]},
        "quantity": {"type": "number", "minimum": 100},
        "price":    {"type": "number", "minimum": 0.01}
    }
}

# ==========================================
# 🔒 对账熔断计数器
# ==========================================
_reconcile_fail_count = 0
_RECONCILE_FAIL_THRESHOLD = 5



@retry_with_backoff()
def safe_place_order(broker_instance, order_dict):
    return broker_instance.place_order(order_dict)

def validate_order(order: dict) -> bool:
    """校验订单格式合法性"""
    try:
        jsonschema.validate(instance=order, schema=ORDER_SCHEMA)
        return True
    except jsonschema.ValidationError as e:
        logger.warning(f"[ORDER_SCHEMA] 订单格式非法: {e.message} | order={order}")
        return False


def get_asset_data_mock_safe() -> dict:
    """
    Mock 模式下从 MockBrokerAdapter 获取资产数据；
    实盘模式下走真实接口，不存在时返回保守空字典。
    """
    try:
        if BROKER_TYPE == "mock":
            bal = broker.balance if hasattr(broker, "balance") else {}
            return {
                "cash": bal.get("cash", 1_000_000.0),
                "total_equity": bal.get("total_equity", bal.get("cash", 1_000_000.0)),
                "market_value": bal.get("market_value", 0.0),
            }
        else:
            return broker.get_balance()
    except Exception as e:
        logger.warning(f"[ASSET_DATA] 获取资产数据失败，返回保守空字典: {e}")
        return {"cash": 0.0, "total_equity": 0.0, "market_value": 0.0}


def phase_daily_settlement():
    """日终结算：标记持仓、统计盈亏、重置状态"""
    logger.info("[SETTLEMENT] 开始日终结算...")
    portfolio = load_portfolio()
    if not portfolio:
        logger.warning("[SETTLEMENT] 无法读取组合，跳过结算。")
        return

    positions = portfolio.get("positions", {})
    cash = portfolio.get("cash", 0.0)
    total_profit = 0.0

    for code, pos in positions.items():
        cost = pos.get("avg_cost", 0) * pos.get("quantity", 0)
        current_val = pos.get("current_price", pos.get("avg_cost", 0)) * pos.get("quantity", 0)
        profit = current_val - cost
        total_profit += profit
        logger.info(f"[SETTLEMENT] {code}: 持仓={pos.get('quantity')} 成本={cost:.2f} 市值={current_val:.2f} 盈亏={profit:.2f}")

    logger.info(f"[SETTLEMENT] 现金={cash:.2f} 持仓盈亏合计={total_profit:.2f} 净值≈{cash + total_profit:.2f}")
    save_portfolio(portfolio)
    logger.info("[SETTLEMENT] 日终结算完成。")



# --- WATCHDOG HEARTBEAT ---
def _update_heartbeat():
    import json, os, time
    from filelock import FileLock
    hb_file = os.path.join(PROJECT_ROOT, "data_cache", "heartbeats.json")
    lock_file = hb_file + ".lock"
    os.makedirs(os.path.dirname(hb_file), exist_ok=True)
    try:
        from filelock import FileLock
        with FileLock(lock_file, timeout=2):
            data = dict()
            if os.path.exists(hb_file):
                try:
                    with open(hb_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except:
                    pass
            data["live_trader"] = time.time()
            with open(hb_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
    except Exception as e:
        from core.logger_config import logger
        logger.warning(f"[HEARTBEAT_FAIL] 无法写入心跳文件: {e}")
# --------------------------


# --- TWAP 队列 ---
# 格式: [{"execute_at": 169xxxxxxx, "order": {...}}, ...]
_twap_queue = []

def run_live_trader():
    global _reconcile_fail_count
    global _twap_queue

    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.connect("tcp://localhost:5555")
    socket.setsockopt_string(zmq.SUBSCRIBE, "TRADE_SIGNAL")
    logger.info("✅ ZeroMQ 订阅已连接 tcp://localhost:5555，监听 TRADE_SIGNAL...")

    settlement_done_today = False

    while True:
        now = datetime.now()
        _update_heartbeat()

        # 15:05 日终结算（每日一次）
        if now.hour == 15 and now.minute >= 5 and not settlement_done_today:
            phase_daily_settlement()
            settlement_done_today = True

        if now.hour == 9 and now.minute < 25:
            settlement_done_today = False  # 重置当日结算标志

        # ── 执行 TWAP 队列 ──
        now_ts = time.time()
        ready_orders = [o for o in _twap_queue if o["execute_at"] <= now_ts]
        _twap_queue = [o for o in _twap_queue if o["execute_at"] > now_ts]
        
        for twap_item in ready_orders:
            order = twap_item["order"]
            logger.info(f"[TWAP_EXEC] 执行拆单: {order['code']} {order['action']} qty={order['quantity']}")
            try:
                result = safe_place_order(broker, order)
                order_manager.track_order(result)
                logger.info(f"[TRADE_TRACE] code={order.get('code')} action={order.get('action')} qty={order.get('quantity')} result={result}")
            except Exception as e:
                logger.error(f"[ORDER_EXEC_FAIL] TWAP 下单失败: {e} | order={order}")

        # ── 对账循环 ──
        current_state = get_trading_state()
        try:
            order_manager.sync_orders()
            _reconcile_fail_count = 0
            if current_state in (TradingState.FROZEN.value, TradingState.DEGRADED.value, TradingState.RECOVERING.value):
                logger.info(f"[STATE_TRANSITION] 对账恢复成功，从 {current_state} 恢复为 ACTIVE。")
                set_trading_state(TradingState.ACTIVE)
        except Exception as e:
            _reconcile_fail_count += 1
            logger.error(
                f"[OrderManager] 对账异常: {repr(e)} "
                f"({_reconcile_fail_count}/{_RECONCILE_FAIL_THRESHOLD})"
            )
            if _reconcile_fail_count >= _RECONCILE_FAIL_THRESHOLD:
                if current_state != TradingState.FROZEN.value:
                    logger.critical(f"[STATE_TRANSITION] 对账连续失败，从 {current_state} 降级为 FROZEN！")
                    set_trading_state(TradingState.FROZEN)
                _reconcile_fail_count = 0

        # ── 接收交易信号 ──
        try:
            msg = socket.recv_string(flags=zmq.NOBLOCK)
            _, payload = msg.split(" ", 1)
            order = json.loads(payload)

            if not validate_order(order):
                continue

            trading_state = get_trading_state()
            if trading_state != TradingState.ACTIVE.value:
                if order.get('action') == 'SELL' and trading_state == TradingState.FROZEN.value:
                    logger.warning(f"[FAIL_CLOSE] 状态={trading_state}，但允许安全退出：放行 {order.get('code')} SELL。")
                else:
                    logger.warning(
                        f"[TRADING_STATE_UNAVAILABLE] 状态={trading_state}，"
                        f"信号 {order.get('code')} {order.get('action')} 被阻断。"
                    )
                    continue

            asset_data = get_asset_data_mock_safe()
            cash = asset_data.get('cash', 0)
            
            stale_count = getattr(run_live_trader, "stale_count", 0)
            if market_provider and order.get("action") == "BUY":
                is_fresh = market_provider.is_data_fresh(order['code'], max_delay_seconds=5)
                if not is_fresh:
                    stale_count += 1
                    run_live_trader.stale_count = stale_count
                    logger.warning(f"[{order['code']}] 行情数据滞后或无法获取，取消本次下单 (stale_count: {stale_count})")
                    if stale_count >= 5:
                        logger.critical("行情连续 5 次滞后，触发系统降级 -> FROZEN")
                        set_trading_state(TradingState.FROZEN)
                    continue
                else:
                    run_live_trader.stale_count = 0

            # TWAP 拆单判断：订单金额 > 可用资金 10%
            order_amount = order.get('price', 0) * order.get('quantity', 0)
            if cash > 0 and order_amount > cash * 0.1 and order.get('quantity', 0) >= 300:
                if market_provider:
                    try:
                        ob = market_provider.get_orderbook(order['code'])
                        ask_price = ob.get("askPrice", [0])[0] if ob.get("askPrice") else 0
                        ask_vol = ob.get("askVol", [0])[0] if ob.get("askVol") else 0
                        bid_price = ob.get("bidPrice", [0])[0] if ob.get("bidPrice") else 0
                        bid_vol = ob.get("bidVol", [0])[0] if ob.get("bidVol") else 0
                        top_amount = (ask_price * ask_vol * 100) + (bid_price * bid_vol * 100)
                        if top_amount > 0 and top_amount < 500000:
                            logger.warning(f"[TWAP_CANCEL] {order['code']} 实时盘口深度差 (一档总额 {top_amount:.0f})，取消拆单执行！")
                            continue
                    except Exception as e:
                        logger.error(f"[TWAP_CANCEL] 盘口获取失败: {e}")
                        continue
                
                import random
                split_count = random.randint(3, 5)
                base_qty = (order['quantity'] // split_count) // 100 * 100
                if base_qty >= 100:
                    logger.info(f"[TWAP_SPLIT] 订单金额 {order_amount:.0f} > 10% 可用资金({cash:.0f})，启用 TWAP 拆分为 {split_count} 笔")
                    remaining_qty = order['quantity']
                    for i in range(split_count):
                        qty = base_qty if i < split_count - 1 else remaining_qty
                        if qty <= 0: continue
                        split_order = order.copy()
                        split_order['quantity'] = qty
                        # 间隔 30-90 秒
                        delay = i * random.randint(30, 90)
                        _twap_queue.append({
                            "execute_at": time.time() + delay,
                            "order": split_order
                        })
                        remaining_qty -= qty
                    continue

            logger.info(f"[ORDER_EXEC] 执行: {order} | 可用资金={cash:.2f}")

            try:
                result = safe_place_order(broker, order)
                order_manager.track_order(result)
                logger.info(
                    f"[TRADE_TRACE] code={order.get('code')} action={order.get('action')} "
                    f"qty={order.get('quantity')} price={order.get('price')} "
                    f"result={result}"
                )
            except Exception as e:
                logger.error(f"[ORDER_EXEC_FAIL] 下单失败: {e} | order={order}")

        except zmq.Again:
            pass  # 无新消息，正常
        except Exception as e:
            logger.error(f"[ZMQ_RECV] 消息接收异常: {e}")

        time.sleep(0.1)


if __name__ == "__main__":
    run_live_trader()
