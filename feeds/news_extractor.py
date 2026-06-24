"""
资讯结构化降维提纯模块
职责：
1. 接入新浪财经 7x24 小时快讯 API，获取近 24 小时金融资讯
2. 调用本地大模型将长文本压缩为极致精简的 JSON 情绪因子
3. 输出格式：{"macro_sentiment": 1, "hot_sectors": ["半导体"], "risk_warnings": ["sh600519"]}
"""
import json
import os
import re
import requests

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080")
from datetime import datetime, timedelta

from core.logger_config import logger


def fetch_sina_7x24_news(hours=24, limit=50):
    """
    从新浪财经 7x24 小时快讯 API 获取最新资讯

    Args:
        hours: 获取多少小时内的资讯（默认 24）
        limit: 最多获取多少条（默认 50）

    Returns:
        list of dict: [{"time": "2024-01-01 12:00:00", "content": "..."}, ...]
    """
    url = "https://zhibo.sina.com.cn/api/zhibo/feed"
    params = {
        "page": 1,
        "page_size": limit,
        "zhibo_id": 152,  # 7x24 快讯频道 ID
        "tag_id": 0,
        "type": 0,
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("result", {}).get("status", {}).get("code") != 0:
            logger.info("[WARN] 新浪 7x24 API 返回异常")
            return []

        feed_list = data.get("result", {}).get("data", {}).get("feed", {}).get("list", [])

        # 过滤时间范围
        cutoff_time = datetime.now() - timedelta(hours=hours)
        filtered_news = []

        for item in feed_list:
            create_time = item.get("create_time", "")
            try:
                news_time = datetime.strptime(create_time, "%Y-%m-%d %H:%M:%S")
                if news_time >= cutoff_time:
                    content = item.get("rich_text", "") or item.get("text", "")
                    # 去除 HTML 标签
                    content = re.sub(r"<[^>]+>", "", content)
                    content = content.strip()
                    if content:
                        filtered_news.append({
                            "time": create_time,
                            "content": content,
                        })
            except Exception as e:
                logger.exception(f"[WARN] 解析资讯时间失败: {e}")
                continue

        logger.info(f"[NEWS] 获取到 {len(filtered_news)} 条近 {hours} 小时资讯")
        return filtered_news

    except Exception as e:
        logger.exception(f"[ERROR] 获取新浪 7x24 资讯失败: {e}")
        return []


def extract_sentiment_with_llm(news_list):
    """
    调用本地大模型提取情绪因子

    Args:
        news_list: list of dict, 每条包含 time 和 content

    Returns:
        dict: {"macro_sentiment": int, "hot_sectors": list, "risk_warnings": list}
    """
    if not news_list:
        return {
            "macro_sentiment": 0,
            "hot_sectors": [],
            "risk_warnings": [],
        }

    # 拼接资讯文本（限制长度）
    news_text = "\n".join([
        f"[{n['time']}] {n['content'][:200]}"  # 每条限制 200 字
        for n in news_list[:30]  # 最多 30 条
    ])

    # 如果文本过长，截断
    if len(news_text) > 6000:
        news_text = news_text[:6000] + "\n...(已截断)"

    messages = [
        {
            "role": "system",
            "content": (
                "你是一个金融情绪分析器。请从以下资讯中提取结构化情绪因子。\n"
                "输出格式必须为纯 JSON，不要包含任何 Markdown 标记或额外文本：\n"
                '{"macro_sentiment": 整数, "hot_sectors": ["板块1", "板块2"], "risk_warnings": ["股票代码"]}\n\n'
                "字段说明：\n"
                "- macro_sentiment: 宏观情绪，-2(极度悲观) 到 +2(极度乐观)，0 为中性\n"
                "- hot_sectors: 近期热门板块（最多 3 个）\n"
                "- risk_warnings: 需要警惕的股票代码（如利空、减持等）\n"
            ),
        },
        {
            "role": "user",
            "content": f"请分析以下资讯：\n{news_text}",
        },
    ]

    try:
        response = requests.post(
            f"{LLM_BASE_URL}/v1/chat/completions",
            json={
                "model": "local-model",
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 500,
            },
            timeout=120,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()

        # 提取 JSON
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            json_str = content[start:end + 1]
            result = json.loads(json_str)

            # 校验并规范化
            macro_sentiment = result.get("macro_sentiment", 0)
            if not isinstance(macro_sentiment, int) or macro_sentiment < -2 or macro_sentiment > 2:
                macro_sentiment = 0

            hot_sectors = result.get("hot_sectors", [])
            if not isinstance(hot_sectors, list):
                hot_sectors = []
            hot_sectors = hot_sectors[:3]  # 最多 3 个

            risk_warnings = result.get("risk_warnings", [])
            if not isinstance(risk_warnings, list):
                risk_warnings = []

            return {
                "macro_sentiment": macro_sentiment,
                "hot_sectors": hot_sectors,
                "risk_warnings": risk_warnings,
            }

    except json.JSONDecodeError as e:
        logger.error(f"[WARN] 情绪因子 JSON 解析失败: {e}")
    except Exception as e:
        logger.exception(f"[WARN] 情绪因子提取异常: {e}")

    # 失败时返回默认值
    return {
        "macro_sentiment": 0,
        "hot_sectors": [],
        "risk_warnings": [],
    }


def get_news_sentiment(hours=24):
    """
    获取并提取近 24 小时资讯情绪因子（主入口）

    Args:
        hours: 获取多少小时内的资讯

    Returns:
        dict: {"macro_sentiment": int, "hot_sectors": list, "risk_warnings": list}
    """
    logger.info("[NEWS] 开始提取资讯情绪因子...")
    news_list = fetch_sina_7x24_news(hours=hours)
    sentiment = extract_sentiment_with_llm(news_list)
    logger.info(f"[NEWS] 情绪因子: {sentiment}")
    return sentiment


if __name__ == "__main__":
    # 测试
    result = get_news_sentiment(hours=24)
    logger.info(json.dumps(result, ensure_ascii=False, indent=2))


# ============================================================
# Dashboard 深度分析 - 真实个股舆情数据管道
# ============================================================

def fetch_stock_news(stock_code: str, limit: int = 5) -> list:
    """
    使用 AkShare 东方财富源获取个股最新新闻。
    """
    try:
        import akshare as ak
        raw_code = stock_code.strip().lower()
        if raw_code.startswith(("sh", "sz", "bj")):
            raw_code = raw_code[2:]
            
        news_df = ak.stock_news_em(symbol=raw_code)
        if news_df.empty:
            return []
            
        records = news_df[['新闻标题', '新闻内容', '发布时间']].head(limit).to_dict('records')
        news_items = []
        for r in records:
            news_items.append({
                "title": r["新闻标题"],
                "publish_time": r["发布时间"],
                "sentiment": "中性"  # 默认中性，可接 LLM 情感分析
            })
        return news_items
    except Exception as e:
        from core.logger_config import logger
        logger.error(f"资讯获取失败 {stock_code}: {e}")
        return []

import streamlit as st

    """
    rss_urls = [
        "https://rss.sina.com.cn/roll/finance/hot_roll.xml"
    ]
    all_entries = []
    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                pub_date = entry.get("published", "")
                
                try:
                    from dateutil import parser
                    dt = parser.parse(pub_date)
                    pub_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    from datetime import datetime
                    pub_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                all_entries.append({
                    "title": title,
                    "summary": summary,
                    "publish_time": pub_time
                })
        except Exception:
            pass
            
    # 按时间降序排列
    all_entries.sort(key=lambda x: x["publish_time"], reverse=True)
    return all_entries
