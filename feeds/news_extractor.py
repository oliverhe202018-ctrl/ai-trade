"""
资讯结构化降维提纯模块 (重构版)
职责：
1. 三层 Waterfall 获取资讯: 东方财富 AkShare -> 东方财富 Kuaixun HTTP -> 新浪 7x24
2. 二层降级处理: LLM 提取 -> 规则引擎提取
3. 输出格式：{"macro_sentiment": 1, "hot_sectors": ["半导体"], "risk_warnings": ["sh600519"], "_source": "...", "_news_count": ...}
"""
import json
import os
import re
import requests
from datetime import datetime, timedelta
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080")

# 规则引擎配置
_BULLISH_KEYWORDS = ["大涨","涨停","新高","利好","增持","回购","业绩超预期",
                     "扭亏","并购","获批","中标","突破","启动","上调","获得订单"]
_BEARISH_KEYWORDS = ["大跌","跌停","利空","减持","质押","违规","立案",
                     "亏损","暴雷","退市","业绩下滑","降级","下调","债务违约"]

_HOT_SECTOR_MAP = {
    "半导体":    ["芯片","半导体","集成电路","晶圆","光刻"],
    "新能源":    ["光伏","储能","锂电","新能源","碳中和","风电"],
    "AI人工智能": ["人工智能","大模型","算力","AI","ChatGPT","大语言模型"],
    "医药生物":  ["医药","生物","创新药","医疗器械","CXO","疫苗"],
    "军工":      ["军工","国防","导弹","航天","歼","战机"],
    "消费":      ["白酒","食品饮料","消费复苏","零售"],
    "地产":      ["房地产","地产","楼市","限购","保交楼"],
}

def fetch_layer1_em_akshare(hours=24):
    """Layer-1: 东方财富 AkShare stock_info_global_em"""
    try:
        import akshare as ak
        logger.info("[NEWS-L1] 尝试通过 AkShare (stock_info_global_em) 获取数据")
        news_df = ak.stock_info_global_em()
        if news_df is None or news_df.empty:
            logger.warning("[NEWS-L1] AkShare 返回数据为空")
            return None
        
        filtered_news = []
        cutoff_time = datetime.now() - timedelta(hours=hours)
        
        columns = list(news_df.columns)
        time_col = next((c for c in columns if '时间' in c or 'time' in c.lower() or 'time' in c), None)
        content_col = next((c for c in columns if '摘要' in c or '内容' in c or '标题' in c or 'title' in c.lower()), None)
        
        if not time_col or not content_col:
            # Fallback for known akshare columns if matching fails due to encoding
            # Typically: ['标题', '摘要', '发布时间', '链接']
            if len(columns) >= 3:
                time_col = columns[2]
                content_col = columns[1]
            else:
                logger.warning(f"[NEWS-L1] 无法识别数据列: {columns}")
                return None
        
        for idx, row in news_df.iterrows():
            try:
                create_time = str(row[time_col])
                content = str(row[content_col])
                
                # Check time valid
                if ":" not in create_time:
                    pass
                
                content = re.sub(r"<[^>]+>", "", content).strip()
                if content:
                    filtered_news.append({
                        "time": create_time,
                        "content": content[:300]
                    })
            except Exception:
                pass
        
        logger.info(f"[NEWS-L1] 获取到 {len(filtered_news)} 条数据")
        return filtered_news if filtered_news else None
    except Exception as e:
        logger.exception(f"[NEWS-L1] AkShare 获取失败: {e}")
        return None

def fetch_layer2_em_http(hours=24):
    """Layer-2: 东方财富 Kuaixun HTTP 直连"""
    url = "https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_1_.html"
    try:
        logger.info("[NEWS-L2] 尝试通过东财快讯 HTTP 直连获取数据")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        text = response.text
        
        # 解析 var ajaxResult={...}
        match = re.search(r"var\s+ajaxResult\s*=\s*(\{.*?\});", text, re.DOTALL)
        if not match:
            # 兼容没有 var ajaxResult= 的纯 JSON 返回
            try:
                data = response.json()
            except:
                logger.warning("[NEWS-L2] 无法解析 JS 变量 ajaxResult")
                return None
        else:
            json_str = match.group(1)
            data = json.loads(json_str)
            
        list_data = data.get("list", [])
        if not list_data:
            return None
            
        filtered_news = []
        for item in list_data:
            create_time = item.get("showtime", "")
            content = item.get("title", "") or item.get("digest", "")
            content = re.sub(r"<[^>]+>", "", content).strip()
            if content:
                filtered_news.append({
                    "time": create_time,
                    "content": content[:300]
                })
        
        logger.info(f"[NEWS-L2] 获取到 {len(filtered_news)} 条数据")
        return filtered_news if filtered_news else None
    except Exception as e:
        logger.exception(f"[NEWS-L2] 东财 HTTP 获取失败: {e}")
        return None

def fetch_layer3_sina_7x24(hours=24, limit_pages=5):
    """Layer-3: 新浪 7x24，支持翻页"""
    url = "https://zhibo.sina.com.cn/api/zhibo/feed"
    filtered_news = []
    cutoff_time = datetime.now() - timedelta(hours=hours)
    
    logger.info("[NEWS-L3] 尝试通过新浪 7x24 获取数据")
    for page in range(1, limit_pages + 1):
        params = {
            "page": page,
            "page_size": 50,
            "zhibo_id": 152,
            "tag_id": 0,
            "type": 0,
        }
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get("result", {}).get("status", {}).get("code") != 0:
                logger.warning(f"[NEWS-L3] API 返回异常状态码, page={page}")
                break
                
            feed_list = data.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
            if not feed_list:
                break
                
            for item in feed_list:
                create_time = item.get("create_time", "")
                try:
                    news_time = datetime.strptime(create_time, "%Y-%m-%d %H:%M:%S")
                    if news_time >= cutoff_time:
                        content = item.get("rich_text", "") or item.get("text", "")
                        content = re.sub(r"<[^>]+>", "", content).strip()
                        # 过滤长度 < 10 的噪声条目
                        if content and len(content) >= 10:
                            filtered_news.append({
                                "time": create_time,
                                "content": content
                            })
                except Exception:
                    continue
        except Exception as e:
            logger.exception(f"[NEWS-L3] 新浪 7x24 获取失败, page={page}: {e}")
            break
            
    logger.info(f"[NEWS-L3] 获取到 {len(filtered_news)} 条数据")
    return filtered_news if filtered_news else None


def fetch_news_waterfall(hours=24):
    """三级数据源瀑布流获取"""
    news_list = fetch_layer1_em_akshare(hours=hours)
    if news_list is not None:
        return news_list, "EM_AKSHARE"
        
    news_list = fetch_layer2_em_http(hours=hours)
    if news_list is not None:
        return news_list, "EM_HTTP"
        
    news_list = fetch_layer3_sina_7x24(hours=hours)
    if news_list is not None:
        return news_list, "SINA_7X24"
        
    return [], "ALL_FAILED"

def _check_llm_alive(timeout=5):
    """前置探活"""
    try:
        res = requests.get(f"{LLM_BASE_URL}/health", timeout=timeout)
        if res.status_code == 200:
            return True
    except:
        pass
        
    try:
        res = requests.get(f"{LLM_BASE_URL}/v1/models", timeout=timeout)
        if res.status_code == 200:
            return True
    except:
        pass
        
    return False

def _rule_engine_fallback(news_list):
    """规则引擎降级"""
    bull_score = 0
    bear_score = 0
    sector_counts = {k: 0 for k in _HOT_SECTOR_MAP}
    risk_warnings = set()
    
    for item in news_list:
        content = item.get("content", "")
        # 情绪得分
        for kw in _BULLISH_KEYWORDS:
            if kw in content:
                bull_score += 1
        for kw in _BEARISH_KEYWORDS:
            if kw in content:
                bear_score += 1
                
        # 板块热度
        for sector, kws in _HOT_SECTOR_MAP.items():
            for kw in kws:
                if kw in content:
                    sector_counts[sector] += 1
                    
        # 简易风险提取 (股票代码)
        matches = re.findall(r"(sh60\d{4}|sz00\d{4}|sz30\d{4})", content)
        for m in matches:
            risk_warnings.add(m)
            
    net = bull_score - bear_score
    if net >= 10:
        macro_sentiment = 2
    elif net >= 4:
        macro_sentiment = 1
    elif net <= -10:
        macro_sentiment = -2
    elif net <= -4:
        macro_sentiment = -1
    else:
        macro_sentiment = 0
        
    sorted_sectors = sorted(sector_counts.items(), key=lambda x: x[1], reverse=True)
    hot_sectors = [s[0] for s in sorted_sectors if s[1] > 0][:3]
    
    return {
        "macro_sentiment": macro_sentiment,
        "hot_sectors": hot_sectors,
        "risk_warnings": list(risk_warnings),
        "_source": "rule_engine"
    }

def extract_sentiment_with_llm(news_list):
    """调用 LLM 提取情绪，失败则返回 None"""
    news_text = "\n".join([
        f"[{n['time']}] {n['content'][:300]}"
        for n in news_list[:30]
    ])
    
    if len(news_text) > 6000:
        news_text = news_text[:6000]

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
                "- risk_warnings: 需要警惕的股票代码（如利空、减持等，包含sh/sz前缀）\n"
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
            timeout=30,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        
        block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if block_match:
            json_str = block_match.group(1)
        else:
            start, end = content.find("{"), content.rfind("}")
            if start != -1 and end != -1:
                json_str = content[start:end + 1]
            else:
                return None, "LLM_PARSE_FAILED"

        result = json.loads(json_str)

        macro_sentiment = result.get("macro_sentiment", 0)
        if not isinstance(macro_sentiment, int) or macro_sentiment < -2 or macro_sentiment > 2:
            macro_sentiment = 0

        hot_sectors = result.get("hot_sectors", [])
        if not isinstance(hot_sectors, list):
            hot_sectors = []
        hot_sectors = hot_sectors[:3]

        risk_warnings = result.get("risk_warnings", [])
        if not isinstance(risk_warnings, list):
            risk_warnings = []

        return {
            "macro_sentiment": macro_sentiment,
            "hot_sectors": hot_sectors,
            "risk_warnings": risk_warnings,
        }, None

    except json.JSONDecodeError as e:
        logger.error(f"[WARN] 情绪因子 JSON 解析失败: {e}")
        return None, "LLM_PARSE_FAILED"
    except Exception as e:
        logger.exception(f"[WARN] 情绪因子提取异常: {e}")
        return None, "LLM_FAILED"


def get_news_sentiment(hours=24):
    """主入口"""
    news_list, source = fetch_news_waterfall(hours)
    
    if source == "ALL_FAILED":
        return {
            "macro_sentiment": 0,
            "hot_sectors": [],
            "risk_warnings": [],
            "_source": "ALL_FAILED",
            "_news_count": 0
        }
        
    news_count = len(news_list)
    
    llm_online = _check_llm_alive(timeout=5)
    
    if llm_online:
        sentiment, llm_err = extract_sentiment_with_llm(news_list)
        if sentiment:
            sentiment["_source"] = source
            sentiment["_news_count"] = news_count
            return sentiment
        else:
            logger.warning(f"[NEWS] LLM 提取异常({llm_err})，启用规则引擎降级")
            fallback_res = _rule_engine_fallback(news_list)
            fallback_res["_source"] = llm_err
            fallback_res["_news_count"] = news_count
            return fallback_res
    else:
        logger.warning("[NEWS] LLM 离线，启用规则引擎降级")
        fallback_res = _rule_engine_fallback(news_list)
        fallback_res["_news_count"] = news_count
        return fallback_res


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
        if news_df is None or news_df.empty:
            return []
            
        records = news_df[['新闻标题', '新闻内容', '发布时间']].head(limit).to_dict('records')
        news_items = []
        for r in records:
            news_items.append({
                "title": r["新闻标题"],
                "publish_time": r["发布时间"],
                "sentiment": "中性"
            })
        return news_items
    except Exception as e:
        from core.logger_config import logger
        logger.error(f"资讯获取失败 {stock_code}: {e}")
        return []

if __name__ == "__main__":
    import json
    result = get_news_sentiment(hours=24)
    print(json.dumps(result, ensure_ascii=False, indent=2))
