"""
Order Lifecycle Manager - 订单状态机与对账守护逻辑
"""
import time
from core.logger_config import logger
from core.state_manager import load_portfolio, save_portfolio
from core.broker_adapter import BaseBroker

# 手续费率等常量
COMMISSION_RATE = 0.0003
MIN_COMMISSION = 5.0
STAMP_DUTY_RATE = 0.001

class OrderManager:
    def __init__(self, broker: BaseBroker, is_mock: bool = False):
        self.broker = broker
        self.is_mock = is_mock
        # 活跃订单池: order_id -> order_info
        self.active_orders = {}

    def add_order(self, order_id: str, code: str, action: str, qty: int, reason: str = ""):
        self.active_orders[order_id] = {
            "order_id": order_id,
            "code": code,
            "action": action,
            "qty": qty,
            "reason": reason,
            "create_time": time.time(),
            "settled_qty": 0
        }
        logger.info(f"➕ [OrderManager] 委托已提交 {order_id}: {action} {code} {qty}股 ({reason})")

    def sync_orders(self):
        """高频轮询对账"""
        if not self.active_orders:
            return
            
        completed_orders = []
        now = time.time()
        
        for order_id in list(self.active_orders.keys()):
            order = self.active_orders[order_id]
            
            try:
                status_info = self.broker.get_order_status(order_id)
            except Exception as e:
                logger.error(f"[OrderManager] 查询订单 {order_id} 状态异常: {e}")
                continue
                
            status = status_info.get("status", "PENDING")
            
            # 超时撤单检查
            if status == "PENDING":
                if now - order["create_time"] > 60:
                    logger.warning(f"⏳ [OrderManager] 订单 {order_id} 挂单超时 (>60s)，发起撤单。")
                    self.broker.cancel_order(order_id)
                continue
                
            # 处理最终态
            if status in ["FILLED", "PARTIAL_FILLED", "CANCELED", "REJECTED"]:
                filled_qty = status_info.get("filled_qty", 0)
                avg_price = status_info.get("avg_price", 0.0)
                
                # 若本次轮询有新增成交，更新账本
                last_settled = order.get("settled_qty", 0)
                newly_filled = filled_qty - last_settled
                
                if newly_filled > 0:
                    if not self.is_mock:
                        self._update_portfolio(
                            code=order["code"],
                            action=order["action"],
                            filled_qty=newly_filled,
                            avg_price=avg_price
                        )
                    order["settled_qty"] = filled_qty
                    logger.info(f"✅ [OrderManager] 订单 {order_id} 撮合成交! {order['action']} {order['code']} @ {avg_price:.2f} x {newly_filled}股")
                
                if status in ["CANCELED", "REJECTED"]:
                    logger.warning(f"❌ [OrderManager] 订单 {order_id} 已结束: {status}")
                
                if status in ["FILLED", "CANCELED", "REJECTED"]:
                    completed_orders.append(order_id)
        
        for oid in completed_orders:
            if oid in self.active_orders:
                del self.active_orders[oid]

    def _update_portfolio(self, code: str, action: str, filled_qty: int, avg_price: float):
        """精确计算费用并安全更新 live_portfolio.json"""
        portfolio = load_portfolio()
        
        turnover = filled_qty * avg_price
        commission = max(MIN_COMMISSION, turnover * COMMISSION_RATE)
        
        if action == "BUY":
            total_cost = turnover + commission
            portfolio["cash"] -= total_cost
            
            pos = portfolio["positions"].get(code, {"shares": 0, "avg_price": 0.0})
            old_shares = pos["shares"]
            old_avg_price = pos["avg_price"]
            
            new_shares = old_shares + filled_qty
            new_avg_price = ((old_shares * old_avg_price) + total_cost) / new_shares if new_shares > 0 else 0
            
            portfolio["positions"][code] = {
                "shares": new_shares,
                "avg_price": new_avg_price
            }
            
        elif action == "SELL":
            stamp_duty = turnover * STAMP_DUTY_RATE
            net_income = turnover - commission - stamp_duty
            portfolio["cash"] += net_income
            
            if code in portfolio["positions"]:
                pos = portfolio["positions"][code]
                if pos["shares"] > filled_qty:
                    pos["shares"] -= filled_qty
                else:
                    del portfolio["positions"][code]
                    
        save_portfolio(portfolio)
