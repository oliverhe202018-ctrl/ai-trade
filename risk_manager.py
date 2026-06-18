"""
风险管理模块 - 专职仓位控制、风控与止盈止损信号判定
包含三层退出机制：
1. 逻辑止盈（最高优先级，赛道退潮立即走）
2. 价格止损（-5%保本线）
3. 价格止盈（+15%兑现）

新增：ATR 波动率自适应仓位模型 (Volatility-Scaled Sizing)
"""

import json
import os


# ==================== 动态超参数配置 ====================
_HYPERPARAMS_PATH = os.path.join(os.path.dirname(__file__), "data_cache", "hyperparams.json")
_DEFAULT_HYPERPARAMS = {
    "atr_period": 14,
    "risk_per_trade": 0.01,
    "stop_loss_pct": -0.05,
}


def _load_hyperparams():
    """
    从 data_cache/hyperparams.json 实时读取超参数。
    若文件不存在，自动创建并写入默认值。
    """
    os.makedirs(os.path.dirname(_HYPERPARAMS_PATH), exist_ok=True)
    if not os.path.exists(_HYPERPARAMS_PATH):
        with open(_HYPERPARAMS_PATH, "w", encoding="utf-8") as f:
            json.dump(_DEFAULT_HYPERPARAMS, f, indent=2)
        return dict(_DEFAULT_HYPERPARAMS)
    with open(_HYPERPARAMS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def calculate_atr(stock_data, period=None):
    """
    计算 ATR (Average True Range) - 波动率指标

    算法：
    TR = Max(High-Low, Abs(High-PrevClose), Abs(Low-PrevClose))
    ATR = period 日 TR 的移动平均

    Args:
        stock_data: dict，需包含以下字段：
            - highs: list[float]，历史最高价序列（至少 period+1 日）
            - lows: list[float]，历史最低价序列
            - closes: list[float]，历史收盘价序列（用于计算 PrevClose）
        period: int，ATR 计算周期，默认从 hyperparams.json 读取（默认 14 日）

    Returns:
        float: ATR 值，数据不足时返回 0
    """
    # 从配置文件实时读取 atr_period
    if period is None:
        params = _load_hyperparams()
        period = params.get("atr_period", 14)

    highs = stock_data.get("highs", [])
    lows = stock_data.get("lows", [])
    closes = stock_data.get("closes", [])

    # 数据不足时返回 0
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return 0.0

    # 计算 TR 序列（从第 2 个数据点开始，因为需要 PrevClose）
    tr_values = []
    for i in range(1, len(highs)):
        high = highs[i]
        low = lows[i]
        prev_close = closes[i - 1]

        # TR = Max(High-Low, Abs(High-PrevClose), Abs(Low-PrevClose))
        tr1 = high - low
        tr2 = abs(high - prev_close)
        tr3 = abs(low - prev_close)
        tr = max(tr1, tr2, tr3)
        tr_values.append(tr)

    # 数据不足时返回 0
    if len(tr_values) < period:
        return 0.0

    # 计算最近 period 日的移动平均
    recent_tr = tr_values[-period:]
    atr = sum(recent_tr) / period
    return atr


def calculate_position_size(total_capital, current_price, atr, risk_per_trade=None, max_position_pct=0.20):
    """
    ATR 波动率自适应仓位计算 (Volatility-Scaled Sizing)

    逻辑：
    - 单笔交易允许承担的最大风险额度为总资金的 risk_per_trade (默认从 hyperparams.json 读取，默认 1%)
    - Target_Shares = (total_capital * risk_per_trade) / atr
    - 约束：Target_Shares * current_price <= total_capital * max_position_pct

    Args:
        total_capital: float，账户总资金
        current_price: float，当前股价
        atr: float，ATR 波动率值
        risk_per_trade: float，单笔交易风险比例，默认从 hyperparams.json 读取
        max_position_pct: float，单只股票最大仓位占比，默认 20%

    Returns:
        int: 目标买入股数（整手，100股为单位）
    """
    # 从配置文件实时读取 risk_per_trade
    if risk_per_trade is None:
        params = _load_hyperparams()
        risk_per_trade = params.get("risk_per_trade", 0.01)

    # ATR 为 0 或无效时，返回 0
    if atr <= 0 or current_price <= 0:
        return 0

    # 计算目标股数：(总资金 * 风险比例) / ATR
    target_shares = (total_capital * risk_per_trade) / atr

    # 约束：单只股票仓位不超过总资金的 max_position_pct
    max_shares_by_capital = (total_capital * max_position_pct) / current_price
    target_shares = min(target_shares, max_shares_by_capital)

    # 向下取整到 100 股（1手）
    target_shares = int(target_shares / 100) * 100

    # 最少买 1 手
    if target_shares < 100:
        target_shares = 100

    return target_shares


class ExitSignal:
    """退出信号数据类"""

    def __init__(self, should_exit=False, exit_type="HOLD", reason="", urgency="MONITOR"):
        self.should_exit = should_exit
        self.exit_type = exit_type  # 'PRICE_STOP_LOSS' | 'PRICE_TAKE_PROFIT' | 'LOGIC_EXIT' | 'TIME_STOP_LOSS' | 'HOLD'
        self.reason = reason
        self.urgency = urgency  # 'IMMEDIATE' | 'NEXT_OPEN' | 'MONITOR'


class LogicExitManager:
    """
    逻辑止盈：持仓逻辑失效时，无视盈亏立即清仓
    """

    SECTOR_RANK_THRESHOLD = 30  # 板块排名跌出前30名触发
    SECTOR_RANK_ALERT = 20  # 排名跌出前20名发出预警
    FUND_FLOW_NEGATIVE_DAYS = 2  # 连续N天主力净流出触发
    MAX_HOLDING_DAYS = 15  # 最大持仓天数阈值（时间止损）

    def check_logic_exit(
        self,
        stock_code,
        current_sector_rank,
        entry_sector_rank,
        recent_fund_flows,
        current_pnl_pct,
    ):
        """
        逻辑止盈检测

        Args:
            stock_code: 股票代码
            current_sector_rank: 当前板块在全市场的排名（1=最强）
            entry_sector_rank: 买入时的板块排名
            recent_fund_flows: 最近N天主力净流入（负=流出）
            current_pnl_pct: 当前持仓盈亏%

        Returns:
            ExitSignal
        """
        reasons = []

        # 逻辑止盈条件1：板块排名退潮
        if current_sector_rank > self.SECTOR_RANK_THRESHOLD:
            reasons.append(
                f"板块排名跌至第{current_sector_rank}名（阈值:{self.SECTOR_RANK_THRESHOLD}），"
                f"买入时排名:{entry_sector_rank}，赛道退潮"
            )
            return ExitSignal(
                should_exit=True,
                exit_type="LOGIC_EXIT",
                reason=" | ".join(reasons),
                urgency="IMMEDIATE",
            )

        # 逻辑止盈条件2：主力连续出逃
        if len(recent_fund_flows) >= self.FUND_FLOW_NEGATIVE_DAYS:
            consecutive_outflow = all(
                flow < 0 for flow in recent_fund_flows[-self.FUND_FLOW_NEGATIVE_DAYS:]
            )
            if consecutive_outflow:
                total_outflow = sum(
                    recent_fund_flows[-self.FUND_FLOW_NEGATIVE_DAYS:]
                )
                reasons.append(
                    f"主力连续{self.FUND_FLOW_NEGATIVE_DAYS}天净流出，"
                    f"累计流出{total_outflow:.1f}万，主力离场信号"
                )
                return ExitSignal(
                    should_exit=True,
                    exit_type="LOGIC_EXIT",
                    reason=" | ".join(reasons),
                    urgency="NEXT_OPEN",
                )

        # 预警区间（排名在21-30之间，监控但不清仓）
        if current_sector_rank > self.SECTOR_RANK_ALERT:
            return ExitSignal(
                should_exit=False,
                exit_type="MONITOR",
                reason=f"板块排名{current_sector_rank}，进入预警区（>{self.SECTOR_RANK_ALERT}），密切关注",
                urgency="MONITOR",
            )

        return ExitSignal(
            should_exit=False,
            exit_type="HOLD",
            reason=f"板块排名{current_sector_rank}，持仓逻辑完整",
            urgency="MONITOR",
        )

    def full_exit_check(
        self,
        stock_code,
        cost_price,
        current_price,
        sector_rank,
        entry_sector_rank,
        recent_fund_flows,
        holding_days=0,
        take_profit_pct=0.15,
        stop_loss_pct=None,
    ):
        """
        完整的分层退出检查，优先级：
        1. 逻辑止盈（最高优先级，赛道退潮立即走）
        2. 时间止损（持仓满N天且未盈利，强制释放流动性）
        3. 价格止损（保本线）
        4. 价格止盈（兑现）
        """
        # 从配置文件实时读取 stop_loss_pct
        if stop_loss_pct is None:
            params = _load_hyperparams()
            stop_loss_pct = params.get("stop_loss_pct", -0.05)

        pnl_pct = (current_price - cost_price) / cost_price

        # 优先级1：逻辑止盈
        logic_signal = self.check_logic_exit(
            stock_code, sector_rank, entry_sector_rank, recent_fund_flows, pnl_pct
        )
        if logic_signal.should_exit:
            return logic_signal

        # 优先级2：时间止损（持仓满N天且未盈利）
        if holding_days >= self.MAX_HOLDING_DAYS and pnl_pct <= 0:
            return ExitSignal(
                should_exit=True,
                exit_type="TIME_STOP_LOSS",
                reason=f"触发时间止损：持仓已满 {holding_days} 天且未实现盈利，强制释放流动性",
                urgency="IMMEDIATE",
            )

        # 优先级3：价格止损
        if pnl_pct <= stop_loss_pct:
            return ExitSignal(
                should_exit=True,
                exit_type="PRICE_STOP_LOSS",
                reason=f"触发止损线 {pnl_pct:.1%}（阈值:{stop_loss_pct:.0%}）",
                urgency="IMMEDIATE",
            )

        # 优先级4：价格止盈
        if pnl_pct >= take_profit_pct:
            return ExitSignal(
                should_exit=True,
                exit_type="PRICE_TAKE_PROFIT",
                reason=f"触发止盈线 {pnl_pct:.1%}（阈值:+{take_profit_pct:.0%}）",
                urgency="NEXT_OPEN",
            )

        return logic_signal  # 返回 HOLD 或 MONITOR 状态


# 保留旧版简单接口兼容调用
def check_sell_signals(positions, quotes, config):
    """
    生成卖出风控信号（兼容旧版接口，增加逻辑止盈层）

    Args:
        positions: 当前持仓 dict {code: {shares, avg_price, name, ...}}
        quotes: 实时行情列表
        config: 系统配置字典

    Returns:
        list of sell decision dicts
    """
    sell_cfg = config.get("sell", {})
    stop_loss_pct = sell_cfg.get("stop_loss_pct", -5)
    take_profit_pct = sell_cfg.get("take_profit_pct", 10)

    sells = []
    price_map = {q["code"]: q for q in quotes}
    logic_exit_mgr = LogicExitManager()

    for code, pos in positions.items():
        # P2-1 修复：严禁使用成本价作为当前价，缺失行情时跳过该标的
        quote_data = price_map.get(code, {})
        if not quote_data or "price" not in quote_data:
            # 数据缺失，判定为停牌或数据异常，跳过风控判定
            continue
        
        current_price = quote_data["price"]
        buy_price = pos["avg_price"]

        if buy_price <= 0:
            continue

        # 优先走逻辑止盈
        sector_rank = quote_data.get("sector_rank", 99)
        entry_sector_rank = pos.get("entry_sector_rank", sector_rank)
        main_fund = quote_data.get("main_fund", 0)
        holding_days = pos.get("holding_days", 0)

        exit_signal = logic_exit_mgr.full_exit_check(
            stock_code=code,
            cost_price=buy_price,
            current_price=current_price,
            sector_rank=sector_rank,
            entry_sector_rank=entry_sector_rank,
            recent_fund_flows=[main_fund],  # 简化：用当日主力净流入代表
            holding_days=holding_days,
            take_profit_pct=take_profit_pct / 100,
            stop_loss_pct=stop_loss_pct / 100,
        )

        if exit_signal.should_exit:
            action_map = {
                "LOGIC_EXIT": "logic_exit",
                "PRICE_STOP_LOSS": "stop_loss",
                "PRICE_TAKE_PROFIT": "take_profit",
                "TIME_STOP_LOSS": "time_stop_loss",
            }
            sells.append(
                {
                    "code": code,
                    "name": pos.get("name", code),
                    "price": current_price,
                    "shares": pos["shares"],
                    "reason": exit_signal.reason,
                    "action": action_map.get(exit_signal.exit_type, "logic_exit"),
                }
            )

    return sells


def check_circuit_breaker(cash, positions, quotes, config):
    """
    账户级日最大回撤熔断

    Args:
        cash: 当前现金
        positions: 当前持仓
        quotes: 实时行情
        config: 系统配置

    Returns:
        bool: True = 触发熔断, 阻止当日买入
    """
    risk = config.get("risk", {})
    max_dd = risk.get("max_daily_drawdown", -3)
    start_cash = config.get("daily_start_cash", cash)

    # 计算当前持仓市值
    price_map = {q["code"]: q for q in quotes}
    market_value = 0
    for code, pos in positions.items():
        current_price = price_map.get(code, {}).get("price", pos["avg_price"])
        market_value += current_price * pos.get("shares", 0)

    current_value = cash + market_value
    drawdown = (current_value - start_cash) / start_cash * 100 if start_cash > 0 else 0

    if drawdown <= max_dd:
        print(f"[WARN] 风控熔断: 日回撤 {drawdown:.2f}% <= {max_dd}%")
        return True
    return False


# ==================== 建仓冷却锁（Cooldown Lock） ====================
# 记录每日收盘价用于检测大盘/账户趋势（使用 list 而非 dict，保证时间有序）
_daily_close_history = []  # [(timestamp, close_value), ...]


def check_building_cooldown(positions, config, today_value=None):
    """
    建仓冷却锁：当账户/大盘处于下行趋势时，限制当日新增开仓数量。

    触发条件：
    1. 连续两日亏损（账户净值连续两日下降）→ 当日最多开 2 仓
    2. 连续三日及以上亏损 → 当日最多开 1 仓（极端保守）
    3. 当日净值相比昨日下降 >2% → 视为明显下行，限制至 2 仓
    4. 当日净值相比昨日下降 >5% → 视为显著下行，限制至 1 仓

    Args:
        positions: 当前持仓 dict
        config: 配置字典
        today_value: 今日收盘价（账户总价值），用于回测传入

    Returns:
        int: 允许的最大新增持仓数，0=禁止开仓，-1=无限制
    """
    global _daily_close_history

    max_positions = config.get("strategy", {}).get("max_positions", 10)
    max_positions = min(max_positions, 10)  # 上限10

    # 记录今日收盘净值（用于下次判断）
    if today_value is not None:
        current_value = today_value
    else:
        current_value = config.get("initial_capital", config.get("cash", 100000))
        if positions:
            for code, pos in positions.items():
                current_value += pos["avg_price"] * pos.get("shares", 0)

    import time

    # 记录历史收盘值（简单阈值判断）
    if not _daily_close_history:
        _daily_close_history.append((time.time(), current_value))
        # 首次记录，允许全量开仓
        return max_positions  # 首次允许开满

    # --- 核心检测逻辑：按日期顺序检查净值变化 ---
    # 取最近5个记录（保证包含足够的对比窗口）
    recent = _daily_close_history[-5:]

    # 判断是否处于连续下跌趋势（连续两日以上净值下降）
    consecutive_down = False
    max_consecutive_down = 0
    for i in range(1, len(recent)):
        if recent[i][1] < recent[i - 1][1]:
            consecutive_down = True
            max_consecutive_down += 1
        else:
            max_consecutive_down = 0

    # 极端保守：连续三日以上亏损 → 限制至 1 仓
    if max_consecutive_down >= 3:
        print(f"[建仓冷却] 连续三日以上净值下跌，限制当日最多开 {min(1, max_positions)} 仓")
        return min(1, max_positions)

    # --- 额外保护：单日净值跳降超过一定比例也限制 ---
    if len(recent) >= 2:
        prev_value = recent[-1][1]  # 上一次记录的值
        if prev_value > 0:
            daily_change_pct = (current_value - prev_value) / prev_value * 100

            # 单日跌幅 > 2% → 限制至 2 仓
            if daily_change_pct < -2.0:
                print(
                    f"[建仓冷却] 单日净值跌幅 {daily_change_pct:.1f}%，限制当日最多开 {min(2, max_positions)} 仓"
                )
                return min(2, max_positions)

            # 单日跌幅 > 5% → 限制至 1 仓
            if daily_change_pct < -5.0:
                print(f"[建仓冷却] 单日净值跌幅 {daily_change_pct:.1f}%，限制当日最多开 {min(1, max_positions)} 仓")
                return min(1, max_positions)

    # --- 记录今日值，下次比较 ---
    _daily_close_history.append((time.time(), current_value))

    if consecutive_down and max_consecutive_down >= 2:
        # 连续下跌 → 限制开仓数为 2
        print(f"[建仓冷却] 账户处于连续下跌趋势，限制当日最多开 {min(2, max_positions)} 仓")
        return min(2, max_positions)

    return max_positions  # 无明确趋势，全量开放
