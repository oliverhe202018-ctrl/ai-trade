"""
网格交易状态机管理器 (Grid Trading State Machine)
职责：维护网格线、检测价格穿越、生成买卖信号
"""
from core.logger_config import logger


class GridManager:
    """
    网格交易状态机

    网格线计算：
        向上: base_price * (1 + step_pct * N)
        向下: base_price * (1 - step_pct * N)

    状态字典结构：
        grid_states[code] = {
            "base_price": float,
            "buy_triggered": {N: True/False, ...},   # 下方第N格是否已买入
            "sell_triggered": {N: True/False, ...},   # 上方第N格是否已卖出
        }
    """

    def __init__(self, step_pct=0.03, trade_amount=5000, max_grid_level=5):
        """
        Args:
            step_pct: 网格步长比例 (默认3%)
            trade_amount: 每格交易金额
            max_grid_level: 最大网格层数 (上下各5层)
        """
        self.step_pct = step_pct
        self.trade_amount = trade_amount
        self.max_grid_level = max_grid_level
        self.grid_states = {}

    def init_grid(self, code, base_price):
        """初始化某只股票的网格基准价"""
        if code not in self.grid_states:
            self.grid_states[code] = {
                "base_price": base_price,
                "buy_triggered": {},
                "sell_triggered": {},
            }
            logger.info(f"  [GRID] {code} 初始化网格基准价: {base_price:.2f}")

    def get_grid_line(self, code, level):
        """
        获取指定层级的网格线价格

        Args:
            code: 股票代码
            level: 层级 (正数=上方卖出线, 负数=下方买入线)

        Returns:
            float: 网格线价格
        """
        state = self.grid_states.get(code)
        if not state:
            return None
        base = state["base_price"]
        return base * (1 + self.step_pct * level)

    def check_crossings(self, code, high_price, low_price, held_shares):
        """
        检测价格穿越网格线，生成交易信号

        Args:
            code: 股票代码
            high_price: 当日最高价
            low_price: 当日最低价
            held_shares: 当前持有该股的数量

        Returns:
            list of dict: [{"action": "BUY"/"SELL", "code": ..., "grid_level": N, "price": ...}]
        """
        state = self.grid_states.get(code)
        if not state:
            return []

        signals = []
        base = state["base_price"]

        # 检查下方网格线（买入信号）
        for level in range(1, self.max_grid_level + 1):
            buy_line = base * (1 - self.step_pct * level)
            # 最低价击穿下方网格
            if low_price <= buy_line:
                if not state["buy_triggered"].get(level, False):
                    signals.append({
                        "action": "BUY",
                        "code": code,
                        "grid_level": -level,
                        "price": buy_line,
                    })
                    state["buy_triggered"][level] = True
                    # 同时重置对应层级的卖出状态（允许再次卖出）
                    state["sell_triggered"].pop(level, None)

        # 检查上方网格线（卖出信号）
        for level in range(1, self.max_grid_level + 1):
            sell_line = base * (1 + self.step_pct * level)
            # 最高价突破上方网格，且持有底仓
            if high_price >= sell_line and held_shares > 0:
                if not state["sell_triggered"].get(level, False):
                    signals.append({
                        "action": "SELL",
                        "code": code,
                        "grid_level": level,
                        "price": sell_line,
                    })
                    state["sell_triggered"][level] = True
                    # 同时重置对应层级的买入状态
                    state["buy_triggered"].pop(level, None)

        return signals

    def calc_grid_shares(self, price):
        """根据交易金额和价格计算应买卖的股数（整手）"""
        if price <= 0:
            return 0
        shares = int(self.trade_amount / price / 100) * 100
        return max(shares, 100)  # 至少1手

    def update_base_price(self, code, new_base_price):
        """更新网格基准价（重置所有网格状态）"""
        self.grid_states[code] = {
            "base_price": new_base_price,
            "buy_triggered": {},
            "sell_triggered": {},
        }
        logger.info(f"  [GRID] {code} 更新基准价: {new_base_price:.2f}")

    def get_status(self, code):
        """获取某只股票的网格状态摘要"""
        state = self.grid_states.get(code)
        if not state:
            return None
        base = state["base_price"]
        buy_count = sum(1 for v in state["buy_triggered"].values() if v)
        sell_count = sum(1 for v in state["sell_triggered"].values() if v)
        return {
            "base_price": base,
            "grid_lines": {
                f"buy_{l}": round(base * (1 - self.step_pct * l), 2)
                for l in range(1, self.max_grid_level + 1)
            } | {
                f"sell_{l}": round(base * (1 + self.step_pct * l), 2)
                for l in range(1, self.max_grid_level + 1)
            },
            "buy_triggered_count": buy_count,
            "sell_triggered_count": sell_count,
        }
