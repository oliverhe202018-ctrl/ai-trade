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
            "create_time": time.time()
        }
        return order_id

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self.orders:
            order = self.orders[order_id]
            if order["status"] == "PENDING":
                order["status"] = "CANCELED"
                return True
        return False

    def get_order_status(self, order_id: str) -> dict:
        if order_id not in self.orders:
            return {"order_id": order_id, "status": "REJECTED"}
        
        order = self.orders[order_id]
        # 模拟撮合逻辑：市价单立即成交，限价单有概率等待
        if order["status"] == "PENDING":
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
                     
        return order.copy()

    def get_balance(self) -> dict:
        return self.balance

    def get_positions(self) -> dict:
        return self.positions

