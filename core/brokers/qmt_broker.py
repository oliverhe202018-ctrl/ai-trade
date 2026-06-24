"""
QMT Broker Adapter (V3.0 路线 C)
连接迅投 MiniQMT 客户端，将底层异步的回调状态封装为同步查询供高层订单状态机使用。
"""
import uuid
import time
import threading
from typing import Dict, Any

from core.logger_config import logger
from core.broker_adapter import BaseBroker

try:
    from xtquant import xtdata
    from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
    from xtquant.xttype import StockAccount
    from xtquant import xtconstant
    XTQUANT_AVAILABLE = True
except ImportError:
    XTQUANT_AVAILABLE = False
    logger.warning("未检测到 xtquant 环境，QmtBroker 降级模式，实盘将无法工作。")
    # 为了保证无 xtquant 环境下的类型检查，创建占位类
    class XtQuantTraderCallback:
        pass


class QmtBroker(BaseBroker, XtQuantTraderCallback):
    def __init__(self, qmt_path: str, account_id: str):
        if not XTQUANT_AVAILABLE:
            raise ImportError("初始化 QmtBroker 失败：当前环境未安装 xtquant SDK。")
            
        self.qmt_path = qmt_path
        self.account_id = account_id
        self.acc = StockAccount(self.account_id)
        
        # 内存缓存：异步回调状态落地 (order_id -> state dict)
        self._order_cache: Dict[str, dict] = {}
        self._cache_lock = threading.Lock()
        
        # xtquant 的 order_id 是自增整形，所以我们要维护：
        # sys_order_id (系统字串, 如 "qmt_xxxxx") <-> xt_order_id (QMT整形) 双向映射
        self._sys2xt = {}
        self._xt2sys = {}
        
        logger.info(f"[QmtBroker] 正在初始化 MiniQMT 连接 (path: {qmt_path}, acc: {account_id})...")
        
        # 初始化交易器
        session_id = int(time.time())
        self.xt_trader = XtQuantTrader(qmt_path, session_id)
        
        # 注册自身作为回调接收器
        self.xt_trader.register_callback(self)
        
        # 启动交易线程
        self.xt_trader.start()
        
        # 建立连接
        connect_result = self.xt_trader.connect()
        if connect_result != 0:
            raise ConnectionError(f"连接 QMT 失败，返回码: {connect_result}")
            
        # 订阅账号
        subscribe_result = self.xt_trader.subscribe(self.acc)
        if subscribe_result != 0:
            raise ConnectionError(f"订阅 QMT 账号失败，返回码: {subscribe_result}")
            
        logger.info("[QmtBroker] MiniQMT 预热与连接完成。")

    def _format_to_qmt_code(self, code: str) -> str:
        """格式化 6 位代码为 QMT 格式 (带有 .SH / .SZ 后缀)"""
        if not code:
            return code
        code = str(code).strip()
        if code.startswith('6'):
            return f"{code}.SH"
        elif code.startswith('0') or code.startswith('3'):
            return f"{code}.SZ"
        # 默认回退
        return code

    def place_order(self, code: str, action: str, qty: int, price_type: str, price: float) -> str:
        sys_order_id = f"qmt_{uuid.uuid4().hex[:8]}"
        qmt_code = self._format_to_qmt_code(code)
        
        xt_action = xtconstant.STOCK_BUY if action == "BUY" else xtconstant.STOCK_SELL
        
        # 价格类型映射
        if price_type == "市价" or price <= 0:
            xt_price_type = xtconstant.LATEST_PRICE
            price = 0.0 # 市价时价格传 0 即可
        else:
            xt_price_type = xtconstant.FIX_PRICE
            
        # 发送异步委托
        xt_order_id = self.xt_trader.order_stock_async(
            self.acc, qmt_code, xt_action, qty, xt_price_type, price, "ai_trader", sys_order_id
        )
        
        if xt_order_id < 0:
            logger.error(f"[QmtBroker] 委托下发失败, QMT 返回码: {xt_order_id}")
            # 生成一个 REJECTED 状态缓存
            with self._cache_lock:
                self._order_cache[sys_order_id] = {
                    "status": "REJECTED",
                    "filled_qty": 0,
                    "avg_price": 0.0
                }
            return sys_order_id
            
        # 建立映射
        with self._cache_lock:
            self._sys2xt[sys_order_id] = xt_order_id
            self._xt2sys[xt_order_id] = sys_order_id
            
            # 初始化 PENDING 态
            self._order_cache[sys_order_id] = {
                "status": "PENDING",
                "filled_qty": 0,
                "avg_price": 0.0
            }
            
        return sys_order_id

    def cancel_order(self, order_id: str) -> bool:
        with self._cache_lock:
            xt_order_id = self._sys2xt.get(order_id)
            
        if xt_order_id is None:
            logger.warning(f"[QmtBroker] 找不到撤单对应的 xt_order_id: {order_id}")
            return False
            
        res = self.xt_trader.cancel_order_stock_async(self.acc, xt_order_id)
        if res < 0:
            logger.error(f"[QmtBroker] 撤单失败, 返回码: {res}")
            return False
        return True

    def get_order_status(self, order_id: str) -> dict:
        """供外层轮询拉取当前同步状态"""
        with self._cache_lock:
            if order_id not in self._order_cache:
                return {"status": "PENDING", "filled_qty": 0, "avg_price": 0.0}
            # 返回副本以防外部修改
            return dict(self._order_cache[order_id])

    def get_balance(self) -> dict:
        asset = self.xt_trader.query_stock_asset(self.acc)
        if asset:
            return {
                "cash": asset.cash,
                "total_equity": asset.total_asset
            }
        return {"cash": 0.0, "total_equity": 0.0}

    def get_positions(self) -> dict:
        positions = self.xt_trader.query_stock_positions(self.acc)
        result = {}
        if positions:
            for pos in positions:
                # 把 QMT 带后缀的股票代码去掉后缀还原成系统内部标准 6 位
                raw_code = pos.stock_code.split('.')[0]
                result[raw_code] = {
                    "shares": pos.volume,
                    "usable_shares": pos.can_use_volume,
                    "avg_price": pos.open_price,
                    "market_value": pos.market_value
                }
        return result

    # ================= XtQuantTraderCallback 实现 =================

    def on_disconnected(self):
        logger.error("[QmtBroker] QMT 连接断开。")

    def on_order_event(self, order):
        """
        委托状态变化推送
        order_status 枚举 (简化映射):
        """
        xt_order_id = order.order_id
        
        with self._cache_lock:
            sys_order_id = self._xt2sys.get(xt_order_id)
            if not sys_order_id:
                # 可能是非本系统发出的订单，忽略
                return
                
            status_map = {
                xtconstant.ORDER_UNREPORTED: "PENDING",
                xtconstant.ORDER_WAIT_REPORTING: "PENDING",
                xtconstant.ORDER_REPORTED: "PENDING",
                xtconstant.ORDER_REPORTED_CANCEL: "PENDING",
                xtconstant.ORDER_PARTSUCC_CANCEL: "CANCELED",  # 部成部撤当做完结
                xtconstant.ORDER_PART_SUCC: "PARTIAL_FILLED",
                xtconstant.ORDER_SUCCEEDED: "FILLED",
                xtconstant.ORDER_CANCELED: "CANCELED",
                xtconstant.ORDER_REJECTED: "REJECTED",
                xtconstant.ORDER_UNKNOWN: "PENDING"
            }
            
            new_status = status_map.get(order.order_status, "PENDING")
            
            # 更新缓存
            if sys_order_id not in self._order_cache:
                self._order_cache[sys_order_id] = {"filled_qty": 0, "avg_price": 0.0}
            
            self._order_cache[sys_order_id]["status"] = new_status
            
            logger.debug(f"[QmtBroker] 订单状态更新: {sys_order_id} -> {new_status}")

    def on_trade_event(self, trade):
        """成交明细推送"""
        xt_order_id = trade.order_id
        
        with self._cache_lock:
            sys_order_id = self._xt2sys.get(xt_order_id)
            if not sys_order_id:
                return
                
            if sys_order_id not in self._order_cache:
                self._order_cache[sys_order_id] = {"status": "PENDING", "filled_qty": 0, "avg_price": 0.0}
            
            # 累加成交数量
            traded_qty = trade.traded_volume
            traded_price = trade.traded_price
            
            cache = self._order_cache[sys_order_id]
            old_filled = cache.get("filled_qty", 0)
            old_price = cache.get("avg_price", 0.0)
            
            new_filled = old_filled + traded_qty
            if new_filled > 0:
                cache["avg_price"] = ((old_filled * old_price) + (traded_qty * traded_price)) / new_filled
            cache["filled_qty"] = new_filled
            
            logger.info(f"[QmtBroker] 成交回报: {sys_order_id} 成交 {traded_qty} @ {traded_price:.2f}")
