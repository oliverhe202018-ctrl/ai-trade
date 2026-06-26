"""
feeds/eastmoney_news_provider.py — 东方财富个股新闻 Provider

Tier: 1 (API — akshare 封装的 EastMoney API)
覆盖: 按股票代码拉取个股新闻，每只股票通常 20-50 条
速率: ~1 req/s (akshare 内部限速)
"""

import os
import sys
import json
import hashlib
import time
import traceback
from datetime import datetime
from typing import List, Dict, Any, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from feeds.base_news_provider import BaseNewsProvider
from core.logger_config import logger


class EastMoneyNewsProvider(BaseNewsProvider):
    """
    东方财富个股新闻 Provider (via akshare)

    API: akshare.stock_news_em(symbol=code)
    返回: 该股票最近的相关新闻 (通常 20-50 条)
    限制: 单次只查询一只股票，批量采集需要遍历股票列表
    """

    def __init__(self, max_pages: int = 5, recent_hours: Optional[int] = None,
                 categories: Optional[List[str]] = None):
        super().__init__("eastmoney")
        self.timeout = 15
        self._last_call_time = 0
        self._rate_limit = 1.0  # 1 second between calls
        self.max_pages = max_pages
        self.recent_hours = recent_hours
        self.categories = categories or ["stock", "announcement", "report"]

    def _rate_limit_wait(self):
        """遵守速率限制"""
        elapsed = time.time() - self._last_call_time
        if elapsed < self._rate_limit:
            time.sleep(self._rate_limit - elapsed)
        self._last_call_time = time.time()

    def fetch_latest(self, limit: int = 50, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        使用东方财富搜索 API 批量拉取个股新闻。

        API: https://search-api-web.eastmoney.com/search/jsonp
        限制: ~1 req/s, 每只股票返回 10 条新闻
        """
        import requests
        import re as _re

        if symbols is None:
            symbols = [
                "000001", "000002", "000333", "000651", "000858",
                "002415", "002475", "002594", "300059", "300750",
                "600000", "600036", "600276", "600519", "600900",
                "601012", "601166", "601318", "601398", "601899",
                "688981", "002230", "300124", "688111", "603259",
            ]

        all_news = []
        success_count = 0
        error_count = 0

        for i, code in enumerate(symbols):
            self._rate_limit_wait()
            clean_code = code.replace("sh", "").replace("sz", "").replace(".SH", "").replace(".SZ", "")
            try:
                url = "https://search-api-web.eastmoney.com/search/jsonp"
                inner = {
                    "uid": "",
                    "keyword": clean_code,
                    "type": ["cmsArticleWebOld"],
                    "client": "web",
                    "clientType": "web",
                    "clientVersion": "curr",
                    "param": {
                        "cmsArticleWebOld": {
                            "searchScope": "default",
                            "sort": "default",
                            "pageIndex": 1,
                            "pageSize": min(limit, 10),
                            "preTag": "<em>",
                            "postTag": "</em>",
                        }
                    },
                }
                params = {
                    "cb": "jQuery123456",
                    "param": json.dumps(inner, ensure_ascii=False),
                    "_": str(int(time.time() * 1000)),
                }
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": f"https://so.eastmoney.com/news/s?keyword={clean_code}",
                }

                response = requests.get(url, params=params, headers=headers, timeout=self.timeout)
                response.raise_for_status()

                text = response.text
                match = _re.search(r"jQuery123456\((.*)\)", text)
                if not match:
                    continue

                data = json.loads(match.group(1))
                articles = data.get("result", {}).get("cmsArticleWebOld", [])

                for article in articles:
                    title = _re.sub(r"</?em>", "", article.get("title", "")).strip()
                    content = _re.sub(r"</?em>", "", article.get("content", "")).strip()
                    content = content.replace("\u3000", "").replace("\r\n", " ")

                    if not title:
                        continue

                    if clean_code.startswith(("6", "9")):
                        std_code = f"sh{clean_code}"
                    else:
                        std_code = f"sz{clean_code}"

                    all_news.append({
                        "symbol_code": std_code,
                        "symbol_name": "",
                        "title": title,
                        "content": content[:2000],
                        "publish_time_raw": article.get("date", ""),
                        "article_url": article.get("url", ""),
                        "source_provider": "eastmoney",
                    })

                success_count += 1

            except Exception as e:
                error_count += 1
                logger.warning(f"[EastMoneyNewsProvider] {clean_code} 拉取失败: {e}")

        if success_count > 0:
            self._mark_success(
                last_event_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                event_count=len(all_news)
            )
        else:
            self._mark_error(
                RuntimeError(f"全部 {len(symbols)} 只股票拉取失败"),
                traceback.format_exc()
            )

        logger.info(f"[EastMoneyNewsProvider] 完成: {success_count}/{len(symbols)} 成功, "
                   f"{error_count} 失败, 共 {len(all_news)} 条新闻")
        return all_news

    def fetch_since(self, timestamp: float) -> List[Dict[str, Any]]:
        """不支持按时间过滤 — 返回最新"""
        return self.fetch_latest()

    def normalize(self, raw_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        将 akshare 返回的原始新闻标准化为统一格式。

        输入格式:
          {
            "symbol_code": "sz000001",
            "title": "...",
            "content": "...",
            "publish_time_raw": "2026-06-26 10:30:00",
            "article_url": "https://...",
            "source_provider": "eastmoney"
          }
        """
        try:
            title = raw_item.get("title", "").strip()
            content = raw_item.get("content", "").strip()
            if not title:
                return None

            symbol_code = raw_item.get("symbol_code", "")
            pub_time_raw = raw_item.get("publish_time_raw", "")
            article_url = raw_item.get("article_url", "")

            # 解析时间
            event_time = None
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                        "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
                try:
                    event_time = datetime.strptime(pub_time_raw, fmt).strftime("%Y-%m-%d %H:%M:%S")
                    break
                except (ValueError, TypeError):
                    continue
            if not event_time:
                event_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            ingest_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Symbol
            symbols = [symbol_code] if symbol_code else []
            if symbol_code:
                # 同时存储 .SH/.SZ 格式以保持兼容
                code_only = symbol_code.replace("sh", "").replace("sz", "")
                if symbol_code.startswith("sh"):
                    symbols.append(f"{code_only}.SH")
                elif symbol_code.startswith("sz"):
                    symbols.append(f"{code_only}.SZ")

            # Event ID
            hash_str = f"eastmoney{event_time}{title}{article_url}"
            event_id = hashlib.sha256(hash_str.encode('utf-8')).hexdigest()

            # 事件类型推断
            event_type = "news"
            keywords = {
                "业绩预告": "earnings", "季报": "earnings", "年报": "earnings",
                "减持": "risk", "违规": "risk", "处罚": "risk", "退市": "risk",
                "涨停": "price_action", "跌停": "price_action",
            }
            for kw, etype in keywords.items():
                if kw in title:
                    event_type = etype
                    break

            return {
                "event_id": event_id,
                "source": "eastmoney",
                "event_type": event_type,
                "event_time": event_time,
                "ingest_time": ingest_time,
                "symbols": symbols,
                "title": title,
                "content": content,
                "url": article_url,
                "importance": "UNKNOWN",
                "sentiment": None,
                "confidence": None,
                "raw": raw_item,
            }
        except Exception as e:
            logger.error(f"[EastMoneyNewsProvider] normalize 失败: {e}\n{traceback.format_exc()}")
            return None
