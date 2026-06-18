"""
实盘交易主入口 (Live Trader)
三阶段执行：信号生成 -> 订单执行 -> 日终结算
"""
import os
import time
import json
from datetime import datetime
from collections import defaultdict

from strategy_engine import select_stocks, generate_sell_signals, mean_reversion_scan, calculate_dca_multiplier, determine_market_regime
from news_extractor import get_news_sentiment
from risk_manager import check_building_cooldown, calculate_atr, calculate_position_size
from grid_manager import GridManager
from state_manager import save_portfolio, load_portfolio
from notifier import send_notification
from trade_engine import MockBroker, QMTBroker, BaseBroker

# 复用 backtester 的数据获取逻辑
from backtester import (
    download_historical_data,
    _calc_buy_cost,
    _calc_sell_revenue,
    BACKTEST_UNIVERSE,
    CACHE_DIR,
)

# 实盘标的池（可独立配置，默认复用回测池）
LIVE_UNIVERSE = BACKTEST_UNIVERSE

# 默认初始资金（仅首次启动时使用）
DEFAULT_INITIAL_CASH = 100000

# ===== 模块级状态（三阶段共享） =====
_portfolio = None
_daily_quotes = []
_price_map = {}
_open_price_map = {}
_high_price_map = {}
_low_price_map = {}
_config = {}
_grid_mgr = None
_stock_history = defaultdict(lambda: {"prices": [], "highs": [], "lows": [], "main_funds": []})
_pending_orders_file = os.path.join(CACHE_DIR, "live_pending_orders.json")

# ===== 券商网关 (Broker Gateway) =====
# 全局 broker 实例，默认使用模拟券商。
# 实盘时可通过 set_broker() 切换为 QMTBroker 等真实券商。
# live_trader 使用 data_cache/live_portfolio.json 作为状态文件，持仓字段为 "positions"
# broker: BaseBroker = MockBroker(
#     state_file=os.path.join(CACHE_DIR, "live_portfolio.json"),
#     position_key="positions",
# )

# 实盘模式：切换至 QMT 网关
broker: BaseBroker = QMTBroker(
    account_id="填入你的资金账号",
    mini_qmt_path=r"D:\你的券商QMT目录\userdata"
)


def set_broker(new_broker: BaseBroker) -> None:
    """切换全局 broker 实例 (用于接入实盘券商)。"""
    global broker
    broker = new_broker


def _init_daily_context():
    """初始化当日执行上下文（加载资产、获取行情）"""
    global _portfolio, _daily_quotes, _price_map, _open_price_map
    global _high_price_map, _low_price_map, _config, _grid_mgr, _stock_history

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 初始化当日执行上下文...")

    # 加载资产快照（强绑定）
    _portfolio = load_portfolio()
    if _portfolio is None:
        print(f"[首次启动] 未找到资产快照，使用默认初始资金: ¥{DEFAULT_INITIAL_CASH}")
        _portfolio = {"cash": DEFAULT_INITIAL_CASH, "positions": {}}
    else:
        print(f"[资产加载] 现金: ¥{_portfolio['cash']:.2f}, 持仓: {len(_portfolio['positions'])} 只")

    # 获取当日行情
    print(f"\n正在获取 {len(LIVE_UNIVERSE)} 只标的行情...")
    _daily_quotes = []
    for code in LIVE_UNIVERSE:
        try:
            _, quotes, _ = download_historical_data(code, days=1)
            if quotes:
                _daily_quotes.extend(quotes)
            time.sleep(1.5)  # 反爬限流
        except Exception as e:
            print(f"[WARN] 获取 {code} 失败: {e}")
            continue

    if not _daily_quotes:
        msg = "当日行情获取全部失败"
        print(f"[ERROR] {msg}")
        send_notification("实盘预警", msg)
        return False

    # 构建价格映射
    _price_map = {q["code"]: q["price"] for q in _daily_quotes}
    _open_price_map = {q["code"]: q.get("open", q["price"]) for q in _daily_quotes}
    _high_price_map = {q["code"]: q.get("high", q["price"]) for q in _daily_quotes}
    _low_price_map = {q["code"]: q.get("low", q["price"]) for q in _daily_quotes}

    print(f"行情获取完毕，共 {len(_daily_quotes)} 只标的")

    # 初始化配置
    _config = {
        "max_positions": 5,
        "sell": {"stop_loss_pct": -10.0, "take_profit_pct": 15.0},
        "news_sentiment": get_news_sentiment(),
        "dca": {"base_amount": 10000, "interval_days": 20},
        "grid": {"step_pct": 0.03, "trade_amount": 5000},
    }

    grid_cfg = _config.get("grid", {})
    _grid_mgr = GridManager(
        step_pct=grid_cfg.get("step_pct", 0.03),
        trade_amount=grid_cfg.get("trade_amount", 5000),
    )

    # 重置 stock_history（实盘简化版，不持久化）
    _stock_history.clear()

    return True


def phase_signal_generation():
    """
    阶段一：14:40 信号生成与路由计算
    生成卖出信号 + 策略路由（网格/定投/趋势） -> 写入待执行订单队列
    """
    global _portfolio, _daily_quotes, _config, _grid_mgr
    global _price_map, _open_price_map, _high_price_map, _low_price_map
    global _stock_history, _pending_orders_file

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] === 阶段一：信号生成 ===")

    if not _init_daily_context():
        return

    # 加载待执行订单队列
    pending_orders = _load_pending_orders(_pending_orders_file)

    # --- 动作 B: 生成当日卖出信号 ---
    sells = generate_sell_signals(_portfolio["positions"], _daily_quotes, _config)
    for sell in sells:
        pending_orders.append({
            "action": "SELL",
            "code": sell["code"],
            "shares": sell["shares"],
            "signal_price": sell["price"],
            "reason": sell["reason"],
        })
        print(f"  [信号] 卖出订单: {sell['code']} (信号价: {sell['price']:.2f})")

    # --- 动作 C: 动态策略路由 ---
    regime_buckets = {"grid": [], "smart_dca": [], "trend": []}
    for q in _daily_quotes:
        code = q["code"]
        hist = _stock_history.get(code)
        if not hist or len(hist["prices"]) < 60:
            regime_buckets["trend"].append(q)
            continue
        regime = determine_market_regime(hist)
        regime_buckets[regime].append(q)

    # C2: 网格策略
    if regime_buckets["grid"] and _grid_mgr:
        for q in regime_buckets["grid"]:
            code = q["code"]
            if code not in _price_map:
                continue

            if code not in _grid_mgr.grid_states:
                base_price = _open_price_map.get(code, _price_map[code])
                _grid_mgr.init_grid(code, base_price)

            high_price = _high_price_map.get(code, _price_map[code])
            low_price = _low_price_map.get(code, _price_map[code])
            held_shares = _portfolio["positions"].get(code, {}).get("shares", 0)

            grid_signals = _grid_mgr.check_crossings(code, high_price, low_price, held_shares)
            for signal in grid_signals:
                trade_amount = _grid_mgr.trade_amount
                current_price = _price_map[code]
                shares = int(trade_amount / current_price / 100) * 100
                if shares < 100:
                    shares = 100

                if signal["action"] == "BUY":
                    pending_orders.append({
                        "action": "BUY",
                        "code": code,
                        "name": code,
                        "shares": shares,
                        "signal_price": signal["price"],
                        "reason": f"网格买入 (L{signal['grid_level']})",
                    })
                elif signal["action"] == "SELL":
                    if held_shares >= shares:
                        pending_orders.append({
                            "action": "SELL",
                            "code": code,
                            "shares": shares,
                            "signal_price": signal["price"],
                            "reason": f"网格卖出 (L{signal['grid_level']})",
                        })

    # C3: 智能定投
    if regime_buckets["smart_dca"]:
        dca_cfg = _config.get("dca", {})
        if dca_cfg:
            base_amount = dca_cfg.get("base_amount", 10000)

            for q in regime_buckets["smart_dca"]:
                code = q["code"]
                price = _price_map.get(code, 0)
                if price <= 0:
                    continue

                hist = _stock_history.get(code)
                ma60 = 0
                atr = 0
                if hist and len(hist["prices"]) >= 60:
                    ma60 = sum(hist["prices"][-60:]) / 60
                    atr_data = {
                        "highs": hist["highs"],
                        "lows": hist["lows"],
                        "closes": hist["prices"],
                    }
                    atr = calculate_atr(atr_data, period=14)

                ai_score = q.get("score", 70)
                stock_data_dca = {"price": price, "ma60": ma60}
                multiplier = calculate_dca_multiplier(stock_data_dca, ai_score)

                if multiplier <= 0:
                    continue

                total_capital = _portfolio["cash"] + sum(
                    pos["shares"] * _price_map.get(c, pos["avg_price"])
                    for c, pos in _portfolio["positions"].items()
                )
                if atr > 0:
                    shares = calculate_position_size(
                        total_capital=total_capital,
                        current_price=price,
                        atr=atr,
                        risk_per_trade=0.01,
                        max_position_pct=0.20,
                    )
                    shares = int(shares * multiplier / 100) * 100
                    if shares < 100:
                        shares = 100
                else:
                    dca_amount = base_amount * multiplier
                    shares = int(dca_amount / price / 100) * 100
                    if shares < 100:
                        shares = 100

                pending_orders.append({
                    "action": "BUY",
                    "code": code,
                    "name": code,
                    "shares": shares,
                    "signal_price": price,
                    "reason": f"Smart DCA (乘数{multiplier})",
                })

    # C4: 趋势策略
    if regime_buckets["trend"]:
        trend_quotes = regime_buckets["trend"]
        decisions = select_stocks(trend_quotes, _portfolio["positions"], _config, mode="backtest")

        total_capital = _portfolio["cash"] + sum(
            pos["shares"] * _price_map.get(c, pos["avg_price"])
            for c, pos in _portfolio["positions"].items()
        )

        for buy in decisions.get("buys", []):
            code = buy["code"]
            price = buy["price"]

            hist = _stock_history.get(code)
            atr = 0
            if hist and len(hist["prices"]) >= 15:
                atr_data = {
                    "highs": hist["highs"],
                    "lows": hist["lows"],
                    "closes": hist["prices"],
                }
                atr = calculate_atr(atr_data, period=14)

            if atr > 0:
                shares = calculate_position_size(
                    total_capital=total_capital,
                    current_price=price,
                    atr=atr,
                    risk_per_trade=0.01,
                    max_position_pct=0.20,
                )
            else:
                shares = buy["shares"]

            if shares < 100:
                continue

            pending_orders.append({
                "action": "BUY",
                "code": code,
                "name": buy["name"],
                "shares": shares,
                "signal_price": price,
                "reason": f"{buy.get('reason', '')} | ATR={atr:.2f}",
            })

        # 左侧均值回归
        mr_candidates = mean_reversion_scan(trend_quotes, _config, min_score=75)
        for mr in mr_candidates:
            if mr.stock_code in _portfolio["positions"]:
                continue
            if any(o["code"] == mr.stock_code for o in pending_orders if o["action"] == "BUY"):
                continue

            current_price = _price_map.get(mr.stock_code, 0)
            if current_price <= 0:
                continue

            hist = _stock_history.get(mr.stock_code)
            atr = 0
            if hist and len(hist["prices"]) >= 15:
                atr_data = {
                    "highs": hist["highs"],
                    "lows": hist["lows"],
                    "closes": hist["prices"],
                }
                atr = calculate_atr(atr_data, period=14)

            if atr > 0:
                shares = calculate_position_size(
                    total_capital=total_capital,
                    current_price=current_price,
                    atr=atr,
                    risk_per_trade=0.01,
                    max_position_pct=0.20,
                )
            else:
                position_value = _portfolio["cash"] * 0.10
                shares = int(position_value / current_price / 100) * 100

            if shares < 100:
                continue

            pending_orders.append({
                "action": "BUY",
                "code": mr.stock_code,
                "name": mr.stock_code,
                "shares": shares,
                "signal_price": current_price,
                "reason": f"{mr.buy_reason} | ATR={atr:.2f}",
            })

    # 持久化待执行订单
    _save_pending_orders(pending_orders, _pending_orders_file)
    print(f"\n[阶段一完成] 生成待执行订单 {len(pending_orders)} 笔")


def phase_order_execution():
    """
    阶段二：14:50 真实状态扣减与买卖订单执行
    执行待执行订单队列 -> 通过 broker 网关执行交易
    严禁直接操作账本 JSON，所有交易必须通过 broker.buy() / broker.sell()
    """
    global _portfolio, _price_map, _open_price_map, _pending_orders_file, broker

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] === 阶段二：订单执行 ===")

    # === 实盘保护：资金验证阻断 ===
    try:
        real_cash = broker.get_cash()
        if real_cash is None:
            msg = "⚠️ 实盘保护：无法获取真实资金（get_cash 返回 None），中止当日交易"
            print(f"[ERROR] {msg}")
            send_notification("实盘预警", msg)
            return
    except Exception as e:
        msg = f"⚠️ 实盘保护：获取真实资金异常（{e}），中止当日交易"
        print(f"[ERROR] {msg}")
        send_notification("实盘预警", msg)
        return

    # 加载待执行订单
    pending_orders = _load_pending_orders(_pending_orders_file)
    if not pending_orders:
        print("无待执行订单，跳过")
        return

    print(f"执行待执行订单 {len(pending_orders)} 笔...")
    executed_orders = []

    for order in pending_orders:
        code = order["code"]
        action = order["action"]
        name = order.get("name", code)
        shares = order["shares"]
        fill_price = _open_price_map.get(code, order["signal_price"])
        reason = order.get("reason", "")

        if action == "SELL":
            # 检查持仓（通过 broker 获取）
            positions = broker.get_positions()
            if code not in positions:
                print(f"  [跳过] {code} 已不在持仓中")
                continue

            # 通过 broker 执行卖出
            result = broker.sell(
                code=code,
                shares=shares,
                price=fill_price,
                name=name,
                reason=reason,
            )

            if result["success"]:
                print(f"  [卖出] {code} @ {fill_price:.2f} x {shares}股 | {result['message']}")
                executed_orders.append(order)
            else:
                print(f"  [卖出失败] {code}: {result['message']}")

        elif action == "BUY":
            # 通过 broker 执行买入
            result = broker.buy(
                code=code,
                shares=shares,
                price=fill_price,
                name=name,
                reason=reason,
            )

            if result["success"]:
                print(f"  [买入] {code} @ {fill_price:.2f} x {shares}股 | {result['message']}")
                executed_orders.append(order)
            else:
                print(f"  [买入失败] {code}: {result['message']}")

    # 移除已执行订单
    pending_orders = [o for o in pending_orders if o not in executed_orders]
    _save_pending_orders(pending_orders, _pending_orders_file)

    # 重新加载 portfolio 以反映 broker 执行后的最新状态
    _portfolio = load_portfolio()

    print(f"\n[阶段二完成] 执行 {len(executed_orders)} 笔，剩余 {len(pending_orders)} 笔")


def phase_daily_settlement():
    """
    阶段三：15:10 日终结算与状态落盘
    结算净值 -> 持仓天数 +1 -> 原子持久化 -> 推送日报
    """
    global _portfolio, _price_map, _pending_orders_file

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] === 阶段三：日终结算 ===")

    # 加载待执行订单（用于日报）
    pending_orders = _load_pending_orders(_pending_orders_file)

    # 结算当日净值
    daily_market_value = sum(
        pos["shares"] * _price_map.get(code, pos["avg_price"])
        for code, pos in _portfolio["positions"].items()
    )
    total_equity = _portfolio["cash"] + daily_market_value

    # 持仓天数 +1
    for pos in _portfolio["positions"].values():
        if "holding_days" in pos:
            pos["holding_days"] += 1

    # 原子持久化
    save_portfolio(_portfolio)

    print(f"\n[阶段三完成] 当日净值: ¥{total_equity:.2f} | 现金: ¥{_portfolio['cash']:.2f} | 待执行: {len(pending_orders)} 笔")
    print(f"资产快照已保存至: data_cache/live_portfolio.json")

    # 推送日报
    send_notification(
        "实盘日报",
        f"净值: ¥{total_equity:.2f}\n"
        f"现金: ¥{_portfolio['cash']:.2f}\n"
        f"持仓: {len(_portfolio['positions'])} 只\n"
        f"待执行: {len(pending_orders)} 笔"
    )

    # 生成 Markdown 日报
    from reporter import generate_daily_report
    report_path = generate_daily_report()

    # 大模型自闭环调优：基于日报进行参数反思
    from auto_tuner import run_daily_reflection
    run_daily_reflection(report_path)


def _load_pending_orders(filepath):
    """加载待执行订单队列"""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_pending_orders(orders, filepath):
    """保存待执行订单队列"""
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    run_live()
