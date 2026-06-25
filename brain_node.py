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

# 添加项目根目录到 Python 路径，确保 core 和 feeds 模块可被引用
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── .env 安全加载：过滤 null 字符键值对（Windows <frozen os> 限制）──
try:
    from dotenv import dotenv_values
    for _k, _v in dotenv_values().items():
        if _v is not None and "\x00" not in _k and "\x00" not in _v:
            os.environ.setdefault(_k, _v)
except Exception as _e:
    print(f"[DOTENV_ERROR] .env 加载异常，继续运行: {_e}")

# === 资讯情绪模块导入 ===
try:
    from feeds.news_extractor import get_news_sentiment
    _NEWS_AVAILABLE = True
except Exception as _ne:
    print(f"[NEWS_IMPORT_WARN] 资讯模块导入失败: {_ne}")
    _NEWS_AVAILABLE = False

from core.logger_config import logger
from core.strategy_engine import select_stocks, generate_sell_signals, mean_reversion_scan, calculate_dca_multiplier, determine_market_regime
from core.risk_manager import calculate_atr, calculate_position_size
from core.grid_manager import GridManager
from core.state_manager import load_portfolio
from core.backtester import download_historical_data, BACKTEST_UNIVERSE
from core.trading_state import get_trading_state, TradingState

LIVE_UNIVERSE = BACKTEST_UNIVERSE

# ==========================================
# 🔒 DCA 日内冷却锁（防止同一标的日内重复定投榨干资金）
# ==========================================
_dca_daily_lock: dict[str, str] = {}   # {stock_code: "YYYY-MM-DD"}

def _is_dca_locked(code: str) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    return _dca_daily_lock.get(code) == today

def _set_dca_lock(code: str):
    today = datetime.now().strftime("%Y-%m-%d")
    _dca_daily_lock[code] = today

# ==========================================
# 📡 大盘缓存读取 + 陈旧计数器
# ==========================================
_last_index_change_pct = None
_stale_cache_counter = 0
_STALE_CACHE_HALT_THRESHOLD = 5

def _get_index_change_pct_from_cache():
    global _stale_cache_counter
    cache_file = os.path.join(PROJECT_ROOT, "data_cache", "index_sh000001.json")
    try:
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
            cache_ts = cache_data.get("timestamp", cache_data.get("ts", 0))
            if time.time() - cache_ts > 600:
                _stale_cache_counter += 1
                logger.warning(f"[INDEX_CACHE_STALE] 大盘缓存文件超过 10 分钟未更新！连续陈旧次数: {_stale_cache_counter}，准备 fallback 到内存缓存")
                if _stale_cache_counter >= _STALE_CACHE_HALT_THRESHOLD:
                    logger.error(f"[INDEX_CACHE_HALT] 大盘缓存连续陈旧 {_stale_cache_counter} 次，触发保守熔断！")
                return None
            _stale_cache_counter = 0
            return cache_data.get("change_pct")
        else:
            logger.warning("[INDEX_CACHE_MISSING] 大盘缓存文件不存在，可能 index_cache_updater 未启动！")
            return None
    except Exception as e:
        logger.error(f"[INDEX_CACHE_ERROR] 读取大盘缓存异常: {e}")
        return None


def _calculate_portfolio_value(portfolio: dict, daily_quotes: list) -> float:
    """计算组合总市值"""
    cash = portfolio.get("cash", 0)
    positions = portfolio.get("positions", {})
    market_value = 0.0
    
    quotes_by_code = {q["code"]: q for q in daily_quotes if "code" in q}
    
    for code, pos in positions.items():
        qty = pos.get("quantity", 0)
        if qty <= 0:
            continue
        if code in quotes_by_code:
            price = quotes_by_code[code].get("current_price", pos.get("avg_cost", 0))
        else:
            price = pos.get("avg_cost", 0)
        market_value += qty * price
    
    return cash + market_value


def _serialize_order(order: dict) -> dict:
    """确保订单可 JSON 序列化"""
    result = {}
    for k, v in order.items():
        if hasattr(v, 'item'):
            result[k] = v.item()
        elif isinstance(v, (int, float, str, bool, type(None))):
            result[k] = v
        else:
            result[k] = str(v)
    return result


def run_brain_node():
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    socket.bind("tcp://*:5555")
    
    logger.info("✅ ZeroMQ 广播频道已绑定 tcp://*:5555")

    # ===============================================================
    # 历史数据预热（启动时一次性加载，后续增量更新）
    # ===============================================================
    _stock_history = {}
    logger.info("📡 正在预热历史数据底座...")
    for code in LIVE_UNIVERSE:
        try:
            hist_df, _, _ = download_historical_data(code, days=30)
            if hist_df is not None and not hist_df.empty:
                _stock_history[code] = hist_df
        except Exception as e:
            logger.warning(f"[预热] {code} 历史数据加载失败: {e}")
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

        # ==========================================
        # 0. 获取资讯情绪因子（每轮开头执行，结果写入 data_cache）
        # ==========================================
        if _NEWS_AVAILABLE:
            try:
                _news_sentiment = get_news_sentiment(hours=24)
                _news_cache_path = os.path.join(PROJECT_ROOT, "data_cache", "news_sentiment_cache.json")
                _news_sentiment["_ts"] = time.time()
                _news_sentiment["_datetime"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                os.makedirs(os.path.join(PROJECT_ROOT, "data_cache"), exist_ok=True)
                with open(_news_cache_path, "w", encoding="utf-8") as _nf:
                    json.dump(_news_sentiment, _nf, ensure_ascii=False, indent=2)
                logger.info(f"[NEWS] 情绪因子已写入缓存: macro={_news_sentiment.get('macro_sentiment',0)}, source={_news_sentiment.get('_source','?')}")
            except Exception as _news_err:
                logger.warning(f"[NEWS] 资讯情绪获取失败，不影响主流程: {_news_err}")

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
                    # 内存缓存也为空，默认进入中性模式 (0.0)，防止误触发熔断
                    logger.warning("[INDEX_CACHE_MISSING] 大盘缓存与内存缓存均为空，默认进入中性模式，不触发熔断！")
                    _last_index_change_pct = 0.0
                    
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
                logger.warning(f"[行情拉取] {code} 失败: {e}")
                return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(fetch_one, code): code for code in LIVE_UNIVERSE}
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    daily_quotes.extend(result)
        
        logger.info(f"📡 本轮拉取到 {len(daily_quotes)} 条行情记录")

        # ==========================================
        # 3. 交易状态校验 — Fail-close
        # ==========================================
        trading_state = get_trading_state()
        if trading_state != TradingState.ACTIVE:
            logger.warning(f"[TRADING_STATE_UNAVAILABLE] 当前交易状态: {trading_state}，禁止买入，直接进入卖出检查。")
            # 即使状态异常，仍然允许卖出已持仓的风险标的
            sell_signals = generate_sell_signals(portfolio, daily_quotes, _stock_history)
            for sell_order in sell_signals:
                try:
                    order = _serialize_order(sell_order)
                    socket.send_string(f"TRADE_SIGNAL {json.dumps(order)}")
                    logger.info(f"[卖出广播] {sell_order.get('code')} {sell_order.get('action')} x{sell_order.get('quantity')}")
                except Exception as e:
                    logger.error(f"[广播异常] 卖出信号发送失败: {e}")
            time.sleep(60)
            continue

        # ==========================================
        # 4. 策略引擎：选股 + 评分
        # ==========================================
        buy_candidates = []
        sell_signals = []

        try:
            buy_candidates = select_stocks(portfolio, daily_quotes, _stock_history)
            logger.info(f"[选股] 候选买入标的: {len(buy_candidates)} 只")
        except Exception as e:
            logger.error(f"[选股异常] {e}")

        try:
            sell_signals = generate_sell_signals(portfolio, daily_quotes, _stock_history)
            logger.info(f"[卖出信号] {len(sell_signals)} 条")
        except Exception as e:
            logger.error(f"[卖出异常] {e}")

        # ==========================================
        # 5. 均值回归扫描
        # ==========================================
        try:
            mr_candidates = mean_reversion_scan(portfolio, daily_quotes, _stock_history)
            if mr_candidates:
                buy_candidates.extend(mr_candidates)
                logger.info(f"[均值回归] 额外候选: {len(mr_candidates)} 只")
        except Exception as e:
            logger.warning(f"[均值回归异常] {e}")

        # ==========================================
        # 6. 网格管理
        # ==========================================
        grid_orders = []
        try:
            gm = GridManager(portfolio)
            grid_orders = gm.generate_grid_orders(daily_quotes)
            logger.info(f"[网格] 生成订单: {len(grid_orders)} 条")
        except Exception as e:
            logger.warning(f"[网格异常] {e}")

        # ==========================================
        # 7. 广播卖出信号
        # ==========================================
        for sell_order in sell_signals:
            try:
                order = _serialize_order(sell_order)
                socket.send_string(f"TRADE_SIGNAL {json.dumps(order)}")
                logger.info(f"[卖出广播] {sell_order.get('code')} x{sell_order.get('quantity')}")
            except Exception as e:
                logger.error(f"[广播异常] {e}")

        # ==========================================
        # 8. 买入决策 — 系统性风险熔断拦截
        # ==========================================
        if systemic_risk_halt:
            logger.warning("🚨 [系统性风险] 本轮买入全部暂停，仅执行卖出。")
        else:
            for candidate in buy_candidates[:5]:  # 每轮最多5个买入信号
                code = candidate.get("code", "")
                
                # DCA 日内冷却
                if _is_dca_locked(code):
                    logger.info(f"[DCA_LOCK] {code} 今日已触发过定投，跳过。")
                    continue

                try:
                    atr = calculate_atr(_stock_history.get(code))
                    position_size = calculate_position_size(
                        portfolio.get("cash", 0),
                        candidate.get("price", 0),
                        atr,
                        risk_pct=0.01
                    )
                    
                    if position_size <= 0:
                        continue

                    # 定投乘数
                    dca_multiplier = calculate_dca_multiplier(candidate, portfolio)
                    final_qty = int(position_size * dca_multiplier / 100) * 100  # 取整到百股
                    
                    if final_qty <= 0:
                        continue

                    order = {
                        "code": code,
                        "action": "BUY",
                        "quantity": final_qty,
                        "price": candidate.get("price", 0),
                        "reason": candidate.get("reason", "AI信号"),
                        "timestamp": now.isoformat()
                    }
                    
                    order = _serialize_order(order)
                    socket.send_string(f"TRADE_SIGNAL {json.dumps(order)}")
                    logger.info(f"[买入广播] {code} x{final_qty} @ {order['price']:.2f}")
                    
                    if candidate.get("strategy") == "dca":
                        _set_dca_lock(code)
                        
                except Exception as e:
                    logger.error(f"[买入异常] {code}: {e}")

        # ==========================================
        # 9. 网格订单广播
        # ==========================================
        for grid_order in grid_orders:
            try:
                order = _serialize_order(grid_order)
                socket.send_string(f"TRADE_SIGNAL {json.dumps(order)}")
            except Exception as e:
                logger.error(f"[网格广播异常] {e}")

        # ==========================================
        # 10. 市场模式判断（用于日志监控）
        # ==========================================
        try:
            regime = determine_market_regime(daily_quotes, _stock_history)
            logger.info(f"[市场模式] {regime}")
        except Exception as e:
            logger.warning(f"[市场模式异常] {e}")

        logger.info(f"🧠 本轮演算及广播完毕，休眠 60 秒等待下个切片...")
        time.sleep(60)


if __name__ == "__main__":
    run_brain_node()
