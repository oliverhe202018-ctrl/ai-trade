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
from core.backtester import BACKTEST_UNIVERSE
from core.trading_state import get_trading_state, TradingState

PAPER_MODE = os.environ.get("PAPER_MODE", "0") == "1" or "--paper" in sys.argv
if PAPER_MODE:
    logger.info("🛡️ [PAPER MODE] 独立模拟盘日志解耦架构启用，实盘交易将被完全物理拦截！")

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

def _append_paper_signal(order: dict):
    """
    独立模拟盘日志写入（解耦架构）
    """
    log_file = os.path.join(PROJECT_ROOT, "data_cache", "paper_signal_log.jsonl")
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(order, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"[PaperMode] 写入模拟信号失败: {e}")

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
                    logger.error(f"[INDEX_CACHE_HALT] 大盘缓存连续陈旧 {_stale_cache_counter} 次，抛出 CACHE_STALE_ERROR 信号！")
                    return "CACHE_STALE_ERROR"
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



# --- WATCHDOG HEARTBEAT ---
def _update_heartbeat():
    import json, os, time
    from filelock import FileLock
    hb_file = os.path.join(PROJECT_ROOT, "data_cache", "heartbeats.json")
    lock_file = hb_file + ".lock"
    os.makedirs(os.path.dirname(hb_file), exist_ok=True)
    try:
        from filelock import FileLock
        with FileLock(lock_file, timeout=2):
            data = dict()
            if os.path.exists(hb_file):
                try:
                    with open(hb_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except:
                    pass
            data["brain_node"] = time.time()
            with open(hb_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
    except Exception as e:
        from core.logger_config import logger
        logger.warning(f"[HEARTBEAT_FAIL] 无法写入心跳文件: {e}")
# --------------------------


def _check_momentum_resonance(code, candidate, hot_sectors, stock_history):
    """
    P1 动量与龙头确认机制
    属于热门板块的个股必须满足：竞价涨幅>3% 或 早盘成交量>昨日30%
    """
    sector = candidate.get("sector", "")
    if sector not in hot_sectors:
        return True, "非热门板块独立走势，放行"
        
    hist_df = stock_history.get(code)
    if hist_df is None or len(hist_df) < 2:
        return False, "缺乏历史数据，无法确认昨日成交量"
        
    try:
        # 获取昨日 volume
        # hist_df 可能是按日期升序
        yest_vol = hist_df.iloc[-2].get('volume', 0)
        if yest_vol <= 0:
            return False, "昨日成交量为0"
            
        if not market_provider:
            return False, "无行情源，动量确认失败"
        tick = market_provider.get_realtime_quote(code)
        if not tick:
            return False, "无法获取今日快照"
            
        today_vol = tick.get("volume", 0)
        # qmt provider doesn't always provide lastClose in our mapped dict (we mapped it to price if lastPrice is missing), so let's check
        last_close = tick.get("lastClose", tick.get("price", 0.0))
        open_price = tick.get("open", 0.0)
        
        if open_price > 0 and last_close > 0:
            open_pct = (open_price / last_close - 1) * 100
            if open_pct > 3.0:
                return True, f"热门板块前排: 竞价涨幅 {open_pct:.2f}% > 3%"
                
        if today_vol > 0.3 * yest_vol:
            return True, f"热门板块前排: 放量 {today_vol}/{yest_vol} > 30%"
            
        return False, "属于热门板块，但无量价共振（跟风标的），拦截"
    except Exception as e:
        logger.warning(f"[{code}] 动量确认异常: {e}")
        return False, "动量检查异常"

from feeds.qmt_market_provider import QMTMarketProvider
try:
    market_provider = QMTMarketProvider()
except Exception as e:
    logger.critical(f"行情接口初始化失败: {e}")
    market_provider = None


def run_brain_node():
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    if not PAPER_MODE:
        socket.bind("tcp://*:5555")
        logger.info("✅ ZeroMQ 广播频道已绑定 tcp://*:5555")
    else:
        logger.info("🛡️ [PAPER MODE] 物理断开 ZMQ 绑定，确保模拟信号完全只写本地日志。")

    # ===============================================================
    # 历史数据预热（启动时一次性加载，后续增量更新）
    # ===============================================================
    _stock_history = {}
    if market_provider:
        logger.info("📡 正在使用 QMT 预热历史数据底座...")
        for code in LIVE_UNIVERSE:
            try:
                hist_df = market_provider.get_bars(code, period="1d", count=30)
                import pandas as pd
                if isinstance(hist_df, pd.DataFrame) and not hist_df.empty:
                    _stock_history[code] = hist_df
            except Exception as e:
                logger.warning(f"[预热] {code} 历史数据加载失败: {e}")
        logger.info(f"✅ 历史底座预热完毕，共加载 {len(_stock_history)} 只标的。")
    else:
        logger.error("❌ 无可用行情源，跳过历史预热。")
    # ===============================================================

    while True:
        now = datetime.now()
        _update_heartbeat()
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
            if index_change_pct == "CACHE_STALE_ERROR":
                systemic_risk_halt = True
                logger.error("🚨 [风控拦截] [INDEX_CACHE_STALE] 由于缓存陈旧达到阈值，主动拒绝买入！")
                _last_index_change_pct = "CACHE_STALE_ERROR"
            elif index_change_pct is not None:
                _last_index_change_pct = index_change_pct
            else:
                # 缓存为空或超过5分钟，fallback 到内存缓存
                if _last_index_change_pct in (None, "CACHE_STALE_ERROR"):
                    # 内存缓存也为空或失效，默认进入中性模式 (0.0)，防止误触发熔断
                    logger.warning("[INDEX_CACHE_MISSING] 大盘缓存与内存缓存均为空或已失效，默认进入中性模式，不触发熔断！")
                    _last_index_change_pct = 0.0
                    
            if isinstance(_last_index_change_pct, (int, float)) and _last_index_change_pct <= -2.5:
                systemic_risk_halt = True
                logger.error(f"🚨 [风控拦截] 上证指数跌幅 {_last_index_change_pct}% <= -2.5%，触发全局系统性风险规避，本轮禁止买入！")
        except Exception as e:
            logger.warning(f"[风控预警] 大盘检测逻辑异常，保守拦截: {e}")
            systemic_risk_halt = True

        # 2. 获取实时行情 - QMT 统一快照
        daily_quotes = []
        try:
            if market_provider:
                snapshots = market_provider.get_market_snapshot(LIVE_UNIVERSE)
                daily_quotes = list(snapshots.values())
            else:
                logger.error("行情接口异常，无法获取实时切片。")
        except Exception as e:
            logger.error(f"[获取行情异常] {e}")
            
        # === 写入行情源健康状态 ===
        health_data = {}
        try:
            if market_provider:
                health_data = market_provider.health_check()
                import tempfile
                health_file_path = os.path.join(PROJECT_ROOT, "data_cache", "market_health.json")
                dir_name = os.path.dirname(health_file_path)
                os.makedirs(dir_name, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(health_data, f)
                os.replace(tmp_path, health_file_path)
        except Exception as e:
            logger.error(f"写入行情健康状态失败: {e}")
            
        logger.info(f"📡 本轮拉取到 {len(daily_quotes)} 条行情记录")

        # ==========================================
        # 3. 交易状态校验 — Fail-close
        # ==========================================
        trading_state = get_trading_state()
        if trading_state != TradingState.ACTIVE.value:
            logger.warning(f"[TRADING_STATE_UNAVAILABLE] 当前交易状态: {trading_state}，禁止买入，直接进入卖出检查。")
            # 即使状态异常，仍然允许卖出已持仓的风险标的
            config = {"initial_capital": 100000, "max_positions": 10, "strategy": {"type": "trend"}}
            sell_signals = generate_sell_signals(portfolio.get("positions", {}), daily_quotes, config)
            for sell_order in sell_signals:
                try:
                    order = _serialize_order(sell_order)
                    if PAPER_MODE:
                        _append_paper_signal(order)
                        logger.info(f"🛡️ [PAPER MODE] 模拟卖出信号落盘: {order.get('code')} x{order.get('quantity')}")
                    else:
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
            config = {"initial_capital": 100000, "max_positions": 10, "strategy": {"type": "trend"}}
            
            health_status = health_data.get("status", "DOWN")
            if health_status in ["STALE", "DOWN"]:
                logger.warning(f"[风控预警] 行情源状态为 {health_status}，禁止生成任何 BUY 信号！")
                buy_candidates = []
            else:
                if PAPER_MODE:
                    picks_file = os.path.join(PROJECT_ROOT, "data_cache", "potential_picks.json")
                    if os.path.exists(picks_file):
                        try:
                            with open(picks_file, "r", encoding="utf-8") as pf:
                                picks_data = json.load(pf)
                                for pick in picks_data.get("picks", []):
                                    f_score = pick.get("fusion_score", 0)
                                    risk_tags = pick.get("risk_tags", [])
                                    # 针对可能缺失 fusion_score 的历史数据，提供容错
                                    if f_score >= 60 or pick.get("priority") == "High" or pick.get("watch_priority") == "High":
                                        if "致命" not in str(risk_tags):
                                            buy_candidates.append({
                                                "code": pick["code"],
                                                "price": pick.get("last_price", 0),
                                                "reason": f"Paper Pick (Fusion: {f_score})",
                                                "strategy": "ai_fusion"
                                            })
                        except Exception as e:
                            logger.error(f"[PaperMode] 读取潜力股失败: {e}")
                    # 补齐最新行情价格
                    for c in buy_candidates:
                        q = [x for x in daily_quotes if x.get("code") == c["code"]]
                        if q:
                            c["price"] = q[0].get("lastPrice", q[0].get("price", 0))
                else:
                    buy_candidates = select_stocks(daily_quotes, portfolio.get("positions", {}), config, _stock_history, market_provider=market_provider)
            logger.info(f"[选股] 候选买入标的: {len(buy_candidates)} 只")
        except Exception as e:
            logger.error(f"[选股异常] {e}")

        try:
            config = {"initial_capital": 100000, "max_positions": 10, "strategy": {"type": "trend"}}
            sell_signals = generate_sell_signals(portfolio.get("positions", {}), daily_quotes, config)
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
        # 7. 广播或落盘卖出信号
        # ==========================================
        for sell_order in sell_signals:
            try:
                order = _serialize_order(sell_order)
                if PAPER_MODE:
                    _append_paper_signal(order)
                    logger.info(f"🛡️ [PAPER MODE] 模拟卖出信号落盘: {sell_order.get('code')} x{sell_order.get('quantity')}")
                else:
                    socket.send_string(f"TRADE_SIGNAL {json.dumps(order)}")
                    logger.info(f"[卖出广播] {sell_order.get('code')} x{sell_order.get('quantity')}")
            except Exception as e:
                logger.error(f"[广播异常] {e}")

        # ==========================================
        # 8. 买入决策 — 系统性风险熔断拦截
        # ==========================================
        hot_sectors = []
        try:
            _news_cache_path = os.path.join(PROJECT_ROOT, "data_cache", "news_sentiment_cache.json")
            if os.path.exists(_news_cache_path):
                with open(_news_cache_path, "r", encoding="utf-8") as _nf:
                    ns = json.load(_nf)
                    hot_sectors = ns.get("hot_sectors", [])
        except Exception as e:
            logger.warning(f"读取资讯缓存获取 hot_sectors 失败: {e}")

        if systemic_risk_halt:
            logger.warning("🚨 [系统性风险] 本轮买入全部暂停，仅执行卖出。")
        else:
            for candidate in buy_candidates[:5]:  # 每轮最多5个买入信号
                code = candidate.get("code", "")
                
                # 动量与龙头确认机制
                is_momentum, mo_reason = _check_momentum_resonance(code, candidate, hot_sectors, _stock_history)
                if not is_momentum:
                    logger.info(f"[前排确认拦截] {code} {mo_reason}")
                    continue
                else:
                    logger.info(f"[前排确认通过] {code} {mo_reason}")

                
                # DCA 日内冷却
                if _is_dca_locked(code):
                    logger.info(f"[DCA_LOCK] {code} 今日已触发过定投，跳过。")
                    continue

                try:
                    hist_data = _stock_history.get(code)
                    if hist_data is None or len(hist_data) < 20:
                        logger.warning(f"[K线不足] {code} 历史数据不足20根，无法计算真实 ATR 与指标，默认跳过，返回 NO_TRADE。")
                        continue
                        
                    atr = calculate_atr(hist_data)
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
                    
                    if PAPER_MODE:
                        _append_paper_signal(order)
                        logger.info(f"🛡️ [PAPER MODE] 模拟买入信号落盘: {code} x{final_qty} @ {order['price']:.2f}")
                    else:
                        socket.send_string(f"TRADE_SIGNAL {json.dumps(order)}")
                        logger.info(f"[买入广播] {code} x{final_qty} @ {order['price']:.2f}")
                    
                    if candidate.get("strategy") == "dca":
                        _set_dca_lock(code)
                        
                except Exception as e:
                    logger.error(f"[买入异常] {code}: {e}")

        # ==========================================
        # 9. 网格订单处理
        # ==========================================
        for grid_order in grid_orders:
            try:
                order = _serialize_order(grid_order)
                if PAPER_MODE:
                    _append_paper_signal(order)
                    logger.info(f"🛡️ [PAPER MODE] 模拟网格信号落盘: {order.get('code')} {order.get('action')} x{order.get('quantity')}")
                else:
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
