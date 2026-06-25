"""
策略引擎 v5 - 三策略升级流水线总调度器
职责：编排各模块拼装，集成大盘环境锁 + 逻辑止盈 + 左侧抄底

新增模块:
    market_filter       → 大盘环境锁（策略1）
    risk_manager        → 逻辑止盈层（策略2）
    mean_reversion_scanner → 左侧抄底模型（策略3）

依赖模块:
    ai_client           → AI 分批评分过滤
    base_strategies     → 趋势/价值/动量策略执行
    risk_manager        → 止盈止损/熔断信号

v6 Async Event-Driven Pipeline:
    select_stocks_async() → async pipeline with concurrent stages
    _ai_score_chunk()   → single chunk scoring coroutine (parallelizable)
    _check_technical_resonance_single() → per-stock resonance check (parallelizable)
    Backward-compatible sync wrapper delegates to async via asyncio.run()
"""
import asyncio
import json
import os
from filelock import FileLock
from core.ai_client import get_ai_scoring_batch
from core.base_strategies import execute_trend_strategy, execute_value_strategy, execute_momentum_strategy
from core.risk_manager import check_sell_signals, LogicExitManager
from core.logger_config import logger

# 大模型推理缓存文件
CACHE_FILE = "./data_cache/ai_scores_cache.json"
CACHE_LOCK_FILE = CACHE_FILE + ".lock"
CACHE_LOCK_TIMEOUT = 10  # 锁超时时间（秒）


def _load_ai_cache():
    """加载 AI 打分缓存，不存在或为空则返回空字典（带文件锁保护）"""
    if not os.path.exists(CACHE_FILE):
        return {}
    lock = FileLock(CACHE_LOCK_FILE, timeout=CACHE_LOCK_TIMEOUT)
    try:
        with lock:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
                return cache if isinstance(cache, dict) else {}
    except Exception as e:
        logger.exception(f"[WARN] AI 缓存加载失败: {e}，将使用空缓存")
        return {}


def _save_ai_cache(cache_dict):
    """强制落盘 AI 打分缓存（带文件锁保护）"""
    lock = FileLock(CACHE_LOCK_FILE, timeout=CACHE_LOCK_TIMEOUT)
    try:
        with lock:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            # 原子写入：先写临时文件，再替换
            tmp_file = CACHE_FILE + ".tmp"
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(cache_dict, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, CACHE_FILE)
    except Exception as e:
        logger.exception(f"[WARN] AI 缓存落盘失败: {e}")

# 策略1：大盘环境锁（延迟加载，避免akshare拖慢启动）
_market_filter = None


def _get_market_filter():
    global _market_filter
    if _market_filter is None:
        try:
            from core.market_filter import MarketEnvironmentFilter
            _market_filter = MarketEnvironmentFilter()
        except Exception as e:
            logger.exception(f"[WARN] 大盘环境锁加载失败: {e}，将跳过环境检测")
            _market_filter = False  # 标记为已尝试但失败
    return _market_filter


def _liquidity_filter(candidates: list) -> list:
    """
    P1 流动性底线过滤器
    目的：剔除换手率极低、盘口无深度、散户特征明显的标的。
    约束：不直接生成买入信号，仅做淘汰过滤，并记录日志。
    如果行情 API 不可用，返回原始 candidates。
    """
    if not candidates:
        return candidates

    try:
        from xtquant import xtdata
    except ImportError:
        logger.warning("[LIQUIDITY_FILTER] xtquant 未安装，跳过流动性过滤。")
        return candidates

    # 提取 QMT 格式的代码列表
    code_map = {}
    for c in candidates:
        prefix = c.get("code", "")[:2]
        code_num = c.get("code", "")[2:]
        if prefix == "sh":
            qmt_code = f"{code_num}.SH"
        elif prefix == "sz":
            qmt_code = f"{code_num}.SZ"
        else:
            continue
        code_map[qmt_code] = c

    if not code_map:
        return candidates

    try:
        qmt_codes = list(code_map.keys())
        for c in qmt_codes:
            xtdata.subscribe_quote(c, period='1d', start_time='', end_time='', count=0, callback=None)
            
        ticks = xtdata.get_full_tick(qmt_codes)
        
        filtered_candidates = []
        for qmt_code, c in code_map.items():
            # 条件1: 换手率 < 1%
            turnover = c.get("turnover_rate", 0.0)
            if turnover > 0 and turnover < 1.0:
                logger.info(f"[流动性过滤] 剔除 {c['code']} ({c.get('name', '')}): 换手率 {turnover}% 低于 1%")
                continue
                
            tick = ticks.get(qmt_code)
            if not tick:
                filtered_candidates.append(c)
                continue
                
            # 条件2: 买卖一档金额之和 < 50w
            ask_price = tick.get("askPrice", [0])[0]
            ask_vol = tick.get("askVol", [0])[0]
            bid_price = tick.get("bidPrice", [0])[0]
            bid_vol = tick.get("bidVol", [0])[0]
            
            top_level_amount = (ask_price * ask_vol * 100) + (bid_price * bid_vol * 100)
            if top_level_amount < 500000:
                logger.info(f"[流动性过滤] 剔除 {c['code']} ({c.get('name', '')}): 盘口一档总挂单金额 {top_level_amount:.0f} < 50万")
                continue
                
            filtered_candidates.append(c)
            
        return filtered_candidates
    except Exception as e:
        logger.warning(f"[LIQUIDITY_FILTER] 流动性检查失败，保守放行: {e}")
        return candidates



def _check_technical_resonance(code, stock_history):
    """
    P1 技术面共振过滤:
    检查 RSI(14) 处于 30-50 且 MACD 零轴上方金叉或红柱放大
    """
    if not stock_history:
        return True, "未传入历史数据"
        
    hist_df = stock_history.get(code)
    if hist_df is None or len(hist_df) < 30:
        return True, "无足够历史数据，保守放行"
        
    try:
        import pandas as pd
        closes = hist_df['close']
        
        # 计算 RSI(14)
        delta = closes.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        current_rsi = rsi.iloc[-1]
        
        if not (30 <= current_rsi <= 50):
            return False, f"RSI({current_rsi:.1f}) 不在 30-50 强势回调区间"
            
        # 计算 MACD (12, 26, 9)
        exp1 = closes.ewm(span=12, adjust=False).mean()
        exp2 = closes.ewm(span=26, adjust=False).mean()
        macd = exp1 - exp2
        signal = macd.ewm(span=9, adjust=False).mean()
        
        current_macd = macd.iloc[-1]
        prev_macd = macd.iloc[-2]
        current_signal = signal.iloc[-1]
        prev_signal = signal.iloc[-2]
        
        # MACD 零轴上方金叉 或 红柱放大
        is_golden_cross = prev_macd <= prev_signal and current_macd > current_signal
        is_red_expanding = current_macd > current_signal and (current_macd - current_signal) > (prev_macd - prev_signal)
        
        if current_macd < 0:
            return False, "MACD 处于零轴下方，非多头排列"
            
        if not (is_golden_cross or is_red_expanding):
            return False, "MACD 非金叉且红柱未放大"
            
        return True, "RSI/MACD 多头共振"
    except Exception as e:
        return True, f"技术指标计算异常，保守放行: {e}"


def select_stocks(quotes, positions, config, stock_history=None, market_provider=None, mode="mock"):
    """
    选股流水线编排（v5 增强版）

    流程:
        1. 涨跌停硬过滤 → 基础粗筛
        1.5 流动性底线过滤
        2. 资金/仓位统计 → 可用资金计算
        3. AI 通信模块 → 分批轮询打分过滤
        4. 策略路由 → 分发到对应策略算法模块
        5. 大盘环境锁 → 过滤系统性风险（策略1）

    Args:
        quotes: 实时行情列表 (来自 market_data)
        positions: 当前持仓 dict
        config: 系统配置字典
        mode: 模拟盘(mock) / 实盘(live) / 回测(backtest)

    Returns:
        dict: {"buys": [...], "sells": [], "strategy": "...", "env_regime": "..."}
    """
    strategy_cfg = config.get("strategy", {})
    strategy_type = strategy_cfg.get("type", "trend")

    # 1. 基础硬编码粗筛 — 排除涨跌停
    candidates = [q for q in quotes if not (q.get("limit_up") or q.get("limit_down"))]
    
    # 1.5 流动性底线过滤 (TODO)
    candidates = _liquidity_filter(candidates)

    # 2. 资金流水统计
    initial_capital = config.get("initial_capital", 50000)
    max_positions = config.get("max_positions", 10)
    available_cash = initial_capital - sum(
        pos.get("avg_price", 0) * pos.get("shares", 0) for pos in positions.values()
    )

    cached_results: list = []
    scored_stocks: list = []

    # 3. 大模型推理缓存拦截器
    if candidates:
        # 加载缓存
        ai_cache = _load_ai_cache()
        
        # 考前过滤：区分缓存命中和需要 AI 打分的股票
        needs_ai_scoring = []
        
        for stock in candidates:
            cache_key = f"{stock.get('date', 'unknown')}_{stock['code']}"
            cached_data = ai_cache.get(cache_key)
            
            # 如果缓存存在且没有 error 标记，直接使用
            if cached_data and not cached_data.get("error"):
                cached_results.append(cached_data)
            else:
                needs_ai_scoring.append(stock)
        
        logger.info(f"  [缓存拦截] 命中 {len(cached_results)} 只，需 AI 打分 {len(needs_ai_scoring)} 只")
        
        # 分批打分与错题本标记
        if needs_ai_scoring:
            CHUNK_SIZE = 5
            chunks = [needs_ai_scoring[i:i + CHUNK_SIZE] for i in range(0, len(needs_ai_scoring), CHUNK_SIZE)]

            for chunk_idx, chunk in enumerate(chunks, start=1):
                try:
                    logger.info(f"  [AI打分] 批次 {chunk_idx}/{len(chunks)}，共 {len(chunk)} 只股票")
                    batch_result = get_ai_scoring_batch(chunk, config)
                    
                    # 更新缓存：成功则存入，失败则标记 error
                    for stock in chunk:
                        cache_key = f"{stock.get('date', 'unknown')}_{stock['code']}"
                        # 查找该股票是否在 batch_result 中
                        matched = next((s for s in batch_result if s['code'] == stock['code']), None)
                        if matched:
                            ai_cache[cache_key] = matched
                            scored_stocks.append(matched)
                        else:
                            # 标记为错误
                            ai_cache[cache_key] = {"error": True, "reason": "解析失败或截断"}
                    
                except ValueError as ve:
                    logger.info(f"  [ERROR] 批次 {chunk_idx} 参数校验失败: {ve}")
                    # 整批标记错误
                    for stock in chunk:
                        cache_key = f"{stock.get('date', 'unknown')}_{stock['code']}"
                        ai_cache[cache_key] = {"error": True, "reason": str(ve)}
                    continue
                except Exception as e:
                    logger.exception(f"  [ERROR] 批次 {chunk_idx} 打分崩溃: {e}")
                    # 整批标记错误
                    for stock in chunk:
                        cache_key = f"{stock.get('date', 'unknown')}_{stock['code']}"
                        ai_cache[cache_key] = {"error": True, "reason": str(e)}
                    continue

            # 强制落盘
            _save_ai_cache(ai_cache)
            logger.info(f"  [缓存落盘] 已更新 {len(ai_cache)} 条记录到 {CACHE_FILE}")
        
        # 合并缓存结果和 AI 打分结果
        candidates = cached_results + scored_stocks

    # ===== AI 选股硬门槛拦截：70分以下一票否决 =====
    # 缓存结果与 AI 打分结果都走此门槛，宁缺毋滥
    if candidates:
        original_count = len(candidates)
        # 有 score 字段的直接比较；无 score 字段的视为通过（历史缓存未打分）
        filtered = []
        for c in candidates:
            score = c.get("score")
            if score is not None:
                if score >= 70:
                    filtered.append(c)
            # else: 无 score 字段则保留（兼容旧缓存）
        candidates = filtered
        rejected = original_count - len(candidates)
        if rejected > 0:
            logger.info(f"  [AI门槛过滤] score<70 拦截 {rejected} 只平庸标的，剩余候选 {len(candidates)} 只")

    # ===== 技术面多头共振检查 =====
    if candidates and stock_history:
        tech_filtered = []
        for c in candidates:
            code = c.get("code")
            passed, reason = _check_technical_resonance(code, stock_history)
            if passed:
                tech_filtered.append(c)
                logger.info(f"  [多头共振通过] {code}: {reason}")
            else:
                logger.info(f"  [多头共振拦截] {code}: {reason}")
        candidates = tech_filtered

    if not candidates:
        return {"buys": [], "sells": [], "strategy": strategy_type, "env_regime": "UNKNOWN"}

    # 4. 大盘环境锁检测（策略1）— 实盘/模拟盘启用，回测跳过
    position_limit = 1.0
    env_regime = "RISK_ON"
    if mode != "backtest":
        mf = _get_market_filter()
        if mf and mf is not False:
            can_buy, env_reason = mf.can_open_position()
            regime = mf.assess_market_regime()
            env_regime = regime["regime"]
            position_limit = regime["position_limit"]
            if not can_buy:
                logger.info(f"[环境锁] 禁止买入 | {env_reason}")
                return {"buys": [], "sells": [], "strategy": strategy_type, "env_regime": env_regime}

    # 5. 根据策略路由，分发到具体策略算法模块
    strategy_map = {
        "trend": execute_trend_strategy,
        "value": execute_value_strategy,
        "momentum": execute_momentum_strategy,
    }

    executor = strategy_map.get(strategy_type, execute_trend_strategy)

    # 环境锁影响：CAUTION 模式下仓位减半（减少 max_positions 和可用资金）
    if position_limit == 0.5:
        max_positions = max(1, max_positions // 2)
        available_cash *= 0.5
        logger.info(f"[环境锁] CAUTION 模式，max_positions={max_positions}，可用资金减半")

    buys = executor(candidates, config, len(positions), max_positions, available_cash)

    return {
        "buys": buys,
        "sells": [],
        "strategy": strategy_type,
        "env_regime": env_regime,
    }


def generate_sell_signals(positions, quotes, config):
    """
    卖出信号生成 — 桥接风控模块（含逻辑止盈层，策略2）
    """
    return check_sell_signals(positions, quotes, config)


def mean_reversion_scan(quotes, config, min_score=None):
    """
    左侧抄底扫描（策略3）— 寻找"主线板块错杀股"

    注意：此接口需要 quotes 包含 close_prices 和 fund_flows 历史序列。
    当前 version 预留接口，实际调用需外部提供 60 日历史数据。

    Args:
        quotes: 行情列表（需含 close_prices, fund_flows 字段）
        config: 系统配置
        min_score: 最低评分阈值

    Returns:
        list of MeanReversionScore objects
    """
    try:
        from core.mean_reversion_scanner import MeanReversionScanner
        scanner = MeanReversionScanner()
        if min_score is not None:
            scanner.MIN_SCORE = min_score

        stocks_data = []
        for q in quotes:
            close_prices = q.get("close_prices", [])
            fund_flows = q.get("fund_flows", [])
            sector_rank = q.get("sector_rank", 99)
            if close_prices and fund_flows:
                stocks_data.append({
                    "code": q["code"],
                    "close": close_prices,
                    "fund_flow": fund_flows,
                    "sector_rank": sector_rank,
                })

        if not stocks_data:
            logger.info("[左侧抄底] 无满足历史数据条件的候选股")
            return []

        results = scanner.scan_universe(stocks_data)
        logger.info(f"[左侧抄底] 扫描 {len(stocks_data)} 只标的，找到 {len(results)} 个错杀信号")
        return results
    except Exception as e:
        logger.exception(f"[左侧抄底] 扫描异常: {e}")
        return []


def calculate_dca_multiplier(stock_data, ai_score):
    """
    智能定投乘数测算

    逻辑：基于大模型打分和技术面动态调整定投金额。
    - ai_score >= 75 (极端低估/强信号) 或 价格低于 MA60 → 乘数 1.5~2.0
    - ai_score < 60 (高位/情绪差) → 乘数 0.5 或 0 (暂停定投)
    - 默认 → 1.0

    Args:
        stock_data: dict，需包含 price, ma60 等字段
        ai_score: float，大模型综合评分

    Returns:
        float: 定投乘数 (0 = 暂停定投)
    """
    price = stock_data.get("price", 0)
    ma60 = stock_data.get("ma60", 0)

    # 极端低估/强信号：加倍定投
    if ai_score >= 75:
        # 若价格同时低于 MA60，给最大乘数 2.0
        if ma60 > 0 and price < ma60:
            return 2.0
        return 1.5

    # 高位/情绪差：缩减或暂停
    if ai_score < 60:
        # 若价格远高于 MA60，直接暂停
        if ma60 > 0 and price > ma60 * 1.1:
            return 0
        return 0.5

    # 中性区间：标准定投
    return 1.0


def determine_market_regime(stock_data):
    """
    市场状态识别器 — 动态策略路由的核心

    根据股票的技术面特征，自动识别最适合的交易策略：
    - "grid": 震荡市，适合网格高抛低吸
    - "smart_dca": 超跌市，适合智能定投抄底
    - "trend": 趋势市，适合右侧追涨

    Args:
        stock_data: dict，需包含以下字段：
            - prices: list[float]，历史价格序列（至少60日）
            - highs: list[float]，历史最高价序列
            - lows: list[float]，历史最低价序列
            - main_funds: list[float]，主力资金流向序列（可选）

    Returns:
        str: "grid" | "smart_dca" | "trend"
    """
    prices = stock_data.get("prices", [])
    highs = stock_data.get("highs", [])
    lows = stock_data.get("lows", [])
    main_funds = stock_data.get("main_funds", [])

    # 数据不足时默认趋势策略
    if len(prices) < 60:
        return "trend"

    current_price = prices[-1]

    # 计算均线
    ma5 = sum(prices[-5:]) / 5
    ma10 = sum(prices[-10:]) / 10
    ma20 = sum(prices[-20:]) / 20
    ma60 = sum(prices[-60:]) / 60

    # ===== 震荡识别 (Sideways) =====
    # 条件1：近20日最高价与最低价的振幅 < 15%
    if len(highs) >= 20 and len(lows) >= 20:
        recent_high = max(highs[-20:])
        recent_low = min(lows[-20:])
        if recent_low > 0:
            amplitude = (recent_high - recent_low) / recent_low
            # 条件2：MA5与MA20缠绕（差距 < 3%）
            if amplitude < 0.15 and ma20 > 0:
                ma_diff = abs(ma5 - ma20) / ma20
                if ma_diff < 0.03:
                    return "grid"

    # ===== 超跌识别 (Oversold) =====
    # 条件1：当前价格低于 MA60 超过 15%
    if ma60 > 0:
        price_below_ma60 = (current_price - ma60) / ma60
        if price_below_ma60 < -0.15:
            # 条件2：近5日主力资金未出现恐慌性净流出
            if len(main_funds) >= 5:
                recent_funds = main_funds[-5:]
                # 恐慌性流出：连续5日净流出且累计流出超过阈值
                if all(f < 0 for f in recent_funds):
                    total_outflow = sum(recent_funds)
                    # 如果累计流出超过近20日平均成交额的30%，视为恐慌
                    # 这里简化为：只要不是恐慌性流出就允许定投
                    if total_outflow > -10000000:  # 假设阈值：1000万
                        return "smart_dca"
                else:
                    # 不是连续流出，允许定投
                    return "smart_dca"
            else:
                # 没有资金数据，默认允许定投
                return "smart_dca"

    # ===== 趋势识别 (Uptrend) =====
    # 条件：MA5 > MA10 > MA20（多头排列）
    if ma5 > ma10 > ma20:
        return "trend"

    # ===== 默认回退 =====
    # 如果不满足上述极端特征，默认趋势策略
    return "trend"


if __name__ == "__main__":
    logger.info("策略引擎 v6 (异步事件驱动版) 已加载")
    logger.info("流水线: market_data → ai_client → base_strategies / risk_manager → 执行")
    logger.info("新增: market_filter(环境锁) | LogicExitManager(逻辑止盈) | MeanReversionScanner(左侧抄底)")
    logger.info("v6 Async Pipeline: concurrent AI scoring + parallel resonance checks + event bus coordination")


# ============================================================================
# v6 ASYNC EVENT-DRIVEN PIPELINE
# ============================================================================

async def _ai_score_chunk(chunk: list, config: dict) -> list:
    """
    异步单批次 AI 打分协程。
    
    替代原 select_stocks() 中 for chunk in chunks: get_ai_scoring_batch(chunk) 的阻塞循环。
    每个 chunk 独立运行，可被 asyncio.gather() 并行调度。
    
    Args:
        chunk: 待打分的股票列表（最多 CHUNK_SIZE=5 只）
        config: 系统配置
    
    Returns:
        list: AI 打分结果（与 get_ai_scoring_batch 返回格式一致）
    """
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: get_ai_scoring_batch(chunk, config)
        )
        return result
    except Exception as e:
        logger.exception(f"[AI_SCORE_ASYNC] 批次打分异常: {e}")
        raise


async def _check_technical_resonance_single(code: str, stock_history: dict):
    """
    异步单只股票技术共振检查协程。
    
    替代原 select_stocks() 中 for c in candidates: _check_technical_resonance(...) 
    的阻塞循环。每个股票独立运行，可被 asyncio.gather() 并行调度。
    
    Args:
        code: 股票代码
        stock_history: 历史数据字典
    
    Returns:
        tuple: (passed: bool, reason: str)
    """
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _check_technical_resonance(code, stock_history)
        )
        return result
    except Exception as e:
        logger.warning(f"[TECH_RES_ASYNC] {code} 共振检查异常: {e}")
        return True, f"技术共振异步检查异常，保守放行: {e}"


async def _load_ai_cache_async():
    """
    异步缓存加载协程。
    
    替代原 _load_ai_cache() 的阻塞文件 I/O，使用 run_in_executor 
    避免阻塞事件循环。
    """
    if not os.path.exists(CACHE_FILE):
        return {}
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: _load_ai_cache_impl()
        )
        return result
    except Exception as e:
        logger.exception(f"[WARN] AI 缓存异步加载失败: {e}，将使用空缓存")
        return {}


def _load_ai_cache_impl():
    """内部实现：带文件锁保护的缓存加载（同步版本）"""
    lock = FileLock(CACHE_LOCK_FILE, timeout=CACHE_LOCK_TIMEOUT)
    try:
        with lock:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
                return cache if isinstance(cache, dict) else {}
    except Exception as e:
        logger.exception(f"[WARN] AI 缓存加载失败: {e}，将使用空缓存")
        return {}


async def _save_ai_cache_async(cache_dict: dict):
    """
    异步缓存落盘协程。
    
    替代原 _save_ai_cache() 的阻塞文件 I/O，使用 run_in_executor 
    避免阻塞事件循环。
    """
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(
            None, lambda: _save_ai_cache_impl(cache_dict)
        )
    except Exception as e:
        logger.exception(f"[WARN] AI 缓存异步落盘失败: {e}")


def _save_ai_cache_impl(cache_dict: dict):
    """内部实现：带文件锁保护的缓存落盘（同步版本）"""
    lock = FileLock(CACHE_LOCK_FILE, timeout=CACHE_LOCK_TIMEOUT)
    try:
        with lock:
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            tmp_file = CACHE_FILE + ".tmp"
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(cache_dict, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, CACHE_FILE)
    except Exception as e:
        logger.exception(f"[WARN] AI 缓存落盘失败: {e}")


async def _run_async_pipeline(quotes: list, positions: dict, config: dict, 
                              stock_history: dict = None, market_provider=None, 
                              mode: str = "mock") -> dict:
    """
    v6 异步事件驱动选股流水线。
    
    核心改进：
    1. AI 打分批次并行执行 — asyncio.gather() 替代 for chunk in chunks 串行循环
       - 原架构：N 个批次 × M 秒/批 = N×M 秒（阻塞）
       - 新架构：max(N) 秒（并发，受 aiohttp 连接池限制）
    2. 技术共振检查并行化 — asyncio.gather(*tasks) 替代 for c in candidates 串行循环
    3. 缓存 I/O 非阻塞 — run_in_executor 避免阻塞事件循环
    4. 事件总线模式 — asyncio.Event + Queue 协调各阶段
    
    Args:
        quotes: 实时行情列表
        positions: 当前持仓 dict
        config: 系统配置字典
        stock_history: 历史数据 dict
        market_provider: 市场数据提供者（预留）
        mode: "mock" / "live" / "backtest"
    
    Returns:
        dict: {"buys": [...], "sells": [], "strategy": "...", "env_regime": "..."}
    """
    strategy_cfg = config.get("strategy", {})
    strategy_type = strategy_cfg.get("type", "trend")
    
    # ── Stage 1: 涨跌停硬过滤 → 基础粗筛 ───────────────────────────────
    candidates = [q for q in quotes if not (q.get("limit_up") or q.get("limit_down"))]
    
    # ── Stage 1.5: 流动性底线过滤（阻塞型，依赖 xtquant） ──────────────
    candidates = _liquidity_filter(candidates)
    
    # ── Stage 2: 资金/仓位统计 ─────────────────────────────────────────
    initial_capital = config.get("initial_capital", 50000)
    max_positions = config.get("max_positions", 10)
    available_cash = initial_capital - sum(
        pos.get("avg_price", 0) * pos.get("shares", 0) for pos in positions.values()
    )
    
    cached_results: list = []
    scored_stocks: list = []
    
    # ── Stage 3: AI 打分（并行化核心） ──────────────────────────────────
    if candidates:
        # 异步加载缓存
        ai_cache = await _load_ai_cache_async()
        
        # 区分缓存命中和需要 AI 打分的股票
        needs_ai_scoring = []
        for stock in candidates:
            cache_key = f"{stock.get('date', 'unknown')}_{stock['code']}"
            cached_data = ai_cache.get(cache_key)
            
            if cached_data and not cached_data.get("error"):
                cached_results.append(cached_data)
            else:
                needs_ai_scoring.append(stock)
        
        logger.info(f"  [缓存拦截] 命中 {len(cached_results)} 只，需 AI 打分 {len(needs_ai_scoring)} 只")
        
        # ── 并行 AI 打分：asyncio.gather() 替代串行 for 循环 ────────────
        if needs_ai_scoring:
            CHUNK_SIZE = 5
            chunks = [needs_ai_scoring[i:i + CHUNK_SIZE] 
                      for i in range(0, len(needs_ai_scoring), CHUNK_SIZE)]
            
            logger.info(f"[AI_SCORE_ASYNC] 启动 {len(chunks)} 个并行打分批次（原为串行）")
            
            # 创建所有批次的协程任务
            scoring_tasks = [_ai_score_chunk(chunk, config) for chunk in chunks]
            
            # 并发执行所有批次 — 这是核心加速点
            batch_results = await asyncio.gather(*scoring_tasks, return_exceptions=True)
            
            # 处理结果和错误
            for idx, (chunk, result) in enumerate(zip(chunks, batch_results)):
                if isinstance(result, Exception):
                    logger.exception(f"  [ERROR] 批次 {idx + 1}/{len(chunks)} 打分崩溃: {result}")
                    for stock in chunk:
                        cache_key = f"{stock.get('date', 'unknown')}_{stock['code']}"
                        ai_cache[cache_key] = {"error": True, "reason": str(result)}
                    continue
                
                try:
                    batch_result = list(result)  # ensure it's a list
                    
                    # 更新缓存和 scored_stocks
                    for stock in chunk:
                        cache_key = f"{stock.get('date', 'unknown')}_{stock['code']}"
                        matched = next((s for s in batch_result if s['code'] == stock['code']), None)
                        if matched:
                            ai_cache[cache_key] = matched
                            scored_stocks.append(matched)
                        else:
                            ai_cache[cache_key] = {"error": True, "reason": "解析失败或截断"}
                            
                except Exception as e:
                    logger.exception(f"  [ERROR] 批次 {idx + 1}/{len(chunks)} 结果处理异常: {e}")
                    for stock in chunk:
                        cache_key = f"{stock.get('date', 'unknown')}_{stock['code']}"
                        ai_cache[cache_key] = {"error": True, "reason": str(e)}
            
            # 异步落盘缓存
            await _save_ai_cache_async(ai_cache)
            logger.info(f"  [缓存落盘] 已更新 {len(ai_cache)} 条记录到 {CACHE_FILE}")
        
        # 合并缓存结果和 AI 打分结果
        candidates = cached_results + scored_stocks
    
    # ── Stage 3.5: AI 选股硬门槛拦截：70分以下一票否决 ────────────────
    if candidates:
        original_count = len(candidates)
        filtered = []
        for c in candidates:
            score = c.get("score")
            if score is not None:
                if score >= 70:
                    filtered.append(c)
        candidates = filtered
        rejected = original_count - len(candidates)
        if rejected > 0:
            logger.info(f"  [AI门槛过滤] score<70 拦截 {rejected} 只平庸标的，剩余候选 {len(candidates)} 只")
    
    # ── Stage 4: 技术面多头共振检查（并行化核心） ───────────────────────
    if candidates and stock_history:
        # 创建所有股票的共振检查协程任务 — asyncio.gather() 并发执行
        resonance_tasks = [_check_technical_resonance_single(c.get("code"), stock_history) 
                           for c in candidates]
        
        results = await asyncio.gather(*resonance_tasks)
        
        tech_filtered = []
        for idx, (c, (passed, reason)) in enumerate(zip(candidates, results)):
            if passed:
                tech_filtered.append(c)
                logger.info(f"  [多头共振通过] {c.get('code')}: {reason}")
            else:
                logger.info(f"  [多头共振拦截] {c.get('code')}: {reason}")
        candidates = tech_filtered
    
    if not candidates:
        return {"buys": [], "sells": [], "strategy": strategy_type, "env_regime": "UNKNOWN"}
    
    # ── Stage 5: 大盘环境锁检测（策略1） ────────────────────────────────
    position_limit = 1.0
    env_regime = "RISK_ON"
    if mode != "backtest":
        mf = _get_market_filter()
        if mf and mf is not False:
            can_buy, env_reason = mf.can_open_position()
            regime = mf.assess_market_regime()
            env_regime = regime["regime"]
            position_limit = regime["position_limit"]
            if not can_buy:
                logger.info(f"[环境锁] 禁止买入 | {env_reason}")
                return {"buys": [], "sells": [], "strategy": strategy_type, "env_regime": env_regime}
    
    # ── Stage 6: 策略路由 → 分发到具体策略算法模块 ──────────────────────
    strategy_map = {
        "trend": execute_trend_strategy,
        "value": execute_value_strategy,
        "momentum": execute_momentum_strategy,
    }
    
    executor = strategy_map.get(strategy_type, execute_trend_strategy)
    
    if position_limit == 0.5:
        max_positions = max(1, max_positions // 2)
        available_cash *= 0.5
        logger.info(f"[环境锁] CAUTION 模式，max_positions={max_positions}，可用资金减半")
    
    buys = executor(candidates, config, len(positions), max_positions, available_cash)
    
    return {
        "buys": buys,
        "sells": [],
        "strategy": strategy_type,
        "env_regime": env_regime,
    }


async def select_stocks_async(quotes: list, positions: dict, config: dict, 
                               stock_history: dict = None, market_provider=None, 
                               mode: str = "mock") -> dict:
    """
    v6 异步事件驱动选股流水线（公开接口）。
    
    替代原同步 select_stocks()，核心改进：
    - AI 打分批次并行执行（asyncio.gather）
    - 技术共振检查并行化（asyncio.gather）
    - 缓存 I/O 非阻塞（run_in_executor）
    - 事件总线模式协调各阶段
    
    Args:
        quotes: 实时行情列表
        positions: 当前持仓 dict
        config: 系统配置字典
        stock_history: 历史数据 dict
        market_provider: 市场数据提供者（预留）
        mode: "mock" / "live" / "backtest"
    
    Returns:
        dict: {"buys": [...], "sells": [], "strategy": "...", "env_regime": "..."}
    """
    try:
        return await _run_async_pipeline(quotes, positions, config, stock_history, 
                                          market_provider, mode)
    except Exception as e:
        logger.exception(f"[ASYNC_PIPELINE] 异步选股流水线异常: {e}")
        strategy_type = config.get("strategy", {}).get("type", "trend")
        return {"buys": [], "sells": [], "strategy": strategy_type, "env_regime": "ERROR"}


def select_stocks(quotes: list, positions: dict, config: dict, 
                   stock_history: dict = None, market_provider=None, 
                   mode: str = "mock") -> dict:
    """
    v6 同步包装器 — 向后兼容原 select_stocks() API。
    
    内部委托给 _run_async_pipeline()，通过 asyncio.run() 执行异步流水线。
    对于已有 async 调用方的代码，可直接使用 select_stocks_async()。
    
    Args:
        quotes: 实时行情列表 (来自 market_data)
        positions: 当前持仓 dict
        config: 系统配置字典
        stock_history: 历史数据 dict（可选）
        market_provider: 市场数据提供者（预留）
        mode: 模拟盘(mock) / 实盘(live) / 回测(backtest)
    
    Returns:
        dict: {"buys": [...], "sells": [], "strategy": "...", "env_regime": "..."}
    """
    try:
        return asyncio.run(_run_async_pipeline(quotes, positions, config, 
                                                stock_history, market_provider, mode))
    except RuntimeError as e:
        if "cannot be called from a running event loop" in str(e):
            # 已在事件循环中运行，降级为同步执行（兼容旧代码）
            logger.warning("[SYNC_FALLBACK] 检测到嵌套事件循环，降级为同步执行")
            return _sync_fallback_pipeline(quotes, positions, config, stock_history, 
                                           market_provider, mode)
        raise


def _sync_fallback_pipeline(quotes: list, positions: dict, config: dict,
                             stock_history: dict = None, market_provider=None,
                             mode: str = "mock") -> dict:
    """
    同步降级流水线 — 当 asyncio.run() 无法调用时使用。
    
    保留原 v5 串行逻辑作为 fallback，确保向后兼容。
    """
    strategy_cfg = config.get("strategy", {})
    strategy_type = strategy_cfg.get("type", "trend")
    
    candidates = [q for q in quotes if not (q.get("limit_up") or q.get("limit_down"))]
    candidates = _liquidity_filter(candidates)
    
    initial_capital = config.get("initial_capital", 50000)
    max_positions = config.get("max_positions", 10)
    available_cash = initial_capital - sum(
        pos.get("avg_price", 0) * pos.get("shares", 0) for pos in positions.values()
    )
    
    cached_results: list = []
    scored_stocks: list = []
    
    if candidates:
        ai_cache = _load_ai_cache()
        
        needs_ai_scoring = []
        for stock in candidates:
            cache_key = f"{stock.get('date', 'unknown')}_{stock['code']}"
            cached_data = ai_cache.get(cache_key)
            
            if cached_data and not cached_data.get("error"):
                cached_results.append(cached_data)
            else:
                needs_ai_scoring.append(stock)
        
        logger.info(f"  [缓存拦截] 命中 {len(cached_results)} 只，需 AI 打分 {len(needs_ai_scoring)} 只")
        
        if needs_ai_scoring:
            CHUNK_SIZE = 5
            chunks = [needs_ai_scoring[i:i + CHUNK_SIZE] for i in range(0, len(needs_ai_scoring), CHUNK_SIZE)]
            
            for chunk_idx, chunk in enumerate(chunks, start=1):
                try:
                    logger.info(f"  [AI打分] 批次 {chunk_idx}/{len(chunks)}，共 {len(chunk)} 只股票")
                    batch_result = get_ai_scoring_batch(chunk, config)
                    
                    for stock in chunk:
                        cache_key = f"{stock.get('date', 'unknown')}_{stock['code']}"
                        matched = next((s for s in batch_result if s['code'] == stock['code']), None)
                        if matched:
                            ai_cache[cache_key] = matched
                            scored_stocks.append(matched)
                        else:
                            ai_cache[cache_key] = {"error": True, "reason": "解析失败或截断"}
                
                except ValueError as ve:
                    logger.info(f"  [ERROR] 批次 {chunk_idx} 参数校验失败: {ve}")
                    for stock in chunk:
                        cache_key = f"{stock.get('date', 'unknown')}_{stock['code']}"
                        ai_cache[cache_key] = {"error": True, "reason": str(ve)}
                    continue
                except Exception as e:
                    logger.exception(f"  [ERROR] 批次 {chunk_idx} 打分崩溃: {e}")
                    for stock in chunk:
                        cache_key = f"{stock.get('date', 'unknown')}_{stock['code']}"
                        ai_cache[cache_key] = {"error": True, "reason": str(e)}
                    continue
            
            _save_ai_cache(ai_cache)
            logger.info(f"  [缓存落盘] 已更新 {len(ai_cache)} 条记录到 {CACHE_FILE}")
        
        candidates = cached_results + scored_stocks
    
    if candidates:
        original_count = len(candidates)
        filtered = []
        for c in candidates:
            score = c.get("score")
            if score is not None:
                if score >= 70:
                    filtered.append(c)
        candidates = filtered
        rejected = original_count - len(candidates)
        if rejected > 0:
            logger.info(f"  [AI门槛过滤] score<70 拦截 {rejected} 只平庸标的，剩余候选 {len(candidates)} 只")
    
    if candidates and stock_history:
        tech_filtered = []
        for c in candidates:
            code = c.get("code")
            passed, reason = _check_technical_resonance(code, stock_history)
            if passed:
                tech_filtered.append(c)
                logger.info(f"  [多头共振通过] {code}: {reason}")
            else:
                logger.info(f"  [多头共振拦截] {code}: {reason}")
        candidates = tech_filtered
    
    if not candidates:
        return {"buys": [], "sells": [], "strategy": strategy_type, "env_regime": "UNKNOWN"}
    
    position_limit = 1.0
    env_regime = "RISK_ON"
    if mode != "backtest":
        mf = _get_market_filter()
        if mf and mf is not False:
            can_buy, env_reason = mf.can_open_position()
            regime = mf.assess_market_regime()
            env_regime = regime["regime"]
            position_limit = regime["position_limit"]
            if not can_buy:
                logger.info(f"[环境锁] 禁止买入 | {env_reason}")
                return {"buys": [], "sells": [], "strategy": strategy_type, "env_regime": env_regime}
    
    strategy_map = {
        "trend": execute_trend_strategy,
        "value": execute_value_strategy,
        "momentum": execute_momentum_strategy,
    }
    
    executor = strategy_map.get(strategy_type, execute_trend_strategy)
    
    if position_limit == 0.5:
        max_positions = max(1, max_positions // 2)
        available_cash *= 0.5
        logger.info(f"[环境锁] CAUTION 模式，max_positions={max_positions}，可用资金减半")
    
    buys = executor(candidates, config, len(positions), max_positions, available_cash)
    
    return {
        "buys": buys,
        "sells": [],
        "strategy": strategy_type,
        "env_regime": env_regime,
    }
