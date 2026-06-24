"""
交易执行引擎 v3 - 模拟交易 + 真实行情接入 + 收盘结算
支持模拟盘测试，后续可无缝切换券商API

架构: 抽象券商网关 (Broker Gateway) 模式
  - BaseBroker: 抽象基类，强制声明 buy/sell/get_positions/get_cash
  - MockBroker: 模拟撮合层，读写 portfolio.json
  - QMTBroker: 实盘层占位 (QMT/Ptrade 等)
"""
import json
import os
import sys
import tempfile
import logging
import random
import time
from abc import ABC, abstractmethod
from datetime import datetime
from filelock import FileLock, Timeout

try:
    from xtquant import xttrader, xttype, xtdata
    XTQUANT_AVAILABLE = True
except ImportError:
    XTQUANT_AVAILABLE = False


# ============ 结构化日志 ============

from core.logger_config import logger

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ============ 状态文件路径 ============

STATE_FILE = os.path.join(SCRIPT_DIR, "portfolio.json")
LOCK_FILE = os.path.join(SCRIPT_DIR, "portfolio.json.lock")
LOCK_TIMEOUT = 10  # 提升锁超时时间至 10 秒

# TTL 内存缓存（60秒）
_cache = {}
_cache_timestamps = {}
CACHE_TTL = 60  # 缓存有效期（秒）

# ============ 默认初始状态 ============

DEFAULT_STATE = {
    "cash": 50000,
    "position": {},
    "history": [],
    "start_date": datetime.now().strftime("%Y-%m-%d"),
    "trading_count": 0,
    "daily_start_cash": 0,
    "last_settle_date": None,  # 上次结算日期，用于幂等性检查
}


# ============ 核心：原子读写 + 文件锁 ============

def _get_cached_state() -> dict | None:
    """从内存缓存读取状态（带 TTL）"""
    if STATE_FILE in _cache_timestamps:
        if time.time() - _cache_timestamps[STATE_FILE] < CACHE_TTL:
            return _cache.get(STATE_FILE)
    return None


def _set_cached_state(state: dict):
    """写入状态到内存缓存"""
    _cache[STATE_FILE] = state
    _cache_timestamps[STATE_FILE] = time.time()


def _invalidate_cache():
    """清除状态缓存"""
    _cache.pop(STATE_FILE, None)
    _cache_timestamps.pop(STATE_FILE, None)


def _atomic_write(state: dict, state_path: str, lock_path: str) -> None:
    """原子写入: 先写 .tmp 临时文件, 再 os.replace 原子替换。"""
    tmp_path = f"{state_path}.tmp"
    lock = FileLock(lock_path, timeout=LOCK_TIMEOUT)
    with lock:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, state_path)
    # 写入成功后清除缓存
    _invalidate_cache()


def load_state() -> dict:
    """加载交易状态 — 带文件锁保护和脏数据容错 + TTL 缓存。"""
    # 先尝试从缓存读取
    cached = _get_cached_state()
    if cached is not None:
        return cached
    
    if not os.path.exists(STATE_FILE):
        return DEFAULT_STATE.copy()
    lock = FileLock(LOCK_FILE, timeout=LOCK_TIMEOUT)
    with lock:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 写入缓存
            _set_cached_state(data)
            return data


def save_state(state: dict) -> None:
    """保存交易状态 — 原子写入。"""
    _atomic_write(state, STATE_FILE, LOCK_FILE)


def load_state_safe() -> dict:
    """安全加载: 捕获所有异常, 返回默认状态。"""
    try:
        return load_state()
    except Timeout:
        logger.error("状态锁获取超时 (3s)")
        return DEFAULT_STATE.copy()
    except json.JSONDecodeError:
        logger.error("portfolio.json 格式错误")
        return DEFAULT_STATE.copy()
    except FileNotFoundError:
        return DEFAULT_STATE.copy()
    except Exception as e:
        logger.exception(f"加载状态失败: {e}")
        return DEFAULT_STATE.copy()


def save_state_safe(state: dict) -> bool:
    """安全保存: 返回是否成功。"""
    try:
        save_state(state)
        return True
    except Timeout:
        logger.error("保存状态锁超时")
        return False
    except Exception as e:
        logger.exception(f"保存状态失败: {e}")
        return False


# ============ 抽象券商网关 (Broker Gateway) ============

class BaseBroker(ABC):
    """
    券商抽象基类 — 强制声明所有券商必须实现的接口。
    实盘接入 (QMT/Ptrade 等) 时，继承此类并实现具体逻辑。
    """

    @abstractmethod
    def buy(self, code: str, shares: int, price: float, **kwargs) -> dict:
        """
        买入股票。
        :param code: 股票代码
        :param shares: 买入股数
        :param price: 委托价格
        :return: 交易结果 dict，至少包含 {"success": bool, "message": str}
        """
        pass

    @abstractmethod
    def sell(self, code: str, shares: int, price: float, **kwargs) -> dict:
        """
        卖出股票。
        :param code: 股票代码
        :param shares: 卖出股数
        :param price: 委托价格
        :return: 交易结果 dict，至少包含 {"success": bool, "message": str}
        """
        pass

    @abstractmethod
    def get_positions(self) -> dict:
        """
        获取当前持仓。
        :return: 持仓 dict，格式 {code: {"shares": int, "avg_price": float, ...}}
        """
        pass

    @abstractmethod
    def get_cash(self) -> float:
        """
        获取可用现金。
        :return: 现金金额 (float)
        """
        pass


class MockBroker(BaseBroker):
    """
    模拟券商 — 读写 JSON 状态文件进行模拟撮合。
    用于回测/模拟盘测试，后续可无缝切换至真实券商。
    
    :param state_file: 状态文件路径，默认为 portfolio.json
    :param lock_file: 锁文件路径，默认为 state_file + ".lock"
    :param position_key: 持仓字段名，trade_engine 用 "position"，live_trader 用 "positions"
    """

    def __init__(self, state_file: str = None, lock_file: str = None, position_key: str = "position", slippage_rate: float = 0.001):
        self._state_file = state_file or STATE_FILE
        self._lock_file = lock_file or (self._state_file + ".lock")
        self._position_key = position_key
        self.slippage_rate = slippage_rate  # 滑点率，默认千分之一

    def _load_state(self) -> dict:
        """加载交易状态 — 带文件锁保护和脏数据容错 + TTL 缓存。"""
        # 先尝试从缓存读取
        cached = _get_cached_state()
        if cached is not None:
            return cached
        
        if not os.path.exists(self._state_file):
            return DEFAULT_STATE.copy()
        lock = FileLock(self._lock_file, timeout=LOCK_TIMEOUT)
        with lock:
            with open(self._state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 写入缓存
                _set_cached_state(data)
                return data

    def _save_state(self, state: dict) -> bool:
        """保存交易状态 — 原子写入。"""
        tmp_path = f"{self._state_file}.tmp"
        lock = FileLock(self._lock_file, timeout=LOCK_TIMEOUT)
        try:
            with lock:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(state, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self._state_file)
            # 写入成功后清除缓存
            _invalidate_cache()
            return True
        except Exception as e:
            logger.exception(f"保存状态失败: {e}")
            return False

    def buy(self, code: str, shares: int, price: float, **kwargs) -> dict:
        """模拟买入 — 扣减现金、更新持仓、记录历史。"""
        name = kwargs.get("name", code)
        reason = kwargs.get("reason", "")
        strategy = kwargs.get("strategy", "")
        expected_price = kwargs.get("expected_price", 0.0)

        state = self._load_state()
        pos_key = self._position_key
        commission_rate = state.get("_config", {}).get("simulation", {}).get("commission_rate", 0.00025)
        min_commission = state.get("_config", {}).get("simulation", {}).get("min_commission", 5)
        stamp_tax_rate = state.get("_config", {}).get("simulation", {}).get("stamp_tax_rate", 0.001)
        transfer_fee_rate = state.get("_config", {}).get("simulation", {}).get("transfer_fee_rate", 0.00001)

        # 应用滑点：买入时价格上涨
        executed_price = price * (1 + self.slippage_rate)
        
        total_cost = executed_price * shares
        commission = max(int(total_cost * commission_rate), min_commission)
        stamp_tax = max(int(total_cost * stamp_tax_rate), 0)
        transfer_fee = max(int(total_cost * transfer_fee_rate), 1)

        total_cost += commission + stamp_tax + transfer_fee

        if state["cash"] < total_cost:
            msg = f"❌ 资金不足: 需要 ¥{total_cost:.2f}, 可用 ¥{state['cash']:.2f}"
            return {"success": False, "message": msg}

        state["cash"] -= total_cost

        # 确保持仓字段存在
        if pos_key not in state:
            state[pos_key] = {}

        if code in state[pos_key]:
            old = state[pos_key][code]
            total_shares = old["shares"] + shares
            old_cost = old["avg_price"] * old["shares"]
            new_cost = executed_price * shares
            old["avg_price"] = round((old_cost + new_cost) / total_shares, 2)
            old["shares"] = total_shares
        else:
            state[pos_key][code] = {
                "name": name,
                "shares": shares,
                "avg_price": executed_price,
                "buy_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

        state["history"].append({
            "action": "BUY",
            "code": code,
            "name": name,
            "price": executed_price,
            "shares": shares,
            "total_cost": round(total_cost, 2),
            "slippage_rate": self.slippage_rate,
            "slippage_amount": round((executed_price - price) * shares, 2),
            "reason": reason,
            "strategy": strategy,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        state["trading_count"] += 1

        log_trade("BUY", code, name, executed_price, shares, total_cost, strategy=strategy,
                  expected_price=expected_price, actual_fill_price=executed_price, slippage_rate=self.slippage_rate)

        if not self._save_state(state):
            return {"success": False, "message": "❌ 保存状态失败"}
        return {"success": True, "message": f"买入成功 {name} {shares}股"}

    def sell(self, code: str, shares: int, price: float, **kwargs) -> dict:
        """模拟卖出 — 更新持仓、增加现金、记录历史。"""
        name = kwargs.get("name", code)
        reason = kwargs.get("reason", "")
        strategy = kwargs.get("strategy", "")

        state = self._load_state()
        pos_key = self._position_key

        if code not in state.get(pos_key, {}):
            msg = f"❌ 无持仓: {code}"
            return {"success": False, "message": msg}

        pos = state[pos_key][code]
        if pos["shares"] < shares:
            msg = f"❌ 持仓不足: 有{pos['shares']}股，卖{shares}股"
            return {"success": False, "message": msg}

        # 应用滑点：卖出时价格下跌
        executed_price = price * (1 - self.slippage_rate)
        
        total_revenue = executed_price * shares
        commission_rate = state.get("_config", {}).get("simulation", {}).get("commission_rate", 0.00025)
        min_commission = state.get("_config", {}).get("simulation", {}).get("min_commission", 5)
        stamp_tax_rate = state.get("_config", {}).get("simulation", {}).get("stamp_tax_rate", 0.001)
        transfer_fee_rate = state.get("_config", {}).get("simulation", {}).get("transfer_fee_rate", 0.00001)

        commission = max(int(total_revenue * commission_rate), min_commission)
        stamp_tax = int(total_revenue * stamp_tax_rate)
        transfer_fee = max(int(total_revenue * transfer_fee_rate), 1)

        total_revenue -= commission + stamp_tax + transfer_fee

        old_price = pos["avg_price"]
        profit = (executed_price - old_price) * shares - commission - stamp_tax - transfer_fee
        profit_pct = ((executed_price - old_price) / old_price * 100) if old_price > 0 else 0

        state["cash"] += total_revenue

        pos["shares"] -= shares
        if pos["shares"] <= 0:
            del state[pos_key][code]

        state["history"].append({
            "action": "SELL",
            "code": code,
            "name": name,
            "price": executed_price,
            "shares": shares,
            "total_revenue": round(total_revenue, 2),
            "profit": round(profit, 2),
            "profit_pct": round(profit_pct, 2),
            "slippage_rate": self.slippage_rate,
            "slippage_amount": round((price - executed_price) * shares, 2),
            "reason": reason,
            "strategy": strategy,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        state["trading_count"] += 1

        log_trade("SELL", code, name, executed_price, shares, total_revenue, profit=profit,
                  strategy=strategy, slippage_rate=self.slippage_rate)

        if not self._save_state(state):
            return {"success": False, "message": "❌ 保存状态失败"}
        return {
            "success": True,
            "message": f"卖出成功 {name} {shares}股 盈亏: {'+' if profit >= 0 else ''}{profit:.2f}元",
            "profit": profit,
        }

    def get_positions(self) -> dict:
        """获取当前持仓 — 从状态文件读取。"""
        try:
            state = self._load_state()
            return state.get(self._position_key, {})
        except Exception:
            return {}

    def get_cash(self) -> float:
        """获取可用现金 — 从状态文件读取。"""
        try:
            state = self._load_state()
            return state.get("cash", 0.0)
        except Exception:
            return 0.0


class QMTBroker(BaseBroker):
    """
    QMT 实盘券商 — 通过 xtquant 连接 QMT 客户端进行真实交易。
    """

    def __init__(self, account_id: str, mini_qmt_path: str):
        if not XTQUANT_AVAILABLE:
            raise ImportError("xtquant 未安装，无法使用 QMTBroker 实盘模式")
        self.account_id = account_id
        self.mini_qmt_path = mini_qmt_path
        session_id = random.randint(100000, 999999)
        self.trader = xttrader.XtQuantTrader(mini_qmt_path, session=session_id)
        self.account = xttrader.StockAccount(account_id)
        self.trader.start()
        self.trader.connect()
        self.trader.subscribe(self.account)
        logger.info(f"QMTBroker 已连接: account={account_id}, session={session_id}")

    @staticmethod
    def _to_qmt_code(code: str) -> str:
        """将内部代码格式转换为 QMT 格式: sh600519 -> 600519.SH, sz000001 -> 000001.SZ"""
        code = code.strip().lower()
        if code.startswith("sh"):
            return f"{code[2:]}.SH"
        elif code.startswith("sz"):
            return f"{code[2:]}.SZ"
        return code

    def get_cash(self) -> float:
        """查询可用资金。"""
        try:
            asset = self.trader.query_stock_asset(self.account)
            if asset:
                return float(asset.cash)
            return None
        except Exception as e:
            logger.exception(f"QMT 查询资金失败: {e}")
            return None

    def get_positions(self) -> dict:
        """查询可用持仓。"""
        try:
            positions = self.trader.query_stock_positions(self.account)
            result = {}
            if positions:
                for pos in positions:
                    if pos.volume > 0:
                        result[pos.stock_code] = {
                            "shares": pos.volume,
                            "avg_price": pos.avg_price,
                            "can_use_volume": pos.can_use_volume,
                        }
            return result
        except Exception as e:
            logger.exception(f"QMT 查询持仓失败: {e}")
            return {}

    def buy(self, code: str, shares: int, price: float, **kwargs) -> dict:
        """QMT 实盘买入。"""
        qmt_code = self._to_qmt_code(code)
        try:
            # 同标的重复委托拦截
            active_orders = self.trader.query_stock_orders(self.account)
            for order in active_orders:
                if order.stock_code == qmt_code and order.order_status in [xttype.XT_ORDER_UNREPORTED, xttype.XT_ORDER_REPORTED]:
                     raise PermissionError(f"DUPLICATE_ORDER_BLOCK: {qmt_code}")

            # 涨跌停拒单拦截
            tick = xtdata.get_full_tick([qmt_code])
            if tick and qmt_code in tick:
                if tick[qmt_code]['lastPrice'] >= tick[qmt_code]['limit_up']:
                    return {"success": False, "message": f"REJECT_BUY_LIMIT_UP: {qmt_code}"}

            order_id = self.trader.order_stock_async(
                self.account, qmt_code, xttype.STOCK_BUY, shares, 0, price, ""
            )
            logger.info(f"QMT 买入委托: {qmt_code} {shares}股 @ {price} -> order_id={order_id}")
            return {"success": True, "message": f"QMT 买入委托已提交 {qmt_code} {shares}股", "order_id": order_id}
        except Exception as e:
            logger.exception(f"QMT 买入失败: {e}")
            return {"success": False, "message": f"QMT 买入失败: {e}"}


    def sell(self, code: str, shares: int, price: float, **kwargs) -> dict:
        """QMT 实盘卖出。"""
        qmt_code = self._to_qmt_code(code)
        try:
            # 同标的重复委托拦截
            active_orders = self.trader.query_stock_orders(self.account)
            for order in active_orders:
                if order.stock_code == qmt_code and order.order_status in [xttype.XT_ORDER_UNREPORTED, xttype.XT_ORDER_REPORTED]:
                     raise PermissionError(f"DUPLICATE_ORDER_BLOCK: {qmt_code}")

            # 涨跌停拒单拦截
            tick = xtdata.get_full_tick([qmt_code])
            if tick and qmt_code in tick:
                if tick[qmt_code]['lastPrice'] <= tick[qmt_code]['limit_down']:
                    return {"success": False, "message": f"REJECT_SELL_LIMIT_DOWN: {qmt_code}"}

            order_id = self.trader.order_stock_async(
                self.account, qmt_code, xttype.STOCK_SELL, shares, 0, price, ""
            )
            logger.info(f"QMT 卖出委托: {qmt_code} {shares}股 @ {price} -> order_id={order_id}")
            return {"success": True, "message": f"QMT 卖出委托已提交 {qmt_code} {shares}股", "order_id": order_id}
        except Exception as e:
            logger.exception(f"QMT 卖出失败: {e}")
            return {"success": False, "message": f"QMT 卖出失败: {e}"}



# ============ 全局默认 broker 实例 ============

# 默认使用模拟券商，实盘时切换为 QMTBroker()
_default_broker = MockBroker()


def get_default_broker() -> BaseBroker:
    """获取全局默认 broker 实例。"""
    return _default_broker


def set_default_broker(broker: BaseBroker) -> None:
    """设置全局默认 broker 实例 (用于切换实盘)。"""
    global _default_broker
    _default_broker = broker


# ============ 结构化交易日志 ============

def log_trade(action: str, code: str, name: str, price: float, quantity: int,
              total_cost: float = 0.0, profit: float = 0.0,
              strategy: str = "", reason: str = "", **extra):
    """结构化记录交易日志到 JSON。"""
    record = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "code": code,
        "name": name,
        "price": price,
        "quantity": quantity,
        "total_cost": round(total_cost, 2),
        "profit": round(profit, 2),
        "strategy": strategy,
        "reason": reason,
    }
    record.update(extra)
    log_file = os.path.join(LOG_DIR, f"json_{datetime.now().strftime('%Y%m%d')}.jsonl")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info(json.dumps(record, ensure_ascii=False))


# ============ 实时行情 ============

def get_current_price(code: str) -> float | None:
    """获取实时价格 — 优先腾讯接口，失败用买入价。"""
    try:
        from market_data import get_realtime_quotes
        quotes = get_realtime_quotes()
        for q in quotes:
            if q["code"] == code:
                return q["price"]
    except Exception as e:
        logger.exception(f"行情获取失败: {e}")

    # 回退到买入价
    state = load_state()
    if code in state.get("position", {}):
        return state["position"][code]["avg_price"]
    return None


# ============ 交易操作 (委托给默认 broker) ============

def buy_stock(code: str, name: str, price: float, shares: int,
              reason: str = "", strategy: str = "", expected_price: float = 0.0):
    """
    买入股票 — 委托给默认 broker。
    保留此函数以兼容 CLI 和旧调用方式。
    """
    result = _default_broker.buy(
        code=code,
        shares=shares,
        price=price,
        name=name,
        reason=reason,
        strategy=strategy,
        expected_price=expected_price,
    )
    return result["message"]


def sell_stock(code: str, name: str, price: float, shares: int,
               reason: str = "", strategy: str = ""):
    """
    卖出股票 — 委托给默认 broker。
    保留此函数以兼容 CLI 和旧调用方式。
    """
    result = _default_broker.sell(
        code=code,
        shares=shares,
        price=price,
        name=name,
        reason=reason,
        strategy=strategy,
    )
    return result["message"]


# ============ 收盘结算 (收盘后调用) ============

def daily_settlement() -> dict:
    """
    收盘结算: 计算当日盈亏、更新日初资金标记。
    返回结算报告 dict。
    幂等性保证: 同一天多次调用只会执行一次结算。
    """
    state = load_state_safe()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    # 幂等性检查: 如果今天已经结算过，直接返回缓存结果
    last_settle_date = state.get("last_settle_date")
    if last_settle_date == today_str:
        logger.info(f"今日 ({today_str}) 已结算，跳过重复调用")
        # 返回一个标记性的报告
        return {
            "date": now.strftime("%Y-%m-%d %H:%M:%S"),
            "status": "skipped",
            "message": f"今日已结算，无需重复执行",
        }

    start_cash = state.get("daily_start_cash", state["cash"])
    end_cash = state["cash"]

    # 更新日初资金标记
    state["daily_start_cash"] = end_cash
    # 记录结算日期
    state["last_settle_date"] = today_str
    save_state(state)

    # 计算持仓盈亏
    positions = state.get("position", {})
    unrealized_pnl = 0
    position_details = []
    for code, pos in positions.items():
        current_price = get_current_price(code)
        if current_price is not None:
            value = current_price * pos["shares"]
            cost = pos["avg_price"] * pos["shares"]
            pnl = value - cost
            unrealized_pnl += pnl
            position_details.append({
                "code": code,
                "name": pos["name"],
                "shares": pos["shares"],
                "buy_price": pos["avg_price"],
                "current_price": current_price,
                "unrealized_pnl": round(pnl, 2),
            })

    total_assets = end_cash + sum(p["unrealized_pnl"] + p["cost"] for p in position_details)
    # 简化：用当前现金 + 持仓市值计算
    market_value = sum(
        get_current_price(c) * p["shares"] if get_current_price(c) else p["avg_price"] * p["shares"]
        for c, p in positions.items()
    )
    realized_pnl = end_cash - start_cash - market_value

    report = {
        "date": now.strftime("%Y-%m-%d %H:%M:%S"),
        "start_cash": round(start_cash, 2),
        "end_cash": round(end_cash, 2),
        "market_value": round(market_value, 2),
        "total_assets": round(end_cash + market_value, 2),
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "trading_count": state.get("trading_count", 0),
        "position_details": position_details,
    }
    logger.info(json.dumps(report, ensure_ascii=False))
    return report


# ============ 全局风控断路器 ============

def check_circuit_breaker(state: dict, config: dict) -> bool:
    """
    检查账户级日最大回撤熔断。
    触发后返回 True，阻止当日买入。
    """
    risk = config.get("risk", {})
    max_dd = risk.get("max_daily_drawdown", -3)
    start_cash = state.get("daily_start_cash", state["cash"])

    # 计算当前回撤
    positions = state.get("position", {})
    market_value = sum(
        get_current_price(c) * p["shares"] if get_current_price(c) else p["avg_price"] * p["shares"]
        for c, p in positions.items()
    )
    current_value = state["cash"] + market_value
    drawdown = (current_value - start_cash) / start_cash * 100 if start_cash > 0 else 0

    if drawdown <= max_dd:
        logger.warning(f"⚠️ 风控熔断: 日回撤 {drawdown:.2f}% <= {max_dd}%")
        return True
    return False


# ============ 状态报告 ============

def get_portfolio_report() -> str:
    """生成账户报告。"""
    state = load_state_safe()

    positions = state.get("position", {})
    market_value = sum(
        get_current_price(c) * p["shares"] if get_current_price(c) else p["avg_price"] * p["shares"]
        for c, p in positions.items()
    )
    total_assets = state["cash"] + market_value
    total_profit = total_assets - state.get("initial_capital", 50000)
    total_profit_pct = (total_profit / 50000 * 100) if 50000 > 0 else 0

    report = f"""
==================================================
📊 AI 炒股状态报告
==================================================
💰 现金: ¥{state['cash']:.2f}
📈 持仓市值: ¥{market_value:.2f}
🏦 总资产: ¥{total_assets:.2f}
📊 累计盈亏: ¥{total_profit:.2f} ({total_profit_pct:+.2f}%)
📋 持仓数: {len(positions)}
🔄 交易次数: {state.get('trading_count', 0)}
==================================================
"""

    if positions:
        report += "\n📋 当前持仓:\n"
        for code, pos in positions.items():
            current_price = get_current_price(code)
            if current_price:
                value = current_price * pos["shares"]
                cost = pos["avg_price"] * pos["shares"]
                pnl = value - cost
                pnl_pct = (pnl / cost * 100) if cost > 0 else 0
                sign = "+" if pnl >= 0 else ""
                report += f"  {code}: {pos['name']} {pos['shares']}股 @ ¥{pos['avg_price']:.2f} (现价¥{current_price:.2f}) {sign}{pnl:.2f}元 ({sign}{pnl_pct:.2f}%)\n"

    return report


# ============ CLI 入口 ============

def main():
    if len(sys.argv) < 2:
        logger.info("用法:")
        logger.info("  python trade_engine.py status              # 查看账户状态")
        logger.info("  python trade_engine.py buy CODE NAME PRICE SHARES REASON")
        logger.info("  python trade_engine.py sell CODE NAME PRICE SHARES REASON")
        logger.info("  python trade_engine.py settle              # 收盘结算")
        return

    cmd = sys.argv[1]

    if cmd == "status":
        logger.info(get_portfolio_report())
    elif cmd == "buy":
        if len(sys.argv) < 7:
            logger.info("用法: buy CODE NAME PRICE SHARES REASON")
            return
        result = buy_stock(
            sys.argv[2], sys.argv[3],
            float(sys.argv[4]), int(sys.argv[5]),
            sys.argv[6] if len(sys.argv) > 6 else ""
        )
        logger.info(result)
    elif cmd == "sell":
        if len(sys.argv) < 7:
            logger.info("用法: sell CODE NAME PRICE SHARES REASON")
            return
        result = sell_stock(
            sys.argv[2], sys.argv[3],
            float(sys.argv[4]), int(sys.argv[5]),
            sys.argv[6] if len(sys.argv) > 6 else ""
        )
        logger.info(result)
    elif cmd == "settle":
        report = daily_settlement()
        logger.info(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        logger.info(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()
