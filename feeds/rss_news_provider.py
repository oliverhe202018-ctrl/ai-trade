"""
feeds/rss_news_provider.py — 通用 RSS/JSON 资讯源适配器 (Phase 5)

Tier: 2 (轻量API聚合)
数据源: Sina Finance JSON API + feedparser RSS/Atom
覆盖范围: 财经头条/A股新闻 (补充覆盖)
设计原则:
  - 轻量化: 不做深度内容解析, 仅结构化标题+时间+URL
  - 容错: 单源失败不影响其他源
  - 降级: 权重控制 (weight=0.0 自动禁用该源)
  - 统一: normalize() 输出 v1 canonical schema

支持的 Feed 类型:
  - json_api: HTTP GET → JSON → data_path 提取条目
  - rss_atom: HTTP GET → feedparser.parse() 解析
"""

import hashlib
import json
import os
import random
import re as _re
import sys
import time
import traceback
from datetime import datetime
from typing import Dict, List, Any, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from feeds.base_news_provider import BaseNewsProvider
from core.logger_config import logger


_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


class RssNewsProvider(BaseNewsProvider):
    """通用 RSS/JSON 资讯源适配器。

    支持两种 feed 类型:
      - json_api: JSON API (如 Sina Finance Roll)
      - rss_atom: 标准 RSS/Atom feed (需 feedparser)

    每个 feed 配置:
      {
        "name": "sina_finance",
        "type": "json_api",
        "url": "https://...",
        "data_path": "result.data",    # JSON路径 (点号分隔)
        "timeout_seconds": 10,
        "weight": 1.0,                 # 降级权重
      }
    """

    def __init__(self, feeds: Optional[List[Dict[str, Any]]] = None):
        """
        Args:
            feeds: feed 配置列表。每个 dict 包含 url/type/name/weight 等。
                   默认为 Sina Finance 双栏目。
        """
        super().__init__("rss")
        self._last_call_time = 0
        self._base_delay = 0.5
        self._jitter = 1.5
        self.feeds = feeds or self._default_feeds()

    @staticmethod
    def _default_feeds() -> List[Dict[str, Any]]:
        """默认 feed 配置: Sina Finance 财经头条 + A股。"""
        return [
            {
                "name": "sina_finance",
                "type": "json_api",
                "url": "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&k=&num=20&page=1",
                "data_path": "result.data",
                "timeout_seconds": 10,
                "weight": 1.0,
            },
            {
                "name": "sina_astock",
                "type": "json_api",
                "url": "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2512&k=&num=20&page=1",
                "data_path": "result.data",
                "timeout_seconds": 10,
                "weight": 0.8,
            },
        ]

    def _rate_limit_wait(self) -> None:
        elapsed = time.time() - self._last_call_time
        target_delay = self._base_delay + random.uniform(0, self._jitter)
        if elapsed < target_delay:
            time.sleep(target_delay - elapsed)
        self._last_call_time = time.time()

    def _fetch_json_api(self, feed_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
        """调用 JSON API 并提取条目。

        Args:
            feed_cfg: feed 配置 dict

        Returns:
            原始条目列表
        """
        import requests

        url = feed_cfg["url"]
        data_path = feed_cfg.get("data_path", "data")
        timeout = feed_cfg.get("timeout_seconds", 10)
        name = feed_cfg.get("name", "unknown")

        headers = {
            "User-Agent": _random_ua(),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://finance.sina.com.cn/",
        }

        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()

        try:
            resp_data = response.json()
        except json.JSONDecodeError:
            logger.warning(f"[RSS] {name}: 非JSON响应")
            return []

        # 沿 data_path 导航 (e.g., "result.data" → resp_data["result"]["data"])
        container = resp_data
        for key in data_path.split("."):
            if isinstance(container, dict):
                container = container.get(key)
            else:
                container = None
                break

        if not isinstance(container, list):
            logger.warning(f"[RSS] {name}: data_path 指向非列表 ({type(container)})")
            return []

        # 附加 feed 元信息
        for item in container:
            item["_rss_feed_name"] = name
            item["_rss_feed_type"] = "json_api"

        return container

    def _fetch_rss_feed(self, feed_cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
        """解析 RSS/Atom feed。

        Args:
            feed_cfg: feed 配置 dict

        Returns:
            标准化条目列表
        """
        import requests

        url = feed_cfg["url"]
        timeout = feed_cfg.get("timeout_seconds", 10)

        try:
            import feedparser  # type: ignore
        except ImportError:
            logger.warning("[RSS] feedparser 未安装, RSS模式不可用")
            return []

        headers = {"User-Agent": _random_ua()}
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()

        feed = feedparser.parse(response.content)
        if feed.get("status", 0) >= 400:
            logger.warning(f"[RSS] feed HTTP {feed.get('status')}: {url}")
            return []

        items = []
        for entry in feed.entries:
            items.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", ""),
                "published": entry.get("published", ""),
                "_rss_feed_name": feed_cfg.get("name", "rss"),
                "_rss_feed_type": "rss_atom",
            })

        return items

    def fetch_latest(self, limit: int = 50) -> List[Dict[str, Any]]:
        """批量拉取所有已配置 feed 的资讯。

        容错: 单个 feed 失败仅 skip, 不影响其他。

        Args:
            limit: 最大返回条数

        Returns:
            原始条目列表
        """
        all_items: List[Dict[str, Any]] = []
        success_count = 0
        error_count = 0

        for feed_cfg in self.feeds:
            weight = feed_cfg.get("weight", 1.0)
            if weight <= 0:
                continue  # 降权到0的源直接跳过

            name = feed_cfg.get("name", "unknown")
            feed_type = feed_cfg.get("type", "json_api")

            self._rate_limit_wait()

            try:
                if feed_type == "json_api":
                    items = self._fetch_json_api(feed_cfg)
                elif feed_type == "rss_atom":
                    items = self._fetch_rss_feed(feed_cfg)
                else:
                    logger.warning(f"[RSS] 未知feed类型: {feed_type}")
                    error_count += 1
                    continue

                if items:
                    all_items.extend(items)
                    success_count += 1
                    logger.debug(f"[RSS] {name}: fetched {len(items)} items")
                else:
                    error_count += 1

            except Exception as e:
                error_count += 1
                logger.warning(f"[RSS] {name} 抓取失败: {e}")

        # 记录健康状态
        if all_items:
            first = all_items[0]
            ctime = first.get("ctime") or first.get("published", "")
            self._mark_success(
                last_event_time=str(ctime),
                event_count=len(all_items),
            )
        else:
            self._mark_success(last_event_time="", event_count=0)

        return all_items[:limit]

    def fetch_since(self, timestamp: float) -> List[Dict[str, Any]]:
        return self.fetch_latest(50)

    def normalize(self, raw_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """将原始条目标准化为 v1 canonical schema。

        Sina JSON API 字段映射:
          title → title
          ctime → published_at (Unix timestamp 秒 → datetime)
          url → url
          intro → summary
          keywords → content

        RSS/Atom 字段映射:
          title → title
          published → published_at
          link → url
          summary → summary
        """
        try:
            title = raw_item.get("title", "").strip()
            if not title:
                return None

            feed_type = raw_item.get("_rss_feed_type", "json_api")
            feed_name = raw_item.get("_rss_feed_name", "rss")
            ingest_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 时间解析
            if feed_type == "json_api":
                ctime = raw_item.get("ctime", "")
                if ctime and str(ctime).isdigit():
                    event_time = datetime.fromtimestamp(int(ctime)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                else:
                    event_time = ingest_time
            else:
                published = raw_item.get("published", "")
                event_time = published or ingest_time

            # URL
            url = raw_item.get("url") or raw_item.get("link", "")

            # Summary & content
            summary = (raw_item.get("intro") or raw_item.get("summary") or title)[:500]
            content = raw_item.get("content") or raw_item.get("intro") or title

            # 从标题提取股票代码
            symbols = self._extract_symbols(title)

            # 事件类型推断
            event_type = self._infer_event_type(title)

            # Event ID
            hash_str = f"rss{feed_name}{event_time}{title}{url}"
            event_id = hashlib.sha256(hash_str.encode("utf-8")).hexdigest()

            normalized = {
                # Canonical v1 schema
                "id": event_id,
                "title": title,
                "source": f"rss_{feed_name}",
                "published_at": event_time,
                "url": url,
                "summary": summary,
                "content": content,
                "symbols": symbols,
                "fetched_at": ingest_time,
                # Backward-compat aliases
                "event_id": event_id,
                "event_type": event_type,
                "event_time": event_time,
                "ingest_time": ingest_time,
                "importance": "LOW",  # RSS源默认低重要性
                "sentiment": None,
                "confidence": None,
                "raw": raw_item,
            }
            return normalized

        except Exception as e:
            logger.error(
                f"[RssNewsProvider] normalize 失败: {e}\n"
                f"{traceback.format_exc()}"
            )
            return None

    def _extract_symbols(self, text: str) -> List[str]:
        """从文本提取A股代码 (sh/sz前缀)。"""
        if not text:
            return []
        codes = set()
        # Use lookbehind and non-consuming lookahead to avoid overlapping matches
        for m in _re.finditer(r'(?:^|[^\d])(\d{6})(?=$|[^\d])', text):
            code = m.group(1)
            if not code.isdigit():
                continue
            if code[0] in ('6', '9'):
                codes.add(f"sh{code}")
            elif code[0] in ('0', '2', '3'):
                codes.add(f"sz{code}")
        return sorted(codes)[:5]

    def _infer_event_type(self, title: str) -> str:
        """从标题推断事件类型 (轻量关键词匹配)。"""
        if any(k in title for k in ["年报","季报","业绩","财报","利润","营收"]):
            return "earnings"
        if any(k in title for k in ["风险","跌","崩盘","退市","ST","处罚","调查"]):
            return "risk"
        if any(k in title for k in ["减持","增持","回购","分红","派息","并购","重组"]):
            return "corporate_action"
        return "news"

    def health_check(self) -> Dict[str, Any]:
        base = super().health_check()
        base["provider_type"] = "rss"
        base["feeds_count"] = len(self.feeds)
        base["feeds"] = [f.get("name", "?") for f in self.feeds]
        return base
