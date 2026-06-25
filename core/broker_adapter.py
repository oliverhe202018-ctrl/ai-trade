"""
Broker Adapter 层 - 抽象基类与模拟券商实现
"""
import uuid
import time
import random
from abc import ABC, abstractmethod
from datetime import datetime

class BaseBroker(ABC):
    @abstractmethod
    def place_order(self, code: str, action: str, qty: int, price_type: str, price: float) -> str:
        """返回订单 ID"""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> dict:
        """
        返回示例:
        {
            "order_id": "xxx",
            "status": "PENDING" | "PARTIAL_FILLED" | "FILLED" | "CANCELED" | "REJECTED",
            "filled_qty": 100,
            "avg_price": 10.5
        }
        """
        pass

    @abstractmethod
    def get_balance(self) -> dict:
        pass

    @abstractmethod
    def get_positions(self) -> dict:
        pass

class MockBrokerAdapter(BaseBroker):
    def __init__(self):
        self.orders = {}
        self.balance = {"cash": 1_000_000.0, "total_equity": 1_000_000.0}
        self.positions = {}
        
    def place_order(self, code: str, action: str, qty: int, price_type: str, price: float) -> str:
        order_id = f"mock_{uuid.uuid4().hex[:8]}"
        self.orders[order_id] = {
            "order_id": order_id,
            "code": code,
            "action": action,
            "qty": qty,
            "price_type": price_type,
            "price": price,
            "status": "PENDING",
            "filled_qty": 0,
            "avg_price": 0.0,
            "create_time": time.time(),
            "_filled": False,
        }
        return order_id

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self.orders:
            order = self.orders[order_id]
            if order["status"] == "PENDING" and not order.get("_filled"):
                order["status"] = "CANCELED"
                return True
        return False

    def get_order_status(self, order_id: str) -> dict:
        if order_id not in self.orders:
            return {"order_id": order_id, "status": "REJECTED"}
        
        order = self.orders[order_id]
        # 模拟撮合逻辑：市价单立即成交，限价单有概率等待
        # _filled 标记保证幂等性：多次调用不会重复扣款/加仓
        if order["status"] == "PENDING" and not order.get("_filled"):
            if order["price_type"] == "市价" or random.random() > 0.2:
                order["status"] = "FILLED"
                order["filled_qty"] = order["qty"]
                # 模拟一点滑点
                slippage = order["price"] * 0.001 if order["price"] > 0 else 0
                if order["action"] == "BUY":
                    order["avg_price"] = order["price"] + slippage
                else:
                    order["avg_price"] = order["price"] - slippage
                
                if order["avg_price"] <= 0:
                     # 容错：如果市价单传入0价格，模拟一个随机价格
                     order["avg_price"] = random.uniform(10.0, 100.0)
                
                # 标记已成交，避免重复处理
                order["_filled"] = True
                
                # 更新持仓和资金
                self._update_position(order)
        
        return order.copy()

    def _update_position(self, order):
        """根据订单执行结果更新持仓和资金（幂等）"""
        code = order["code"]
        action = order["action"]
        qty = order["filled_qty"]
        price = order["avg_price"]
        
        if action == "BUY":
            cost = qty * price
            commission = max(5.0, cost * 0.001)  # 最低佣金5元，费率0.1%
            
            if self.balance["cash"] < cost + commission:
                order["status"] = "REJECTED"
                return
            
            self.balance["cash"] -= (cost + commission)
            
            if code in self.positions:
                old_pos = self.positions[code]
                new_qty = old_pos["qty"] + qty
                new_avg_price = (old_pos["avg_price"] * old_pos["qty"] + price * qty) / new_qty
                self.positions[code]["qty"] = new_qty
                self.positions[code]["avg_price"] = round(new_avg_price, 3)
            else:
                self.positions[code] = {
                    "code": code,
                    "qty": qty,
                    "avg_price": round(price, 3),
                    "market_value": round(qty * price, 2),
                }
        
        elif action == "SELL":
            if code not in self.positions or self.positions[code]["qty"] < qty:
                order["status"] = "REJECTED"
                return
            
            revenue = qty * price
            commission = max(5.0, revenue * 0.001)
            stamp_tax = revenue * 0.0005  # 印花税0.05%
            
            self.balance["cash"] += (revenue - commission - stamp_tax)
            
            remaining_qty = self.positions[code]["qty"] - qty
            if remaining_qty <= 0:
                del self.positions[code]
            else:
                self.positions[code]["qty"] = remaining_qty

    def get_balance(self) -> dict:
        return self.balance

    def get_positions(self) -> dict:
        return self.positions

