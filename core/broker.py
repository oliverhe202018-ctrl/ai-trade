"""
券商适配器层 v3 - 统一接口，支持模拟盘/真实盘无缝切换
使用方式: 修改 config.yaml 中的 broker_mode 即可切换

支持的券商:
  mock       - 模拟盘（本地行情+本地持仓，默认）
  easytrader - 同花顺 GUI 自动化
  tdx        - 通达信 GUI 自动化
  qmt        - 迅投 MiniQMT

配置项说明:
  broker_mode           - 交易模式 (mock/easytrader/tdx/qmt)
  initial_capital       - 启动资金
  max_position_pct      - 每笔最大仓位比例
  max_positions         - 最大持仓数量
  strategy.type         - 选股策略 (trend/value/momentum)
  sell.stop_loss_pct    - 止损线
  sell.take_profit_pct  - 止盈线
  simulation.*          - 模拟盘手续费和滑点
  live.*                - 实盘交易参数
  easytrader_path       - 同花顺路径 (auto=自动检测)
  tdx_path              - 通达信路径 (auto=自动检测)
"""
import copy
import json
import os
import sys
import yaml
from abc import ABC, abstractmethod
from datetime import datetime

from core.logger_config import logger

# 配置
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "config.yaml")

# 默认配置（当配置文件损坏时使用）
DEFAULT_CONFIG = {
    "broker_mode": "mock",
    "initial_capital": 50000,
    "daily_profit_target": 50,
    "data_source": "tencent",
    "strategy": {
        "type": "trend",
        "safe_gain_range": [-3, 3],
        "exclude_sectors": [],
        "max_positions": 10,
        "max_sector_pct": 30,
        "max_single_pct": 25,
        "max_per_sector": 2,
        "min_buy_amount": 2000,
        "position_sizing": "atr"
    },
    "risk": {
        "stop_loss_pct": -8,
        "take_profit_pct": 10,
        "max_holding_days": 20,
        "max_daily_drawdown": -3,
        "max_total_loss_pct": -10,
        "circuit_breaker": True
    },
    "simulation": {
        "commission_rate": 0.00025,
        "min_commission": 5,
        "stamp_tax_rate": 0.001,
        "transfer_fee_rate": 0.00001,
        "slippage_pct": 0.1,
        "use_realtime": True
    },
    "live": {
        "max_order_count": 10,
        "retry_count": 3,
        "retry_interval": 1,
        "price_strategy": 0,
        "order_timeout": 30,
        "commission_rate": 0.00025,
        "min_commission": 5,
        "stamp_tax_rate": 0.001,
        "transfer_fee_rate": 0.00001,
        "slippage": 0.01
    },
    "qmt": {
        "account_id": "",
        "mini_qmt_path": ""
    },
    "llama": {
        "server_path": "",
        "models": {}
    },
    "easytrader_path": "auto",
    "tdx_path": "auto",
    "notify": {
        "webhook": "",
        "telegram_token": "",
        "chat_id": ""
    },
    "poll_interval": 300
}

def load_config():
    """加载配置 - 使用 PyYAML 标准库"""
    if not os.path.exists(CONFIG_FILE):
        logger.info(f"[警告] 配置文件不存在: {CONFIG_FILE}，使用默认配置")
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # 如果配置文件为空或解析结果为 None，返回默认配置
        if config is None:
            logger.info(f"[警告] 配置文件为空: {CONFIG_FILE}，使用默认配置")
            return DEFAULT_CONFIG.copy()

        # 合并默认配置（确保所有必要的键都存在）
        merged_config = DEFAULT_CONFIG.copy()
        merged_config.update(config)
        return merged_config

    except yaml.YAMLError as e:
        logger.info(f"[错误] 配置文件格式损坏: {CONFIG_FILE}")
        logger.info(f"  YAML 解析错误: {e}")
        logger.info(f"  使用默认配置兜底")
        return DEFAULT_CONFIG.copy()
    except Exception as e:
        logger.exception(f"[错误] 读取配置文件失败: {CONFIG_FILE}")
        logger.info(f"  使用默认配置兜底")
        return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    """
    原子化保存配置到 config/config.yaml。
    
    这是统一配置写入入口 —— auto_tuner.py / update_config.py 等模块通过此函数
    写入配置，确保与 load_config() 的读取路径完全一致，消除"双配置源"问题。
    """
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        tmp_file = CONFIG_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        os.replace(tmp_file, CONFIG_FILE)
        logger.info(f"[配置] 配置已原子化保存: {CONFIG_FILE}")
    except Exception as e:
        logger.exception(f"[错误] 保存配置文件失败: {CONFIG_FILE}")
        raise


def detect_easytrader_path():
    """自动检测同花顺客户端路径"""
    candidates = [
        r"C:\Users\a2515\海软\同花顺\stock\hmstk.exe",
        r"C:\HMSOFT\hmstock\hmstk.exe",
        r"C:\THSHJ\stock\hmstk.exe",
        r"C:\Program Files\同花顺\stock\hmstk.exe",
        r"C:\Program Files (x86)\同花顺\stock\hmstk.exe",
        r"D:\海软\同花顺\stock\hmstk.exe",
        r"D:\同花顺\stock\hmstk.exe",
        r"C:\Users\a2515\AppData\Local\HmStk\stock\hmstk.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

def detect_tdx_path():
    """自动检测通达信客户端路径"""
    candidates = [
        r"C:\通达信\tdx.exe",
        r"C:\Program Files\通达信\tdx.exe",
        r"C:\Program Files (x86)\通达信\tdx.exe",
        r"D:\通达信\tdx.exe",
        r"C:\Users\a2515\通达信\tdx.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None



class BrokerBase(ABC):
    """券商适配器基类"""

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def get_accounts(self):
        pass

    @abstractmethod
    def get_position(self, code):
        pass

    @abstractmethod
    def buy(self, code, price, quantity):
        pass

    @abstractmethod
    def sell(self, code, price, quantity):
        pass

    @abstractmethod
    def get_quotes(self, codes):
        """批量获取实时行情"""
        pass

    @abstractmethod
    def query_funds(self):
        """查询资金"""
        pass

    @abstractmethod
    def query_orders(self):
        """查询委托"""
        pass

    @abstractmethod
    def cancel_order(self, order_id):
        """撤单"""
        pass

    def disconnect(self):
        """断开连接"""
        pass

    def is_alive(self):
        return True


class MockBroker(BrokerBase):
    """模拟券商 - 使用本地行情 + 本地持仓 + 多策略"""

    def __init__(self):
        from trade_engine import load_state, save_state, get_current_price
        self.state_file = os.path.join(os.path.dirname(__file__), "portfolio.json")
        self.state = load_state()
        self.current_price = get_current_price
        self.config = load_config()
        # 模拟盘参数
        sim_cfg = self.config.get("simulation", {})

        def _clean_rate(val, default):
            """清理费率参数: 去除 '%' 符号, 转为 float. """
            try:
                return float(str(val).replace('%', '').strip())
            except (ValueError, TypeError):
                return default

        def _clean_rate_or_int(val, default):
            """清理费率/数量参数: 先试 float, 再试 int, 最后回退 default. """
            try:
                s = str(val).replace('%', '').strip()
                v = float(s)
                # 如果是整数值, 返回 int; 否则返回 float
                return int(v) if v == int(v) else v
            except (ValueError, TypeError):
                return default

        self.commission_rate = _clean_rate(sim_cfg.get("commission_rate", 0.00025), 0.00025)
        self.stamp_tax_rate = _clean_rate(sim_cfg.get("stamp_tax_rate", 0.001), 0.001)
        self.transfer_fee_rate = _clean_rate(sim_cfg.get("transfer_fee_rate", 0.00001), 0.00001)
        self.min_commission = _clean_rate_or_int(sim_cfg.get("min_commission", 5), 5)
        self.slippage_pct = _clean_rate(sim_cfg.get("slippage_pct", 0.1), 0.1)
        # 缓存引用, 避免每次调用重新导入
        self._load_state = load_state
        self._save_state = save_state

    def connect(self):
        return {"status": "connected", "broker": "MockBroker", "msg": "模拟盘已连接"}

    def get_accounts(self):
        return [{
            "account_id": "MOCK",
            "cash": self.state["cash"],
            "total_assets": self.state["cash"] + self._calc_position_value(),
        }]

    def _calc_position_value(self):
        total = 0
        for code, pos in self.state.get("position", {}).items():
            try:
                from market_data import get_realtime_quotes
                quotes = get_realtime_quotes()
                for q in quotes:
                    if q["code"] == code:
                        total += q["price"] * pos["shares"]
                        break
                else:
                    total += pos["avg_price"] * pos["shares"]
            except:
                total += pos["avg_price"] * pos["shares"]
        return total

    def get_position(self, code):
        pos = self.state.get("position", {}).get(code)
        if not pos:
            return None
        try:
            from market_data import get_realtime_quotes
            quotes = get_realtime_quotes()
            for q in quotes:
                if q["code"] == code:
                    return {
                        "code": code,
                        "shares": pos["shares"],
                        "avg_price": pos["avg_price"],
                        "current_price": q["price"],
                        "market_value": q["price"] * pos["shares"],
                        "profit": (q["price"] - pos["avg_price"]) * pos["shares"],
                    }
        except:
            pass
        return {
            "code": code,
            "shares": pos["shares"],
            "avg_price": pos["avg_price"],
            "current_price": pos["avg_price"],
            "market_value": pos["avg_price"] * pos["shares"],
            "profit": 0,
        }

    def buy(self, code, price, quantity):
        # 安全类型转换
        price = float(price)
        quantity = int(quantity)

        # 应用滑点 (模拟盘加价)
        slippage = price * self.slippage_pct / 100
        price += slippage
        
        total_cost = price * quantity
        commission = max(int(total_cost * self.commission_rate), self.min_commission)
        stamp_tax = max(int(total_cost * self.stamp_tax_rate), 0)
        transfer_fee = max(int(total_cost * self.transfer_fee_rate), 1)
        total_cost += commission + stamp_tax + transfer_fee

        if self.state["cash"] < total_cost:
            return {"status": "rejected", "reason": "资金不足"}

        self.state["cash"] -= total_cost

        if code in self.state.get("position", {}):
            old = self.state["position"][code]
            total_shares = old["shares"] + quantity
            old_cost = old["avg_price"] * old["shares"]
            new_cost = price * quantity
            old["avg_price"] = round((old_cost + new_cost) / total_shares, 2)
            old["shares"] = total_shares
        else:
            self.state["position"][code] = {
                "name": code,
                "shares": quantity,
                "avg_price": price,
                "buy_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

        self.state["history"].append({
            "action": "BUY",
            "code": code,
            "price": price,
            "quantity": quantity,
            "total_cost": round(total_cost, 2),
            "slippage": round(slippage * quantity, 2),
            "commission": commission,
            "stamp_tax": stamp_tax,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.state["trading_count"] += 1

        # 原子持久化 (filelock + os.replace)
        self._save_state(self.state)

        return {"status": "filled", "order_id": f"MOCK_{datetime.now().strftime('%Y%m%d%H%M%S')}", "cost": round(total_cost, 2), "price_with_slippage": round(price, 2)}

    def sell(self, code, price, quantity):
        # 安全类型转换
        price = float(price)
        quantity = int(quantity)

        pos = self.state.get("position", {}).get(code)
        if not pos or pos["shares"] < quantity:
            return {"status": "rejected", "reason": "持仓不足"}

        total_revenue = price * quantity
        commission = max(int(total_revenue * 0.00025), 5)
        stamp_tax = int(total_revenue * 0.001)
        transfer_fee = max(int(total_revenue * 0.00001), 1)
        total_revenue -= commission + stamp_tax + transfer_fee

        profit = (price - pos["avg_price"]) * quantity - commission - stamp_tax - transfer_fee

        self.state["cash"] += total_revenue
        pos["shares"] -= quantity
        if pos["shares"] <= 0:
            del self.state["position"][code]

        self.state["history"].append({
            "action": "SELL",
            "code": code,
            "price": price,
            "quantity": quantity,
            "revenue": round(total_revenue, 2),
            "profit": round(profit, 2),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        self.state["trading_count"] += 1

        # 原子持久化 (filelock + os.replace)
        self._save_state(self.state)

        return {"status": "filled", "order_id": f"MOCK_{datetime.now().strftime('%Y%m%d%H%M%S')}", "revenue": round(total_revenue, 2), "profit": round(profit, 2)}

    def get_quotes(self, codes):
        from market_data import get_realtime_quotes
        return get_realtime_quotes()

    def query_funds(self):
        return {
            "cash": self.state["cash"],
            "total_assets": self.state["cash"] + self._calc_position_value(),
        }

    def query_orders(self):
        return self.state.get("history", [])

    def cancel_order(self, order_id):
        return {"status": "cancelled", "order_id": order_id}


class EasyTraderBroker(BrokerBase):
    """同花顺/通达信 GUI 自动化 (easytrader)"""

    def __init__(self, broker_type="ths", user_path=None):
        """
        broker_type: "ths"(同花顺) / "tdx"(通达信)
        user_path: 券商客户端安装路径 (None=自动检测)
        """
        self.broker_type = broker_type
        self.user_path = user_path
        self.client = None

    def connect(self):
        # 自动检测路径
        if self.user_path is None or self.user_path == "auto":
            if self.broker_type == "ths":
                self.user_path = detect_easytrader_path()
            elif self.broker_type == "tdx":
                self.user_path = detect_tdx_path()
        
        if self.user_path is None:
            return {"status": "warning", "broker": f"EasyTrader({self.broker_type})", "msg": "未检测到客户端路径，尝试使用默认路径"}
        else:
            return {"status": "connected", "broker": f"EasyTrader({self.broker_type})", "path": self.user_path}

    def get_accounts(self):
        if not self.client:
            return []
        return self.client.balance

    def get_position(self, code):
        if not self.client:
            return None
        positions = self.client.position
        for pos in positions:
            if pos["证券代码"] == code:
                return pos
        return None

    def buy(self, code, price, quantity):
        if not self.client:
            return {"status": "error", "msg": "未连接"}
        try:
            result = self.client.buy(code, price=price, amount=quantity)
            return {"status": "filled", "result": result}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def sell(self, code, price, quantity):
        if not self.client:
            return {"status": "error", "msg": "未连接"}
        try:
            result = self.client.sell(code, price=price, amount=quantity)
            return {"status": "filled", "result": result}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_quotes(self, codes):
        if not self.client:
            return []
        return self.client.quoter.get(codes)

    def query_funds(self):
        if not self.client:
            return {}
        return self.client.balance

    def query_orders(self):
        if not self.client:
            return []
        return self.client.today_trades

    def cancel_order(self, order_id):
        if not self.client:
            return {"status": "error"}
        try:
            return self.client.cancel_order(order_id)
        except Exception as e:
            return {"status": "error", "msg": str(e)}


class QMTBroker(BrokerBase):
    """迅投 MiniQMT"""

    def __init__(self, xt_path=None, account=None, md_path=None):
        self.xt_path = xt_path or r"C:\国金证券QMT\userdata_mini"
        self.account = account
        self.md_path = md_path

    def connect(self):
        try:
            import xtquant
            from xtquant import xttrader, xtdata
            self.xt_trader = xttrader.XtQuantTrader(self.xt_path, "")
            self.xt_trader.start_forever()
            if self.account:
                self.xt_trader.connect(self.account)
            return {"status": "connected", "broker": "QMT(MiniQMT)"}
        except ImportError:
            return {"status": "error", "msg": "未安装 xtquant: pip install xtquant"}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_accounts(self):
        if not self.xt_trader:
            return []
        return self.xt_trader.query_stock_accounts()

    def get_position(self, code):
        if not self.xt_trader:
            return None
        positions = self.xt_trader.query_stock_positions()
        for pos in positions:
            if pos.stock_code == code:
                return {
                    "code": pos.stock_code,
                    "shares": pos.quantity,
                    "avg_price": pos.open_price,
                    "market_value": pos.market_value,
                }
        return None

    def buy(self, code, price, quantity):
        if not self.xt_trader:
            return {"status": "error", "msg": "未连接"}
        try:
            order_id = self.xt_trader.stock_order(
                self.account, code, price_type=1, price=price, quantity=quantity
            )
            return {"status": "submitted", "order_id": order_id}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def sell(self, code, price, quantity):
        if not self.xt_trader:
            return {"status": "error", "msg": "未连接"}
        try:
            order_id = self.xt_trader.stock_order(
                self.account, code, price_type=2, price=price, quantity=quantity
            )
            return {"status": "submitted", "order_id": order_id}
        except Exception as e:
            return {"status": "error", "msg": str(e)}

    def get_quotes(self, codes):
        from xtquant import xtdata
        results = []
        for code in codes:
            data = xtdata.get_market_data_ex([], [code])
            if code in data:
                results.append(data[code])
        return results

    def query_funds(self):
        if not self.xt_trader:
            return {}
        accounts = self.xt_trader.query_stock_accounts()
        return accounts

    def query_orders(self):
        if not self.xt_trader:
            return []
        return self.xt_trader.query_stock_orders()

    def cancel_order(self, order_id):
        if not self.xt_trader:
            return {"status": "error"}
        try:
            return self.xt_trader.cancel_order(order_id)
        except Exception as e:
            return {"status": "error", "msg": str(e)}


# ============ 工厂函数 ============

def get_broker(broker_mode=None):
    """工厂函数 - 根据模式返回对应的券商实例
    
    优先级: 参数 > config.yaml > 默认值(mock)
    
    支持的 broker_mode:
        mock          - 模拟盘 (本地行情+本地持仓, 默认)
        easytrader    - 同花顺 GUI 自动化
        tdx           - 通达信 GUI 自动化
        qmt           - 迅投 MiniQMT
    """
    # 1. 优先用传入的参数
    if broker_mode is not None:
        mode = broker_mode
    else:
        # 2. 从 config.yaml 读取
        config = load_config()
        mode = config.get("broker_mode", "mock")

    # 3. 从配置读取券商路径
    config = load_config()
    easy_path = config.get("easytrader_path", "auto")
    tdx_path = config.get("tdx_path", "auto")
    qmt_path = config.get("qmt_path", r"C:\国金证券QMT\userdata_mini")
    if easy_path == "auto":
        easy_path = None
    if tdx_path == "auto":
        tdx_path = None

    # 实盘参数 (从 live 配置读取)
    live_cfg = config.get("live", {})
    price_strategy = live_cfg.get("price_strategy", 0)
    order_timeout = live_cfg.get("order_timeout", 30)

    brokers = {
        "mock": lambda: MockBroker(),
        "easytrader": lambda: EasyTraderBroker(
            broker_type="ths",
            user_path=easy_path,
        ),
        "tdx": lambda: EasyTraderBroker(
            broker_type="tdx",
            user_path=tdx_path,
        ),
        "qmt": lambda: QMTBroker(
            xt_path=qmt_path,
        ),
    }

    if mode not in brokers:
        raise ValueError(f"不支持的 broker_mode: {mode} (可选: {list(brokers.keys())})")

    return brokers[mode]()


if __name__ == "__main__":
    broker = get_broker()
    logger.info(f"连接状态: {broker.connect()}")
    accounts = broker.get_accounts()
    logger.info(f"账户: {accounts}")
