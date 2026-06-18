"""
大模型量化回测引擎 (LLM Backtesting Framework)
自动下载历史数据 -> 时间轴切片 -> 驱动大模型决策 -> 生成资金曲线与绩效报告
含真实交易摩擦成本算法
"""
import os
import gc
import requests
import json
import time
import random
import pandas as pd
from datetime import datetime
from collections import defaultdict

# 引入我们重构好的核心引擎
from strategy_engine import select_stocks, generate_sell_signals, mean_reversion_scan, calculate_dca_multiplier, determine_market_regime
from news_extractor import get_news_sentiment
from risk_manager import check_building_cooldown, calculate_atr, calculate_position_size
from grid_manager import GridManager

# 本地缓存目录
CACHE_DIR = "./data_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# 反爬虫 User-Agent 列表
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

# 交易摩擦成本常量
STAMP_DUTY = 0.001        # 印花税，仅卖出单向收取
COMMISSION = 0.00025      # 券商佣金，买卖双向收取，最低收费5元
MIN_COMMISSION = 5.0      # 最低佣金
SLIPPAGE = 0.001          # 滑点损耗，买卖双向计算（0.1%）

# 回测核心标的池 (选取沪深300中具有代表性的高流动性标的)
BACKTEST_UNIVERSE = [
    "sh600519", "sz000858", "sh601318", "sh600036", "sz002594",
    "sz300750", "sh601899", "sz002475", "sz300059", "sh601012",
    "sz000333", "sz002415", "sh600276", "sh600900", "sz000001",
]


def _get_headers():
    """生成反爬虫伪装请求头"""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://web.sqt.gtimg.cn/",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def _load_cache(code, days):
    """尝试从本地 CSV 缓存加载历史数据，成功返回 list[dict]，失败返回 None"""
    cache_file = os.path.join(CACHE_DIR, f"{code}_history_{days}.csv")
    if not os.path.exists(cache_file):
        return None
    try:
        df = pd.read_csv(cache_file)
        if df.empty:
            return None
        # 读回时还原布尔列
        for col in ("limit_up", "limit_down"):
            if col in df.columns:
                df[col] = df[col].astype(bool)
        records = df.to_dict("records")
        print(f"  [CACHE HIT] {code} 命中本地缓存，跳过网络请求")
        return records
    except Exception as e:
        print(f"  [CACHE WARN] {code} 缓存读取失败: {e}")
        return None


def _save_cache(code, days, records):
    """将历史数据落盘到 CSV 缓存"""
    if not records:
        return
    cache_file = os.path.join(CACHE_DIR, f"{code}_history_{days}.csv")
    try:
        df = pd.DataFrame(records)
        df.to_csv(cache_file, index=False)
    except Exception as e:
        print(f"  [CACHE WARN] {code} 缓存落盘失败: {e}")


def download_historical_data(code, days=100):
    """
    通过腾讯财经前复权 K 线接口下载真实历史日 K 线，并预计算 MACD/MA5 等技术因子。
    强制本地缓存优先：命中则绝不发起网络请求。

    Returns:
        (code, historical_quotes, used_network) 三元组
        used_network: 是否发生了真实网络请求
    """
    # ===== 缓存优先 =====
    cached = _load_cache(code, days)
    if cached is not None:
        return code, cached, False

    # 腾讯接口：直接使用 sh600519/sz000858 格式，qfq 代表前复权
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days + 40},qfq"

    # 网络级重试机制：最多重试 3 次
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=_get_headers(), timeout=3)
            resp.raise_for_status()
            data = resp.json()
            break  # 请求成功，跳出重试循环
        except requests.exceptions.RequestException as e:
            if attempt < 2:
                print(f"[重试] 网络波动，2秒后重试... (第{attempt + 1}次失败: {e})")
                time.sleep(2)
            else:
                print(f"[WARN] 腾讯财经接口彻底异常 {code}: {e}，跳过该标的")
                return code, [], True

    try:
        # 提取 K 线
        stock_data = data.get("data", {}).get(code, {})
        klines = stock_data.get("qfqday", stock_data.get("day", []))

        if not klines:
            print(f"[WARN] 腾讯接口返回空数据 {code}")
            return code, [], True

        # 切片清洗：腾讯接口有时返回 7 个元素（最后附带成交额），强制截取前 6 个
        clean_klines = [k[:6] for k in klines]

        # 转换为 DataFrame
        df = pd.DataFrame(clean_klines, columns=['日期', '开盘价', '收盘价', '最高价', '最低价', '成交量'])

        # 确保价格列转换为 float
        for col in ['开盘价', '收盘价', '最高价', '最低价']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['成交量'] = pd.to_numeric(df['成交量'], errors='coerce').fillna(0)

        # 过滤无效行
        df = df.dropna(subset=['收盘价'])
        df = df.reset_index(drop=True)

        closes = df['收盘价'].values.tolist()
        historical_quotes = []

        # 逐日计算 MACD 和 MA5
        for i in range(len(df)):
            if i < 40:
                continue  # 前40天用于计算初始指标，不输出

            close_price = float(closes[i])
            ma5 = sum(closes[i - 4:i + 1]) / 5
            ma5_trend = "站上5日线" if close_price > ma5 else "跌破5日线"

            # 计算涨跌幅（基于前一日收盘价）
            if i >= 1:
                prev_close = float(closes[i - 1])
                change_pct = (close_price - prev_close) / prev_close * 100 if prev_close > 0 else 0.0
            else:
                change_pct = 0.0

            # MACD 计算
            ema12, ema26 = closes[i - 40], closes[i - 40]
            macds = []
            for p in closes[i - 39 : i + 1]:
                ema12 = ema12 * 11 / 13 + p * 2 / 13
                ema26 = ema26 * 25 / 27 + p * 2 / 27
                macds.append(ema12 - ema26)

            signal = macds[0]
            hists = []
            for m in macds[1:]:
                signal = signal * 8 / 10 + m * 2 / 10
                hists.append(m - signal)

            hist_today = hists[-1]
            hist_yest = hists[-2] if len(hists) >= 2 else 0
            if hist_today > 0 and hist_yest <= 0:
                macd_trend = "MACD金叉"
            elif hist_today < 0 and hist_yest >= 0:
                macd_trend = "MACD死叉"
            elif hist_today > 0 and hist_today > hist_yest:
                macd_trend = "红柱放大(多头)"
            elif hist_today < 0 and hist_today < hist_yest:
                macd_trend = "绿柱放大(空头)"
            else:
                macd_trend = "震荡调整"

            historical_quotes.append(
                {
                    "date": str(df.iloc[i]['日期']).strip(),
                    "code": code,
                    "name": code,
                    "price": close_price,
                    "open": float(df.iloc[i]['开盘价']),
                    "high": float(df.iloc[i]['最高价']),
                    "low": float(df.iloc[i]['最低价']),
                    "change_pct": change_pct,
                    "turnover_rate": 0.0,
                    "volume_ratio": 1.5,
                    "macd_trend": macd_trend,
                    "ma5_trend": ma5_trend,
                    "sector": "核心资产",
                    "limit_up": change_pct >= 9.5,
                    "limit_down": change_pct <= -9.5,
                    "main_fund": 0,
                }
            )

        result = historical_quotes[-days:]
        if result:
            _save_cache(code, days, result)

        # 显式释放 DataFrame 内存，防止长期运行导致 OOM
        del df
        del closes
        gc.collect()

        return code, result, True

    except Exception as e:
        print(f"[WARN] 腾讯财经接口数据解析异常 {code}: {e}，跳过该标的")
        return code, [], True


def _calc_buy_cost(price, shares):
    """计算买入交易的总成本（含滑点+佣金）"""
    actual_price = price * (1 + SLIPPAGE)
    amount = actual_price * shares
    commission = max(MIN_COMMISSION, amount * COMMISSION)
    total_cost = amount + commission
    return actual_price, commission, total_cost


def _calc_sell_revenue(price, shares):
    """计算卖出交易的净收入（含滑点+佣金+印花税）"""
    actual_price = price * (1 - SLIPPAGE)
    amount = actual_price * shares
    commission = max(MIN_COMMISSION, amount * COMMISSION)
    stamp_tax = amount * STAMP_DUTY
    revenue = amount - commission - stamp_tax
    return actual_price, commission, stamp_tax, revenue


def run_backtest(days=30, initial_cash=100000):
    """执行时间序列回测 (含交易摩擦成本)"""
    print(
        f"\n[{datetime.now().strftime('%H:%M:%S')}] "
        f"正在单线程下载历史数据 (本地缓存优先 + 反爬限流模式)..."
    )

    market_history = defaultdict(list)
    network_request_count = 0

    # 放弃并发，采用线性循环下载
    for code in BACKTEST_UNIVERSE:
        print(f"-> 正在获取 {code}...")
        try:
            _, quotes, used_network = download_historical_data(code, days)
            if used_network:
                network_request_count += 1
                # 只有真实网络请求才 sleep 1.5 秒，缓存命中不 sleep
                time.sleep(1.5)
            for q in quotes:
                market_history[q["date"]].append(q)
        except Exception as e:
            print(f"[WARN] 获取 {code} 失败: {e}，跳过该标的")
            continue

    dates = sorted(list(market_history.keys()))
    if not dates:
        print("数据下载全部失败，请检查网络或稍后再试。")
        return

    print(f"数据清洗完毕，共计 {len(dates)} 个交易日 (其中 {network_request_count} 次真实网络请求)。启动回放...\n")

    # 2. 初始化虚拟账户
    portfolio = {"cash": initial_cash, "positions": {}}
    equity_curve = []
    trade_log = []

    config = {
        "initial_capital": initial_cash,
        "max_positions": 5,
        "sell": {"stop_loss_pct": -10.0, "take_profit_pct": 15.0},
        "news_sentiment": get_news_sentiment(),
        # Smart DCA 定投配置（per-stock 参数）
        "dca": {
            "base_amount": 10000,
            "interval_days": 20,
        },
        # 网格交易配置（per-stock 参数）
        "grid": {
            "step_pct": 0.03,
            "trade_amount": 5000,
        },
    }

    # 动态策略路由：GridManager 始终初始化（内部按 stock 懒初始化网格）
    grid_cfg = config.get("grid", {})
    grid_mgr = GridManager(
        step_pct=grid_cfg.get("step_pct", 0.03),
        trade_amount=grid_cfg.get("trade_amount", 5000),
    )
    print(f"[ROUTER] 动态策略路由已启用 | 网格步长={grid_cfg.get('step_pct', 0.03)*100}% | 每格金额={grid_cfg.get('trade_amount', 5000)}")

    # 3. 时间循环：逐日回放 (T+1 撮合机制)
    pending_orders = []  # 待执行订单队列
    dca_last_date_idx = {}  # per-stock 上次定投的日期索引
    stock_history = defaultdict(lambda: {"prices": [], "highs": [], "lows": [], "main_funds": []})  # per-stock 历史累积

    # 预热阶段：用前 40 天数据填充 stock_history，确保 ATR 等指标有足够数据
    warmup_days = min(40, len(dates) - 1)  # 至少留 1 天用于回测
    for warmup_idx in range(warmup_days):
        warmup_date = dates[warmup_idx]
        warmup_quotes = market_history[warmup_date]
        for q in warmup_quotes:
            code = q["code"]
            hist = stock_history[code]
            hist["prices"].append(q["price"])
            hist["highs"].append(q.get("high", q["price"]))
            hist["lows"].append(q.get("low", q["price"]))
            hist["main_funds"].append(q.get("main_fund", 0))
    print(f"[预热] 已用前 {warmup_days} 天数据填充 stock_history，ATR 指标可正常计算")

    # 从预热结束后的第一天开始正式回测
    for date_idx, date in enumerate(dates[warmup_days:]):
        date_idx = date_idx + warmup_days  # 调整 date_idx 为原始索引
        print("=" * 50)
        print(f"回测日期: {date}")
        print("=" * 50)

        daily_quotes = market_history[date]
        if not daily_quotes:
            continue

        # 构建当日价格映射表
        price_map = {q["code"]: q["price"] for q in daily_quotes}
        open_price_map = {q["code"]: q.get("open", q["price"]) for q in daily_quotes}
        high_price_map = {q["code"]: q.get("high", q["price"]) for q in daily_quotes}
        low_price_map = {q["code"]: q.get("low", q["price"]) for q in daily_quotes}

        # 累积当日数据到 stock_history（带滚动窗口上限，防止无限增长）
        for q in daily_quotes:
            code = q["code"]
            hist = stock_history[code]
            hist["prices"].append(q["price"])
            hist["highs"].append(q.get("high", q["price"]))
            hist["lows"].append(q.get("low", q["price"]))
            hist["main_funds"].append(q.get("main_fund", 0))
            # 滚动窗口上限：只保留最近 100 天（determine_market_regime 最多需 60 天）
            if len(hist["prices"]) > 100:
                hist["prices"] = hist["prices"][-100:]
                hist["highs"] = hist["highs"][-100:]
                hist["lows"] = hist["lows"][-100:]
                hist["main_funds"] = hist["main_funds"][-100:]

        # --- 动作 A: 执行 T-1 日的待执行订单 (使用当日开盘价) ---
        if pending_orders:
            print(f"执行昨日待执行订单 {len(pending_orders)} 笔 (使用今日开盘价)...")
            executed_orders = []

            for order in pending_orders:
                code = order["code"]
                action = order["action"]

                if action == "SELL":
                    # 卖出订单：使用当日开盘价成交
                    if code not in portfolio["positions"]:
                        print(f"  [跳过] {code} 已不在持仓中")
                        continue

                    shares = order["shares"]
                    fill_price = open_price_map.get(code, order["signal_price"])
                    actual_price, commission, stamp_tax, revenue = _calc_sell_revenue(fill_price, shares)

                    portfolio["cash"] += revenue
                    total_fee = commission + stamp_tax
                    
                    # 部分卖出：只减少持仓股数，不删除整个持仓
                    if code in portfolio["positions"]:
                        pos = portfolio["positions"][code]
                        if pos["shares"] > shares:
                            pos["shares"] -= shares
                        else:
                            # 全部卖出，删除持仓
                            del portfolio["positions"][code]

                    trade_log.append(
                        f"[{date}] 卖出 {code} @ {fill_price:.2f}(实际{actual_price:.2f}) x {shares}股 "
                        f"| 费用{total_fee:.2f}(佣金{commission:.2f}+印花税{stamp_tax:.2f}) "
                        f"| 回收{revenue:.2f} | {order['reason']}"
                    )
                    print(trade_log[-1])
                    executed_orders.append(order)

                elif action == "BUY":
                    # 买入订单：使用当日开盘价成交
                    fill_price = open_price_map.get(code, order["signal_price"])
                    shares = order["shares"]
                    actual_price, commission, total_cost = _calc_buy_cost(fill_price, shares)

                    if portfolio["cash"] >= total_cost:
                        portfolio["cash"] -= total_cost

                        # 如果已经持有该股票，必须累加股数并计算加权平均成本
                        if code in portfolio["positions"]:
                            old_pos = portfolio["positions"][code]
                            total_shares = old_pos["shares"] + shares
                            new_avg_price = (
                                (old_pos["avg_price"] * old_pos["shares"]) + (actual_price * shares)
                            ) / total_shares
                            portfolio["positions"][code]["shares"] = total_shares
                            portfolio["positions"][code]["avg_price"] = new_avg_price
                        else:
                            portfolio["positions"][code] = {
                                "shares": shares,
                                "avg_price": actual_price,
                                "name": order["name"],
                                "holding_days": 0,
                            }

                        trade_log.append(
                            f"[{date}] 买入 {code} @ {fill_price:.2f}(实际{actual_price:.2f}) x {shares}股 "
                            f"| 佣金{commission:.2f} | 总成本{total_cost:.2f} "
                            f"| AI: {order.get('reason', '')}"
                        )
                        print(trade_log[-1])
                        executed_orders.append(order)
                    else:
                        print(f"  [跳过] {code} 资金不足: 需要 ¥{total_cost:.2f}, 可用 ¥{portfolio['cash']:.2f}")

            # 清空已执行订单
            pending_orders = [o for o in pending_orders if o not in executed_orders]

        # --- 动作 B: 生成当日卖出信号 (止盈/止损) ---
        sells = generate_sell_signals(portfolio["positions"], daily_quotes, config)
        for sell in sells:
            pending_orders.append({
                "action": "SELL",
                "code": sell["code"],
                "shares": sell["shares"],
                "signal_price": sell["price"],
                "reason": sell["reason"],
            })
            print(f"  [信号] 生成卖出订单: {sell['code']} (信号价: {sell['price']:.2f})")

        # --- 动作 C: 动态策略路由 — 逐票识别 regime 并分发 ---
        # C1: 按 regime 分组候选股票
        regime_buckets = {"grid": [], "smart_dca": [], "trend": []}
        for q in daily_quotes:
            code = q["code"]
            hist = stock_history.get(code)
            if not hist or len(hist["prices"]) < 60:
                # 数据不足，默认趋势
                regime_buckets["trend"].append(q)
                continue
            regime = determine_market_regime(hist)
            regime_buckets[regime].append(q)

        # 打印路由摘要
        regime_summary = {k: len(v) for k, v in regime_buckets.items() if v}
        if regime_summary:
            print(f"  [ROUTER] regime 分布: {regime_summary}")

        # C2: 网格策略 (震荡市) — 高抛低吸
        if regime_buckets["grid"] and grid_mgr:
            grid_cfg = config.get("grid", {})
            for q in regime_buckets["grid"]:
                code = q["code"]
                if code not in price_map:
                    continue

                # 初始化网格基准价
                if code not in grid_mgr.grid_states:
                    base_price = open_price_map.get(code, price_map[code])
                    grid_mgr.init_grid(code, base_price)
                    print(f"  [GRID] {code} 初始化网格基准价: {base_price:.2f}")

                high_price = high_price_map.get(code, price_map[code])
                low_price = low_price_map.get(code, price_map[code])
                held_shares = portfolio["positions"].get(code, {}).get("shares", 0)

                grid_signals = grid_mgr.check_crossings(code, high_price, low_price, held_shares)
                for signal in grid_signals:
                    trade_amount = grid_mgr.trade_amount
                    current_price = price_map[code]
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
                        print(f"  [GRID] {code} 触发网格买入 (L{signal['grid_level']}) @ {signal['price']:.2f}")
                    elif signal["action"] == "SELL":
                        if held_shares >= shares:
                            pending_orders.append({
                                "action": "SELL",
                                "code": code,
                                "shares": shares,
                                "signal_price": signal["price"],
                                "reason": f"网格卖出 (L{signal['grid_level']})",
                            })
                            print(f"  [GRID] {code} 触发网格卖出 (L{signal['grid_level']}) @ {signal['price']:.2f}")
                        else:
                            print(f"  [GRID] {code} 持仓不足，跳过网格卖出")

        # C3: 智能定投 (超跌市) — ATR 波动率自适应仓位 + 乘数检测
        if regime_buckets["smart_dca"]:
            dca_cfg = config.get("dca", {})
            if dca_cfg:
                base_amount = dca_cfg.get("base_amount", 10000)
                interval_days = dca_cfg.get("interval_days", 20)

                for q in regime_buckets["smart_dca"]:
                    code = q["code"]
                    price = price_map.get(code, 0)
                    if price <= 0:
                        continue

                    # 定投间隔检查 (per-stock)
                    last_idx = dca_last_date_idx.get(code, -interval_days)
                    if date_idx - last_idx < interval_days:
                        continue

                    # 计算 MA60 和 ATR
                    hist = stock_history.get(code)
                    ma60 = 0
                    atr = 0
                    if hist and len(hist["prices"]) >= 60:
                        ma60 = sum(hist["prices"][-60:]) / 60
                        # 计算 ATR (需要 highs, lows, closes)
                        atr_data = {
                            "highs": hist["highs"],
                            "lows": hist["lows"],
                            "closes": hist["prices"],  # prices 即收盘价序列
                        }
                        atr = calculate_atr(atr_data, period=14)

                    # 获取 AI 评分
                    ai_score = q.get("score", 70)

                    # 计算定投乘数
                    stock_data_dca = {"price": price, "ma60": ma60}
                    multiplier = calculate_dca_multiplier(stock_data_dca, ai_score)

                    if multiplier <= 0:
                        print(f"    [DCA] {code} 乘数=0，暂停定投")
                        continue

                    # ATR 波动率自适应仓位计算
                    total_capital = portfolio["cash"] + sum(
                        pos["shares"] * price_map.get(c, pos["avg_price"])
                        for c, pos in portfolio["positions"].items()
                    )
                    if atr > 0:
                        # 使用 ATR 仓位计算，risk_per_trade=1%，max_position_pct=20%
                        shares = calculate_position_size(
                            total_capital=total_capital,
                            current_price=price,
                            atr=atr,
                            risk_per_trade=0.01,
                            max_position_pct=0.20,
                        )
                        # 应用定投乘数
                        shares = int(shares * multiplier / 100) * 100
                        if shares < 100:
                            shares = 100
                        dca_amount = shares * price
                    else:
                        # ATR 数据不足，回退到原逻辑
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
                        "reason": f"Smart DCA (乘数{multiplier}, AI={ai_score}, ATR={atr:.2f})",
                    })
                    print(f"    [DCA] {code} 定投 {shares}股 (乘数{multiplier}, 金额{dca_amount:.0f}, ATR={atr:.2f})")
                    dca_last_date_idx[code] = date_idx

        # C4: 趋势策略 (趋势市) — ATR 波动率自适应仓位 + AI 打分选股
        if regime_buckets["trend"]:
            trend_quotes = regime_buckets["trend"]
            decisions = select_stocks(trend_quotes, portfolio["positions"], config, mode="backtest")

            # 计算当前账户总净值
            total_capital = portfolio["cash"] + sum(
                pos["shares"] * price_map.get(c, pos["avg_price"])
                for c, pos in portfolio["positions"].items()
            )

            for buy in decisions.get("buys", []):
                code = buy["code"]
                price = buy["price"]

                # ATR 波动率自适应仓位：废弃 cash/max_positions 均分逻辑
                hist = stock_history.get(code)
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
                    # ATR 数据不足，回退到原逻辑
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
                print(f"  [信号] 生成趋势买入订单: {code} {shares}股 (信号价: {price:.2f}, ATR={atr:.2f})")

            # 左侧均值回归抄底 (仅对趋势候选，同样使用 ATR 仓位)
            mr_candidates = mean_reversion_scan(trend_quotes, config, min_score=75)
            for mr in mr_candidates:
                if mr.stock_code in portfolio["positions"]:
                    continue
                if any(o["code"] == mr.stock_code for o in pending_orders if o["action"] == "BUY"):
                    continue

                current_price = price_map.get(mr.stock_code, 0)
                if current_price <= 0:
                    continue

                # ATR 波动率自适应仓位
                hist = stock_history.get(mr.stock_code)
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
                    # ATR 数据不足，回退到 10% 资金配比
                    position_value = portfolio["cash"] * 0.10
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
                print(f"  [信号] 生成均值回归买入订单: {mr.stock_code} {shares}股 (信号价: {current_price:.2f}, ATR={atr:.2f})")

        # --- 动作 D: 结算当日净值 ---
        daily_market_value = sum(
            pos["shares"] * price_map.get(code, pos["avg_price"])
            for code, pos in portfolio["positions"].items()
        )
        total_equity = portfolio["cash"] + daily_market_value

        # ===== 建仓冷却锁：限制下行趋势中的开仓数量 =====
        max_positions = config.get("max_positions", config.get("strategy", {}).get("max_positions", 10))
        today_equity = portfolio["cash"] + daily_market_value
        cooldown_limit = check_building_cooldown(portfolio["positions"], config, today_value=today_equity)
        if cooldown_limit < max_positions:
            # 过滤 buy 信号：只保留前 cooldown_limit 笔新仓订单
            buy_orders = [o for o in pending_orders if o["action"] == "BUY"]
            if len(buy_orders) > cooldown_limit:
                # 保留卖单和其他非新仓订单，只限制新仓订单数量
                original_buy_count = len(buy_orders)
                # 按信号价排序取前 cooldown_limit 笔（优先价格最优）
                sorted_buys = sorted(buy_orders, key=lambda x: x.get("signal_price", float("inf")))[:cooldown_limit]
                remaining = set(id(o) for o in sorted_buys)
                pending_orders = [o for o in pending_orders if not (o["action"] == "BUY" and id(o) in remaining)]
                print(f"[建仓冷却] 限制当日开仓数至 {cooldown_limit}（原 {original_buy_count} 笔买入信号）")
        equity_curve.append((date, total_equity))

        # --- 动作 E: 每日闭盘后对所有持仓执行 holding_days +1 ---
        for pos in portfolio["positions"].values():
            if "holding_days" in pos:
                pos["holding_days"] += 1

        print(f"当日收盘净值: {total_equity:.2f} (仓位: {daily_market_value/total_equity*100:.1f}%)")
        print(f"待执行订单队列: {len(pending_orders)} 笔")
        
        # --- 内存清理：显式释放当日临时变量，防止长期运行 OOM ---
        del price_map
        del open_price_map
        del high_price_map
        del low_price_map
        del daily_quotes
        
        print("等待大模型冷却...")
        time.sleep(1)  # 给显卡一点喘息时间

    # 4. 最后一天未执行的订单作废
    if pending_orders:
        print(f"\n[警告] 回测结束，{len(pending_orders)} 笔未执行订单已作废:")
        for order in pending_orders:
            print(f"  - {order['action']} {order['code']} (信号价: {order['signal_price']:.2f})")

    # 4. 生成绩效报告
    print("\n" + "=" * 50)
    print("大模型量化回测报告 (LLM Backtest Report)")
    print("=" * 50)
    final_equity = equity_curve[-1][1]
    total_return = (final_equity - initial_cash) / initial_cash * 100

    # 简易最大回撤计算
    max_drawdown = 0
    peak = initial_cash
    for _, eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_drawdown:
            max_drawdown = dd

    print(f"测试区间: {dates[0]} 至 {dates[-1]}")
    print(f"初始资金: {initial_cash:.2f}")
    print(f"最终净值: {final_equity:.2f}")
    print(f"区间收益率: {total_return:+.2f}%")
    print(f"最大回撤: -{max_drawdown:.2f}%")
    print(f"总交易次数: {len(trade_log)}")
    print(f"交易成本: 印花税{STAMP_DUTY*100:.2f}% 佣金{COMMISSION*100:.3f}% 滑点{SLIPPAGE*100:.2f}%")
    print("=" * 50)


if __name__ == "__main__":
    # 为了演示速度，这里默认回测过去 15 个交易日（约3周）
    # 如果你的显卡顶得住，可以改为 60（3个月）甚至 120（半年）
    run_backtest(days=60, initial_cash=100000)
