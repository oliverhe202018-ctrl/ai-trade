"""
feeds/szse_news_provider.py — 深交所(SZSE)官方公告 Provider (Phase 3)

Tier: 1 (API)
数据源: 深圳证券交易所 www.szse.cn
覆盖范围: 全市场深市A股公告 (所有类型)
增强功能:
  - POST JSON 请求
  - announceCount 驱动分页
  - 防反爬 (随机延时 0.5~2.0s, UA轮换)
  - 公告类型推断 (earnings/risk/corporate_action/governance/announcement)
  - 股票代码自动绑定 (secCode[] → sz{code})
  - PDF/详情链接构建

速率: ~1 req/s (含随机抖动)
"""

import hashlib
import json
import math
import os
import random
import re as _re
import sys
import time
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from feeds.base_news_provider import BaseNewsProvider
from core.logger_config import logger


_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


def _random_delay(base: float = 0.5, jitter: float = 1.5) -> float:
    return base + random.uniform(0, jitter)


class SzseNewsProvider(BaseNewsProvider):
    """深交所(SZSE)官方公告 Provider。

    接口: POST www.szse.cn/api/disc/announcement/annList
    格式: JSON (标准 RESTful)
    分页: announceCount 驱动, ceil(announceCount / pageSize)
    """

    SZSE_API = "http://www.szse.cn/api/disc/announcement/annList"
    SZSE_DETAIL_URL = "http://www.szse.cn/disclosure/listed/fixed/index.html"
    SZSE_DOWNLOAD_BASE = "http://disc.static.szse.cn/download"

    def __init__(self, max_pages: int = 5, recent_hours: int = 24):
        """
        Args:
            max_pages: 最大翻页数 (默认5页, 约150条)
            recent_hours: 时间窗口 (仅抓取最近N小时公告, 默认24h)
        """
        super().__init__("szse")
        self.timeout = 15
        self._last_call_time = 0
        self._base_delay = 0.5
        self._jitter = 1.5
        self.max_pages = max_pages
        self.recent_hours = recent_hours

    def _rate_limit_wait(self) -> None:
        """速率限制 (随机抖动防反爬)。"""
        elapsed = time.time() - self._last_call_time
        target_delay = _random_delay(self._base_delay, self._jitter)
        if elapsed < target_delay:
            time.sleep(target_delay - elapsed)
        self._last_call_time = time.time()

    def _build_date_window(self) -> tuple:
        """构建日期窗口 (YYYY-MM-DD 格式)。"""
        end = datetime.now()
        start = end - timedelta(hours=self.recent_hours)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    def _fetch_page(self, page_num: int = 1, page_size: int = 30) -> Dict[str, Any]:
        """获取单页公告数据。

        Args:
            page_num: 页码 (1-indexed)
            page_size: 每页条数 (上限~30)

        Returns:
            解析后的响应 dict
        """
        import requests

        start_date, end_date = self._build_date_window()

        body = {
            "seDate": [start_date, end_date],
            "channelCode": ["fixed_disc"],
            "pageSize": min(page_size, 30),
            "pageNum": page_num,
        }

        headers = {
            "User-Agent": _random_ua(),
            "Referer": f"{self.SZSE_DETAIL_URL}",
            "Content-Type": "application/json",
            "Origin": "http://www.szse.cn",
            "Accept": "application/json, text/plain, */*",
        }

        response = requests.post(
            self.SZSE_API,
            json=body,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        response.encoding = 'utf-8'

        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"SZSE 响应格式异常: {type(data)}")

        return data

    def fetch_latest(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取最新公告列表 (带分页)。

        Args:
            limit: 最大返回条数

        Returns:
            原始公告列表
        """
        raw_items: List[Dict[str, Any]] = []

        try:
            # 第1页: 获取总数
            self._rate_limit_wait()
            res = self._fetch_page(page_num=1)
            items = res.get("data", [])
            announce_count = res.get("announceCount", 0)

            if not items:
                self._mark_success(last_event_time="", event_count=0)
                return []

            raw_items.extend(items)

            # 记录第一条事件时间
            first_item = items[0]
            pub_time = first_item.get("publishTime", "")
            if pub_time:
                self._mark_success(
                    last_event_time=pub_time,
                    event_count=min(len(items), announce_count),
                )
            else:
                self._mark_success(last_event_time="")

            # 翻页: 计算总页数, 不超过 max_pages
            page_size = 30
            total_pages = math.ceil(announce_count / page_size) if announce_count else 1
            actual_max = min(self.max_pages, total_pages)

            for page in range(2, actual_max + 1):
                if len(raw_items) >= limit:
                    break
                self._rate_limit_wait()
                res2 = self._fetch_page(page_num=page)
                new_items = res2.get("data", [])
                if not new_items:
                    break
                raw_items.extend(new_items)

            return raw_items[:limit]

        except Exception as e:
            self._mark_error(e, traceback.format_exc())
            logger.error(f"[SzseNewsProvider] 抓取失败: {e}")
            return []

    def fetch_since(self, timestamp: float) -> List[Dict[str, Any]]:
        """不支持时间戳过滤 — 返回最新公告。"""
        return self.fetch_latest(50)

    def normalize(self, raw_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """将 SZSE 原始公告标准化为 v1 canonical schema。

        Returns:
            标准化 dict (9字段 + backward-compat aliases) 或 None (无效数据)
        """
        try:
            title = raw_item.get("title", "").strip()
            if not title:
                return None

            sec_codes = raw_item.get("secCode", [])
            sec_names = raw_item.get("secName", [])
            attach_path = raw_item.get("attachPath", "")
            publish_time = raw_item.get("publishTime", "")
            ann_id = raw_item.get("annId", "")
            content_text = raw_item.get("content", "")

            if not publish_time:
                return None

            ingest_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 构建 URL: 优先PDF下载链接, 否则详情页
            if attach_path:
                url = f"{self.SZSE_DOWNLOAD_BASE}{attach_path}"
            elif ann_id:
                url = f"http://www.szse.cn/api/disc/announcement/annDetail?annId={ann_id}"
            else:
                url = self.SZSE_DETAIL_URL

            # 股票代码绑定 (深市 → sz{6位})
            symbols = []
            if isinstance(sec_codes, list):
                for code in sec_codes:
                    code_str = str(code).strip()
                    if len(code_str) == 6 and code_str.isdigit():
                        symbols.append(f"sz{code_str}")

            # 事件类型推断
            event_type = self._infer_event_type(title)

            # 摘要
            summary = (content_text or title)[:500]

            # 计算 SHA256 event_id
            hash_str = f"szse{publish_time}{title}{attach_path or ''}"
            event_id = hashlib.sha256(hash_str.encode('utf-8')).hexdigest()

            # 附加的title前缀 (公司名称)
            title_prefix = ""
            if isinstance(sec_names, list) and sec_names:
                title_prefix = f"[{sec_names[0]}] "

            normalized = {
                # Canonical v1 schema
                "id": event_id,
                "title": f"{title_prefix}{title}",
                "source": "szse",
                "published_at": publish_time,
                "url": url,
                "summary": summary,
                "content": content_text or title,
                "symbols": symbols,
                "fetched_at": ingest_time,
                # Backward-compat aliases
                "event_id": event_id,
                "event_type": event_type,
                "event_time": publish_time,
                "ingest_time": ingest_time,
                "importance": "UNKNOWN",
                "sentiment": None,
                "confidence": None,
                "raw": raw_item,
            }
            return normalized

        except Exception as e:
            logger.error(f"[SzseNewsProvider] normalize 失败: {e}\n{traceback.format_exc()}")
            return None

    def _infer_event_type(self, title: str) -> str:
        """从公告标题推断事件类型。"""
        # 业绩报告类
        if any(k in title for k in [
            "年报", "半年报", "季报", "业绩预告", "业绩快报",
            "年度报告", "半年度报告", "季度报告",
        ]):
            return "earnings"

        # 风险类
        if any(k in title for k in [
            "风险提示", "退市风险", "立案调查", "异常波动",
            "暂停上市", "终止上市", "ST", "*ST",
        ]):
            return "risk"

        # 公司行动类
        if any(k in title for k in [
            "减持", "增持", "回购", "股份变动", "权益变动",
            "分红", "派息", "转增", "送股",
        ]):
            return "corporate_action"

        # 治理类
        if any(k in title for k in [
            "股东大会", "董事会", "监事会", "独立董事",
            "公司章程", "制度", "管理办法",
        ]):
            return "governance"

        return "announcement"

    def health_check(self) -> Dict[str, Any]:
        """返回健康度状态 (继承基类 + 额外字段)。"""
        base = super().health_check()
        base["provider_type"] = "szse"
        base["endpoint"] = self.SZSE_API
        return base
