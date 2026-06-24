"""
AI 决策通信模块 - 负责本地大模型交互、分批切片轮询与异常解析
注入宏观新闻 + 主力资金面 + 板块排名，供 Qwen 35B 深度打分
带 Evaluator 自我修复机制
"""
import json
import os
import requests

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080")
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, RetryError

# ================= 新增：引入跨目录的情绪嗅探器 =================
from feeds.sentiment_collector import build_ai_context_prompt
# 移除旧的从 advanced_factors 引入单薄新闻的逻辑
# ==========================================================

from feeds.memory_retriever import get_relevant_experience
from feeds.notifier import send_notification
from core.logger_config import logger


def _compress_context(long_text: str) -> str:
    """
    信息密度压缩：将冗长的宏观新闻与历史经验压缩为高密度核心信号。
    仅在文本长度 > 2000 字符时触发大模型压缩。
    """
    if len(long_text) <= 2000:
        return long_text

    logger.info(f"  [COMPRESS] 上下文长度 {len(long_text)} 字符，触发信息密度压缩...")
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个量化信息提炼机。请将以下冗长的宏观新闻与历史经验，"
                "压缩提炼为高密度的核心主线与交易信号。"
                "剔除废话，保留数据，严格控制在 800 字以内。"
            ),
        },
        {
            "role": "user",
            "content": long_text,
        },
    ]
    try:
        response = requests.post(
            f"{LLM_BASE_URL}/v1/chat/completions",
            json={
                "model": "local-model",
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 1000,
            },
            timeout=60,
        )
        if response.status_code == 200:
            compressed = response.json()["choices"][0]["message"]["content"].strip()
            logger.info(f"  [COMPRESS] 压缩完成: {len(long_text)} → {len(compressed)} 字符")
            return compressed
        else:
            logger.info(f"  [COMPRESS] 压缩失败(状态码 {response.status_code})，回退原始文本")
            return long_text
    except Exception as e:
        logger.exception(f"  [COMPRESS] 压缩异常: {e}，回退原始文本")
        return long_text


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def _get_ai_scoring_batch_inner(candidates, config=None):
    """内部函数：带重试逻辑的 AI 打分核心实现"""
    if not candidates:
        return candidates

    # 严格限流：从底层杜绝超长列表撑爆上下文
    if len(candidates) > 5:
        raise ValueError("批处理数量超过上限 5，请在调用方执行分批逻辑！")

    # ================= 核心重构：宏观情绪降维打击 =================
    try:
        # 1. 瞬间抓取当下的绝对宏观情绪文本（财联社电报 + 市场温度计）
        macro_context = build_ai_context_prompt()
        logger.info(f"  [DEBUG] 成功获取宏观情绪上下文 ({len(macro_context)} 字符)")
    except Exception as e:
        logger.error(f"  [ERROR] 获取宏观情绪失败: {e}，使用空降级")
        macro_context = "当前宏观数据缺失，请纯按技术面和资金面打分。"

    # ================= 新增：回测胜率数据注入 =================
    try:
        from core.strategy_engine import _get_market_filter
        mf = _get_market_filter()
        regime_key = "RISK_ON"
        if mf and mf is not False:
            regime = mf.assess_market_regime()
            regime_key = regime["regime"]
            
        stats_file = "./data_cache/backtest_stats.json"
        if os.path.exists(stats_file):
            with open(stats_file, "r", encoding="utf-8") as f:
                stats_data = json.load(f)
                regime_stats = stats_data.get("regimes", {}).get(regime_key, {})
                stats_str = json.dumps(regime_stats, ensure_ascii=False)
        else:
            stats_str = "无回测数据"
            
        macro_context += f"\n\n【历史回测概率参考】：当前大盘状态为 {regime_key}。该状态下系统策略历史胜率：{stats_str}。请严格结合历史胜率进行打分。"
        logger.info(f"  [DEBUG] 成功注入大盘状态 ({regime_key}) 的历史胜率数据。")
    except Exception as e:
        logger.error(f"  [ERROR] 注入回测胜率失败: {e}")
    # ========================================================

    # 2. 触发 RAG 记忆检索：用当前复杂的宏观情绪去知识库里找“相似的历史教训”
    retrieved_memories = get_relevant_experience(macro_context)

    # 3. 信息密度压缩：合并最新的大盘情绪与历史记忆
    combined_context = f"{macro_context}\n【历史经验】{retrieved_memories}"
    compressed_context = _compress_context(combined_context)
    # ============================================================

    # 将 main_fund (主力净流入) + sector_rank (板块排名) 加入组装字典
    stock_list_str = json.dumps(
        [
            {
                "code": c.get("code"),
                "name": c.get("name"),
                "sector": c.get("sector"),
                "change": c.get("change_pct"),
                "vol_ratio": c.get("volume_ratio"),
                "turnover": c.get("turnover_rate"),
                "macd": c.get("macd_trend", "无"),
                "ma5": c.get("ma5_trend", "无"),
                "main_fund_10k": c.get("main_fund", 0),  # 主力资金(万)
                "sector_rank": c.get("sector_rank", 99),  # 全市场板块排名
            }
            for c in candidates
        ],
        ensure_ascii=False,
    )

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个量化打分器。请直接输出结果，包含在 JSON 对象中，格式为：\n"
                '{"stock_picks": [{"code": "代码", "score": 分数, "reason": "理由"}]}\n\n'
                "你是专业的A股量化操盘手。请对股票打分(0-100)。\n"
                f"【浓缩上下文（当前宏观情绪+历史经验）】：\n{compressed_context}\n\n"
                "【选股铁律】：\n"
                "1. 顺势而为：请首先评估【浓缩上下文】中的市场温度（极端贪婪/恐慌冰点等）。若环境极度恶劣，请直接将所有多头策略的评分扣减 20 分！\n"
                "2. 技术面底线：MACD金叉或红柱放大，且站上5日线，给予高分。若死叉或跌破5日线直接淘汰！\n"
                "3. 资金面印证：若 main_fund_10k 为大额正数，强力加分；若流出坚决回避。\n"
                "4. 赛道共振(最重要)：注意 sector_rank（板块全市场排名）。若该股所在板块排名前 10，说明是当前绝对主线风口，请给予极高权重加分！若排名靠后(>50)，说明资金在流出该赛道，果断降分。\n"
                "5. 经验传承：必须参考上下文中的历史实战经验，避免重复踩坑。\n"
                "6. 理由字段内使用单引号(')代替双引号(\")，避免JSON解析错误。\n"
                "必须输出合法的 JSON 对象格式，严禁包含任何 Markdown 代码块或额外说明。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"请结合当前最新的市场情绪面、技术形态、主力资金与板块风口，对以下候选股票进行评分：\n{stock_list_str}\n\n"
                "请直接输出 JSON 对象。"
            ),
        },
    ]

    response = requests.post(
        f"{LLM_BASE_URL}/v1/chat/completions",
        json={
            "model": "local-model",
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 8192,
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )

    # 异常拦截：非 200 状态码抛异常触发重试
    if response.status_code != 200:
        raise RuntimeError(f"API 返回非 200 状态码: {response.status_code}")

    # 提取原始报文
    raw_json = response.json()
    message = raw_json["choices"][0]["message"]
    content = message.get("content", "")

    logger.info(f"[DEBUG] LLM 返回内容长度: {len(content)}")

    # 1. 过滤掉可能的 <think>...</think> 标签及其内容
    import re
    cleaned_text = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
    
    # 2. 提取 JSON 块
    json_match = re.search(r'```json\n(.*?)\n```', cleaned_text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # 兜底直接解析
        json_str = cleaned_text.strip()
        
    try:
        business_data = json.loads(json_str)
    except json.JSONDecodeError:
        raise ValueError(f"严格 JSON 解析失败，清理后报文: {json_str[:200]}")
    all_ai_score_map = {
        item.get("code"): item for item in business_data.get("stock_picks", [])
    }

    filtered_candidates = []
    for candidate in candidates:
        code = candidate.get("code")
        if code in all_ai_score_map:
            score = all_ai_score_map[code].get("score", 0)
            if score >= 60:
                candidate["score"] = score
                candidate["ai_reason"] = all_ai_score_map[code].get("reason", "")
                filtered_candidates.append(candidate)

    filtered_candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    return filtered_candidates


def get_ai_scoring_batch(candidates, config=None):
    """
    调用本地大模型对候选股票进行打分与深度过滤（带熔断降级）

    严格限流：单次调用 candidates 数量不得超过 5，
    超出则抛出 ValueError，由调用方负责分批。

    当 LLM 服务完全不可用时，自动降级为中性结果（score=50, HOLD），
    让策略引擎退化为"纯技术面决策"，绝不阻塞主进程。
    """
    try:
        return _get_ai_scoring_batch_inner(candidates, config)
    except RetryError as e:
        # 重试 3 次后彻底失败，触发降级兜底
        logger.info(f"[CRITICAL] LLM 服务熔断：重试 3 次后仍失败 - {e}")
        send_notification(
            "LLM 服务熔断警报",
            f"本地大模型服务连续 3 次调用失败，系统已自动降级为纯技术面决策模式。\n错误详情: {e}"
        )
        # 返回中性占位结果：所有股票 score=50, reason="HOLD"
        for candidate in candidates:
            candidate["score"] = 50
            candidate["ai_reason"] = "LLM 服务不可用，降级为中性评分"
        return candidates
    except Exception as e:
        # 捕获其他未预期异常（如 ValueError 限流错误）
        logger.exception(f"[ERROR] AI 打分异常: {e}")
        # 限流错误不降级，直接抛出
        if "批处理数量超过上限" in str(e):
            raise
        # 其他异常降级
        send_notification(
            "LLM 服务异常警报",
            f"AI 打分模块发生未预期异常，系统已自动降级。\n错误详情: {e}"
        )
        for candidate in candidates:
            candidate["score"] = 50
            candidate["ai_reason"] = "LLM 服务异常，降级为中性评分"
        return candidates