"""
多智能体反思管线 - Generator 生成交易经验 + Evaluator 评分 + ChromaDB 向量存储
每日 20:00 自动执行，将高质量经验沉淀到向量库供后续检索
"""
import json
import os
import time

import schedule
from openai import OpenAI

from core.logger_config import logger

# 导入统一的 ChromaDB 管理器
from core.chroma_manager import add_trading_experience

# ==================== 客户端初始化 ====================

# Generator: 本地 Hermes 模型
generator_client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",
    api_key="not-needed",
)

# Evaluator: 远程 DeepSeek (懒加载，调用时校验)
evaluator_client = None
REMOTE_MODEL = "deepseek-chat"


def _get_evaluator_client():
    """懒加载 Evaluator 客户端，调用时校验 API Key"""
    global evaluator_client
    if evaluator_client is not None:
        return evaluator_client

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY 未配置。请在 .env 文件中设置后重试。"
        )

    evaluator_client = OpenAI(
        base_url="https://api.deepseek.com/v1",
        api_key=api_key,
    )
    return evaluator_client


# ==================== 核心大模型请求函数 ====================

def call_generator(news_text: str, market_summary: str) -> str:
    """
    Generator 节点：输入新闻和盘面，输出不超过150字的交易规律总结
    """
    response = generator_client.chat.completions.create(
        model="local-model",
        messages=[
            {
                "role": "system",
                "content": (
                    "你是一位资深A股操盘手。根据提供的当日新闻和盘面异动，"
                    "总结出一条精炼的交易经验或规律。"
                    "要求：不超过150字，直击要害，具备可操作性。"
                    "你必须且只能输出合法的 JSON 对象，格式为：{\"experience\": \"经验内容\"}。"
                    "严禁输出任何 Markdown 代码块符号（如 ```json）或任何额外的说明性文字。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"【今日重大新闻】\n{news_text}\n\n"
                    f"【盘面异动概况】\n{market_summary}\n\n"
                    "请总结交易经验（不超过150字）："
                ),
            },
        ],
        temperature=0.3,
        max_tokens=300,
        response_format={"type": "json_object"},
        timeout=60,
    )
    raw = response.choices[0].message.content.strip()
    data = json.loads(raw)
    return data.get("experience", "").strip()


def call_evaluator(trading_experience: str) -> dict:
    """
    Evaluator 节点：对交易经验进行逻辑严密性评分 (1-10)
    必须返回 JSON: {"score": 8, "reason": "..."}
    """
    client = _get_evaluator_client()
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {
                "role": "system",
                "content": (
                    "你是一位严格的量化交易评审专家。请对以下交易经验进行逻辑严密性评分。\n"
                    "评分范围：1-10 分。\n"
                    "你必须且只能返回如下 JSON 格式，不要输出任何其他内容：\n"
                    '{"score": 8, "reason": "评分理由"}\n'
                    "必须输出合法的 JSON 对象格式，严禁包含任何 Markdown 代码块或额外说明。"
                ),
            },
            {
                "role": "user",
                "content": f"请评估以下交易经验：\n{trading_experience}",
            },
        ],
        temperature=0.1,
        max_tokens=200,
        response_format={"type": "json_object"},
        timeout=60,
    )
    raw = response.choices[0].message.content.strip()
    return json.loads(raw)


# ==================== 主流程 ====================

def run_daily_reflexion():
    """
    每日反思主流程：
    1. 模拟拉取当日异动股与重大新闻
    2. 调用 Generator 生成交易经验
    3. 调用 Evaluator 评分
    4. score >= 8 时存入 ChromaDB
    """
    logger.info("=" * 60)
    logger.info("[REFLEXION] 开始每日反思流程...")

    # --- 步骤1：模拟拉取当日数据 ---
    news_text = (
        "央行宣布定向降准0.5个百分点，释放长期资金约5000亿元；"
        "国务院常务会议部署加快发展新质生产力；"
        "北向资金今日净买入超80亿元。"
    )
    market_summary = (
        "沪指涨1.2%站上3300点，成交额突破1.2万亿；"
        "半导体板块集体爆发，多只个股涨停；"
        "新能源赛道回调，锂电池板块跌幅居前；"
        "异动股：中芯国际涨停、北方华创涨8%、宁德时代跌3%。"
    )
    logger.info(f"[REFLEXION] 新闻: {news_text[:40]}...")
    logger.info(f"[REFLEXION] 盘面: {market_summary[:40]}...")

    # --- 步骤2：Generator 生成交易经验 ---
    try:
        experience = call_generator(news_text, market_summary)
        logger.info(f"[REFLEXION] Generator 输出: {experience}")
    except Exception as e:
        logger.exception(f"[REFLEXION] Generator 调用失败: {e}")
        return

    # --- 步骤3：Evaluator 评分 ---
    try:
        result = call_evaluator(experience)
        score = result.get("score", 0)
        reason = result.get("reason", "")
        logger.info(f"[REFLEXION] Evaluator 评分: {score}/10, 理由: {reason}")
    except Exception as e:
        logger.exception(f"[REFLEXION] Evaluator 调用失败: {e}")
        return

    # --- 步骤4：达标则存入 ChromaDB ---
    if score >= 8:
        # P2-4 修复：使用基于日期的确定性ID，避免同一天重复写入
        date_str = time.strftime("%Y%m%d")
        doc_id = f"exp_{date_str}_{hash(experience) % 10000:04d}"
        try:
            # 关键修复：将 experience（反思经验）作为文档主体存入向量库，
            # 而非 news_text。这样后续 RAG 检索时，相似度计算匹配的是经验本身。
            add_trading_experience({
                "document": experience,
                "metadata": {
                    "trading_experience": experience,
                    "score": score,
                    "reason": reason,
                    "date": date_str,
                    "news_context": news_text[:200],
                }
            }, doc_id)
            logger.info(f"[REFLEXION] 经验已写入 ChromaDB (id={doc_id})")
            logger.info(f"[REFLEXION] 写入文档: {experience[:80]}...")
        except Exception as e:
            logger.exception(f"[REFLEXION] 写入 ChromaDB 失败: {e}")
    else:
        logger.info(f"[REFLEXION] 评分 {score} < 8，经验未达标，已丢弃")

    logger.info("[REFLEXION] 每日反思流程结束")
    logger.info("=" * 60)

    # 返回反思结果，供外部调用方（如 auto_tuner）使用
    return {
        "experience": experience,
        "score": score,
        "reason": reason,
        "stored": score >= 8,
        "doc_id": doc_id if score >= 8 else None,
    }


# ==================== 定时调度 ====================

if __name__ == "__main__":
    run_daily_reflexion()

#if __name__ == "__main__":
    #schedule.every().day.at("20:00").do(run_daily_reflexion)
  #  logger.info("[SCHEDULER] 反思管线已启动，每日 20:00 自动执行")

    # 首次启动时立即执行一次（可选）
    # run_daily_reflexion()

    #while True:
      #  schedule.run_pending()
        #time.sleep(30)
