"""
满血慢脑节点 (Slow Brain)
职责：死循环拉取数据 -> 跑 AI 与风控引擎 (涵盖网格、定投、趋势、左侧) -> 广播交易指令
"""
import os
import sys
import time
import json
import zmq
from datetime import datetime
from collections import defaultdict, OrderedDict
from dotenv import load_dotenv

# 添加项目根目录到 Python 路径，确保 core 和 feeds 模块可被引用
PROJECT_ROOT = os.path.dirname(__file__)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv()
from core.logger_config import logger
from core.strategy_engine import select_stocks, generate_sell_signals, mean_reversion_scan, calculate_dca_multiplier, determine_market_regime
from core.risk_manager import calculate_atr, calculate_position_size
from core.grid_manager import GridManager
from core.state_manager import load_portfolio
from core.backtester import download_historical_data, BACKTEST_UNIVERSE

LIVE_UNIVERSE = BACKTEST_UNIVERSE

# ==========================================
# 🔒 DCA 日内冷却锁（防止同一标的日内重复定投榨干资金）
# ==========================================
# 记录今日已执行过定投的标的代码，每只股票每天只允许定投一次
# 跨日时（检测到日期变化）自动清空集合
_dca_traded_today = set()
_last_dca_date = datetime.now().date()
_last_index_change_pct = None  # 增加：大盘风控缓存

_stale_cache_counter = 0

def _get_index_change_pct_from_cache():
    """从本地缓存文件读取大盘跌幅"""
    global _stale_cache_counter
    cache_file = os.path.join(PROJECT_ROOT, "data_cache", "index_sh000001.json")
    try:
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # 超过 10 分钟未更新视为 stale
            if time.time() - data.get("ts", 0) > 600:
                _stale_cache_counter += 1
                logger.warning(f"[INDEX_CACHE_STALE] 大盘缓存文件超过 10 分钟未更新！连续陈旧次数: {_stale_cache_counter}，准备 fallback 到内存缓存")
                return None
                
            _stale_cache_counter = 0
            return float(data.get("change_pct", 0.0))
        else:
            logger.warning("[INDEX_CACHE_MISSING] 大盘缓存文件不存在，可能 index_cache_updater 未启动！")
    except Exception as e:
        logger.error(f"[INDEX_CACHE_ERROR] 读取大盘缓存失败: {e}")
    return None

class LRUStockHistory(OrderedDict):
    def __init__(self, maxsize=50, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.maxsize = maxsize

    def __getitem__(self, key):
        if key not in self:
            val = {"prices": [], "highs": [], "lows": [], "main_funds": []}
            self[key] = val
            return val
        self.move_to_end(key)
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        if len(self) > self.maxsize:
            self.popitem(last=False)

    def get(self, key, default=None):
        if key in self:
            self.move_to_end(key)
            return self[key]
        return default


def run_slow_brain():
    logger.info("=" * 70)
    logger.info("🧠 满血慢脑节点 (Slow Brain) 已启动 - ZeroMQ 指挥官")
    logger.info("=" * 70)

    # 启动 ZeroMQ 广播基站
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    BRAIN_NODE_PUB_ENDPOINT = "tcp://127.0.0.1:5555"
    socket.bind(BRAIN_NODE_PUB_ENDPOINT)
    logger.info(f"[ZMQ_BIND] source=brain_node endpoint={BRAIN_NODE_PUB_ENDPOINT}")

    _stock_history = LRUStockHistory(maxsize=50)
    _config = {
        "max_positions": 5,
        "sell": {"stop_loss_pct": -10.0, "take_profit_pct": 15.0},
        "dca": {"base_amount": 10000, "interval_days": 20},
        # 🔧 网格火力口径重塑：
        # - trade_amount 提升至 10000 元，确保越过 6000 元免五生死线（避免最低 5 元手续费反噬）
        # - step_pct 拉宽至 5%，降低开火频率，防止震荡市中资金被网格快速榨干
        "grid": {"step_pct": 0.05, "trade_amount": 10000},
    }
    
    _grid_mgr = GridManager(
        step_pct=_config["grid"]["step_pct"], 
        trade_amount=_config["grid"]["trade_amount"]
    )

    def send_order(action: str, code: str, shares: int, price: float, reason: str):
        import uuid
        # 5分钟指纹幂等性ID
        order_id = f"{code}_{action}_{int(time.time())//300}"
        trade_id = str(uuid.uuid4())
        order = {
            "order_id": order_id,
            "trade_id": trade_id,
            "decision_id": "",
            "event_ids": [],
            "source": "brain_node",
            "action": action,
            "code": code,
            "shares": int(shares),
            "signal_price": float(price),
            "reason": str(reason),
            "timestamp": datetime.now().isoformat()
        }
        socket.send_string(f"TRADE_SIGNAL {json.dumps(order)}")
        logger.info(f"📡 [广播指令] {action}: {code} | 股数: {shares} | 信号价: {price:.2f} | ID: {order_id} | 原因: {reason}")

    # ================= 新增：启动时进行历史数据预热 =================
    logger.info("⏳ 正在预热 60 日历史行情底座，构建策略路由上下文...")
    for code in LIVE_UNIVERSE:
        try:
            _, hist_quotes, _ = download_historical_data(code, days=60) # 拉取 60 天
            if hist_quotes:
                _stock_history[code]["prices"] = [q["price"] for q in hist_quotes]
                _stock_history[code]["highs"] = [q.get("high", q["price"]) for q in hist_quotes]
                _stock_history[code]["lows"] = [q.get("low", q["price"]) for q in hist_quotes]
                # 如果有资金流数据也可以在这里 append
        except Exception as e:
            logger.warning(f"预热 {code} 历史数据失败: {e}")
        time.sleep(0.2) # 防止瞬间并发把 API 接口打挂
    logger.info(f"✅ 历史底座预热完毕，共加载 {len(_stock_history)} 只标的。")
    # ===============================================================

    while True:
        now = datetime.now()
        # 仅在盘中交易时段进行 AI 轮询 (9:30-11:30, 13:00-15:00)
        is_trading_time = (now.hour == 9 and now.minute >= 30) or (now.hour == 10) or (now.hour == 11 and now.minute <= 30) or (13 <= now.hour < 15)
        
        if not is_trading_time:
            time.sleep(60)
            continue

        logger.info(f"\n[{now.strftime('%H:%M:%S')}] 🧠 慢脑开始新一轮深度行情演算...")

        # 1. 加载最新资产账本
        portfolio = load_portfolio()
        if not portfolio:
            portfolio = {"cash": 100000.0, "positions": {}}
        
        # 容错：确保关键字段存在
        if "cash" not in portfolio:
            portfolio["cash"] = 100000.0
        if "positions" not in portfolio:
            portfolio["positions"] = {}

        # ==========================================
        # 🚨 大盘系统性风险熔断检测
        # ==========================================
        systemic_risk_halt = False
        global _last_index_change_pct
        
        try:
            index_change_pct = _get_index_change_pct_from_cache()
            if index_change_pct is not None:
                _last_index_change_pct = index_change_pct
            else:
                # 缓存为空或超过5分钟，fallback 到内存缓存
                if _last_index_change_pct is None:
                    # 内存缓存也为空，默认进入保守模式 (-999.0)
                    logger.warning("[INDEX_CACHE_MISSING] 大盘缓存与内存缓存均为空，默认进入保守模式！")
                    _last_index_change_pct = -999.0
                    
            if _last_index_change_pct <= -2.5:
                systemic_risk_halt = True
                logger.error(f"🚨 [风控拦截] 上证指数跌幅 {_last_index_change_pct}% <= -2.5%，触发全局系统性风险规避，本轮禁止买入！")
        except Exception as e:
            logger.warning(f"[风控预警] 大盘检测逻辑异常，保守拦截: {e}")
            systemic_risk_halt = True

        # 2. 获取实时行情 - ThreadPoolExecutor 并发拉取
        daily_quotes = []
        import concurrent.futures
        import random

        def fetch_one(code):
            try:
                # 批次内的轻量随机抖动，防封杀 (0.1s ~ 0.3s)
                time.sleep(random.uniform(0.1, 0.3))
                _, quotes, _ = download_historical_data(code, days=1)
                return quotes
            except Exception as e:
                logger.warning(f"[MarketData] 获取 {code} 实时行情失败: {e}")
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(LIVE_UNIVERSE), 10)) as executor:
            future_to_code = {executor.submit(fetch_one, code): code for code in LIVE_UNIVERSE}
            for future in concurrent.futures.as_completed(future_to_code):
                try:
                    quotes = future.result()
                    if quotes:
                        daily_quotes.extend(quotes)
                except Exception as e:
                    logger.error(f"[MarketData] 并发获取失败: {e}")

        if not daily_quotes:
            logger.info("未获取到行情，休眠等待...")
            time.sleep(60)
            continue

        price_map = {q["code"]: q["price"] for q in daily_quotes}
        
        # === 新增：将今日最新价格实时追加到历史底座中，维持 60 日滚动 ===
        for q in daily_quotes:
            c = q["code"]
            if c in _stock_history:
                _stock_history[c]["prices"].append(q["price"])
                _stock_history[c]["highs"].append(q.get("high", q["price"]))
                _stock_history[c]["lows"].append(q.get("low", q["price"]))
                # 截断保持 60 天长度，节省内存
                _stock_history[c]["prices"] = _stock_history[c]["prices"][-60:]
                _stock_history[c]["highs"] = _stock_history[c]["highs"][-60:]
                _stock_history[c]["lows"] = _stock_history[c]["lows"][-60:]
        # ============================================================
        open_price_map = {q["code"]: q.get("open", q["price"]) for q in daily_quotes}
        high_price_map = {q["code"]: q.get("high", q["price"]) for q in daily_quotes}
        low_price_map = {q["code"]: q.get("low", q["price"]) for q in daily_quotes}

        # 3. 生成卖出信号并直接广播
        sells = generate_sell_signals(portfolio["positions"], daily_quotes, _config)
        for sell in sells:
            send_order(action="SELL", code=sell["code"], shares=sell["shares"], price=sell["price"], reason=sell["reason"])

        # 4. 动态策略路由分类
        regime_buckets = {"grid": [], "smart_dca": [], "trend": []}
        for q in daily_quotes:
            code = q["code"]
            hist = _stock_history.get(code)
            if not hist or len(hist["prices"]) < 60:
                regime_buckets["trend"].append(q)
                continue
            regime = determine_market_regime(hist)
            regime_buckets[regime].append(q)

        # 🚨 全局系统性风险规避：清空所有买入篮子
        if systemic_risk_halt:
            regime_buckets["grid"].clear()
            regime_buckets["smart_dca"].clear()
            regime_buckets["trend"].clear()
            logger.warning("🚨 [风控拦截] 已清空所有策略的买入队列。")

        total_capital = portfolio.get("cash", 0.0) + sum(pos.get("shares", 0) * price_map.get(c, pos.get("avg_price", 0.0)) for c, pos in portfolio.get("positions", {}).items())

        # ==========================================
        # C2: 网格策略广播
        # ==========================================
        if regime_buckets["grid"] and _grid_mgr:
            for q in regime_buckets["grid"]:
                code = q["code"]
                if code not in price_map: continue

                if code not in _grid_mgr.grid_states:
                    _grid_mgr.init_grid(code, open_price_map.get(code, price_map[code]))

                held_shares = portfolio["positions"].get(code, {}).get("shares", 0)
                grid_signals = _grid_mgr.check_crossings(code, high_price_map[code], low_price_map[code], held_shares)
                
                for signal in grid_signals:
                    shares = max(100, int(_grid_mgr.trade_amount / price_map[code] / 100) * 100)
                    if signal["action"] == "BUY":
                        send_order(action="BUY", code=code, shares=shares, price=signal["price"], reason=f"网格买入 (L{signal['grid_level']})")
                    elif signal["action"] == "SELL" and held_shares >= shares:
                        send_order(action="SELL", code=code, shares=shares, price=signal["price"], reason=f"网格卖出 (L{signal['grid_level']})")

        # ==========================================
        # C3: 智能定投广播 (已注入利润垫核算)
        # ==========================================
        # 🔒 跨日检测：如果日期变化，清空 DCA 冷却锁
        global _dca_traded_today, _last_dca_date
        today = datetime.now().date()
        if today != _last_dca_date:
            _dca_traded_today.clear()
            _last_dca_date = today
            logger.info("🔒 [DCA冷却锁] 跨日重置，已清空今日定投记录")

        if regime_buckets["smart_dca"]:
            base_amount = _config["dca"]["base_amount"]
            for q in regime_buckets["smart_dca"]:
                code = q["code"]
                price = price_map.get(code, 0)
                if price <= 0: continue

                # 🔒 DCA 日内冷却锁：同一标的每天只允许定投一次
                if code in _dca_traded_today:
                    logger.debug(f"🔒 [DCA冷却锁] {code} 今日已定投，跳过")
                    continue

                hist = _stock_history.get(code)
                ma60 = sum(hist["prices"][-60:]) / 60 if hist and len(hist["prices"]) >= 60 else 0
                atr = calculate_atr({"highs": hist["highs"], "lows": hist["lows"], "closes": hist["prices"]}, 14) if hist and len(hist["prices"]) >= 15 else 0

                multiplier = calculate_dca_multiplier({"price": price, "ma60": ma60}, q.get("score", 70))
                if multiplier <= 0: continue

                if atr > 0:
                    # 提取该股的现有持仓数据，计算浮盈比例
                    existing_pos = portfolio["positions"].get(code, {})
                    avg_price = existing_pos.get("avg_price", 0)
                    floating_profit = (price - avg_price) / avg_price if avg_price > 0 else 0.0

                    shares = calculate_position_size(
                        total_capital=total_capital,
                        current_price=price,
                        atr=atr,
                        risk_per_trade=0.01,
                        max_position_pct=0.20,
                        floating_profit_pct=floating_profit # 注入浮盈参数
                    )
                    shares = max(100, int(shares * multiplier / 100) * 100)
                else:
                    shares = max(100, int(base_amount * multiplier / price / 100) * 100)

                send_order(action="BUY", code=code, shares=shares, price=price, reason=f"Smart DCA (乘数{multiplier})")

                # 🔒 记录已定投标的，防止日内重复
                _dca_traded_today.add(code)
                logger.info(f"🔒 [DCA冷却锁] {code} 已加入今日定投黑名单，当前黑名单: {len(_dca_traded_today)} 只")

        # ==========================================
        # C4: 趋势策略 & 左侧抄底广播 (已注入利润垫核算)
        # ==========================================
        if regime_buckets["trend"]:
            trend_quotes = regime_buckets["trend"]
            
            # 右侧趋势
            decisions = select_stocks(trend_quotes, portfolio["positions"], _config, mode="live")
            for buy in decisions.get("buys", []):
                code, price = buy["code"], buy["price"]
                hist = _stock_history.get(code)
                atr = calculate_atr({"highs": hist["highs"], "lows": hist["lows"], "closes": hist["prices"]}, 14) if hist and len(hist["prices"]) >= 15 else 0
                
                # 提取该股的现有持仓数据，计算浮盈比例
                existing_pos = portfolio["positions"].get(code, {})
                avg_price = existing_pos.get("avg_price", 0)
                floating_profit = (price - avg_price) / avg_price if avg_price > 0 else 0.0

                if atr > 0:
                    shares = calculate_position_size(
                        total_capital=total_capital, 
                        current_price=price, 
                        atr=atr, 
                        risk_per_trade=0.01, 
                        max_position_pct=0.20,
                        floating_profit_pct=floating_profit # 注入浮盈参数
                    )
                else:
                    shares = buy["shares"]

                if shares >= 100:
                    send_order(action="BUY", code=code, shares=shares, price=price, reason=f"{buy.get('reason', '')} | ATR={atr:.2f}")

            # 左侧均值回归
            mr_candidates = mean_reversion_scan(trend_quotes, _config, min_score=75)
            for mr in mr_candidates:
                if mr.stock_code in portfolio["positions"]: continue # 抄底股通常无底仓，直接continue了
                price = price_map.get(mr.stock_code, 0)
                if price <= 0: continue

                hist = _stock_history.get(mr.stock_code)
                atr = calculate_atr({"highs": hist["highs"], "lows": hist["lows"], "closes": hist["prices"]}, 14) if hist and len(hist["prices"]) >= 15 else 0
                
                # 抄底时因为没有底仓，floating_profit_pct 默认为 0.0
                if atr > 0:
                    shares = calculate_position_size(
                        total_capital=total_capital, 
                        current_price=price, 
                        atr=atr, 
                        risk_per_trade=0.01, 
                        max_position_pct=0.20,
                        floating_profit_pct=0.0 
                    )
                else:
                    shares = int(total_capital * 0.10 / price / 100) * 100
                
                if shares >= 100:
                    send_order(action="BUY", code=mr.stock_code, shares=shares, price=price, reason=f"{mr.buy_reason} | ATR={atr:.2f}")

        logger.info(f"🧠 本轮演算及广播完毕，休眠 60 秒等待下个切片...")
        time.sleep(60)

if __name__ == "__main__":
    run_slow_brain()