"""
feeds/sse_news_provider.py — 上交所(SSE)官方公告 Provider (Phase 3)

Tier: 1 (API)
数据源: 上海证券交易所 query.sse.com.cn
覆盖范围: 全市场沪市A股公告 (所有类型)
增强功能:
  - JSONP 响应自动解析
  - totalCount 驱动分页
  - 防反爬 (随机延时 0.5~2.0s, UA轮换, Referer固定)
  - 公告类型推断 (earnings/risk/corporate_action/governance/announcement)
  - 股票代码自动绑定 (SECURITY_CODE → sh{code})
  - PDF公告链接构建

速率: ~1 req/s (含随机抖动)
"""

import hashlib
import json
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


class SseNewsProvider(BaseNewsProvider):
    """上交所(SSE)官方公告 Provider。

    接口: GET query.sse.com.cn/security/stock/queryCompanyBulletin.do
    格式: JSONP (jsonpCallback({...}))
    分页: pageHelp.total / pageHelp.pageCount 驱动
    """

    SSE_API = "http://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
    SSE_BASE_URL = "http://www.sse.com.cn"

    def __init__(self, max_pages: int = 5, recent_hours: int = 24):
        """
        Args:
            max_pages: 最大翻页数 (默认5页, 约150条)
            recent_hours: 时间窗口 (仅抓取最近N小时公告, 默认24h)
        """
        super().__init__("sse")
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

    def _parse_jsonp(self, text: str) -> Dict[str, Any]:
        """解析 SSE JSONP 响应, 剥离回调函数包装。

        输入: jsonpCallback({...})
        输出: 解析后的 dict
        """
        # 匹配 JSONP 模式: callbackName({...});
        m = _re.search(r'^\s*\w+\s*\(\s*(.*)\s*\)\s*;?\s*$', text, _re.DOTALL)
        if m:
            return json.loads(m.group(1))
        # Fallback: 尝试直接解析
        return json.loads(text)

    def _fetch_page(self, page_num: int = 1, page_size: int = 30) -> Dict[str, Any]:
        """获取单页公告数据。

        Args:
            page_num: 页码 (1-indexed)
            page_size: 每页条数 (上限~25)

        Returns:
            解析后的 pageHelp dict 或空 dict
        """
        import requests

        start_date, end_date = self._build_date_window()

        params = {
            "jsonCallBack": "jsonpCallback",
            "isPagination": "true",
            "pageHelp.pageSize": str(min(page_size, 25)),
            "pageHelp.pageNo": str(page_num),
            "pageHelp.beginPage": str(page_num),
            "pageHelp.endPage": str(page_num),
            "securityType": "0101",      # 股票
            "reportType": "ALL",
            "beginDate": start_date,
            "endDate": end_date,
            "productId": "",
        }

        headers = {
            "User-Agent": _random_ua(),
            "Referer": f"{self.SSE_BASE_URL}/disclosure/listedinfo/announcement/",
            "Accept": "*/*",
        }

        response = requests.get(self.SSE_API, params=params, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        response.encoding = 'utf-8'

        data = self._parse_jsonp(response.text)
        if not isinstance(data, dict):
            raise ValueError(f"SSE 响应格式异常: {type(data)}")

        ph = data.get("pageHelp", {})
        if not isinstance(ph, dict):
            raise ValueError("SSE 响应缺少 pageHelp 字段")

        return ph

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
            ph = self._fetch_page(page_num=1)
            items = ph.get("data", [])
            total = ph.get("total", 0)
            page_count = ph.get("pageCount", 1)

            if not items:
                self._mark_success(last_event_time="", event_count=0)
                return []

            raw_items.extend(items)

            # 记录第一条事件时间
            first_item = items[0]
            add_date = first_item.get("ADDDATE", "")
            if add_date:
                self._mark_success(
                    last_event_time=add_date,
                    event_count=min(len(items), total)
                )
            else:
                self._mark_success(last_event_time="")

            # 翻页: 直到达到 max_pages 或超过 total
            actual_max = min(self.max_pages, page_count)
            for page in range(2, actual_max + 1):
                if len(raw_items) >= limit:
                    break
                self._rate_limit_wait()
                ph2 = self._fetch_page(page_num=page)
                new_items = ph2.get("data", [])
                if not new_items:
                    break
                raw_items.extend(new_items)

            return raw_items[:limit]

        except Exception as e:
            self._mark_error(e, traceback.format_exc())
            logger.error(f"[SseNewsProvider] 抓取失败: {e}")
            return []

    def fetch_since(self, timestamp: float) -> List[Dict[str, Any]]:
        """不支持时间戳过滤 — 返回最新公告。"""
        return self.fetch_latest(50)

    def normalize(self, raw_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """将 SSE 原始公告标准化为 v1 canonical schema。

        Returns:
            标准化 dict (9字段 + backward-compat aliases) 或 None (无效数据)
        """
        try:
            title = raw_item.get("TITLE", "").strip()
            if not title:
                return None

            sec_code = raw_item.get("SECURITY_CODE", "").strip()
            sec_name = raw_item.get("SECURITY_NAME", "").strip()
            bulletin_type = raw_item.get("BULLETIN_TYPE", "")
            bulletin_heading = raw_item.get("BULLETIN_HEADING", "")

            # 时间: SSEDATE + SSETimeStr (或 ADDDATE 作为fallback)
            sse_date = raw_item.get("SSEDATE", "")
            sse_time = raw_item.get("SSETimeStr", "00:00:00")
            add_date = raw_item.get("ADDDATE", "")

            if sse_date and sse_time:
                published_at = f"{sse_date} {sse_time}"
            elif add_date:
                published_at = add_date
            else:
                published_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            ingest_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 构建 URL
            url_path = raw_item.get("URL", "")
            if url_path:
                if url_path.startswith("/"):
                    url = f"{self.SSE_BASE_URL}{url_path}"
                else:
                    url = url_path
            else:
                url = ""

            # 股票代码绑定 (沪市 → sh{6位})
            symbols = []
            if sec_code and len(sec_code) == 6 and sec_code.isdigit():
                symbols.append(f"sh{sec_code}")

            # 事件类型推断
            event_type = self._infer_event_type(title, bulletin_type, bulletin_heading)

            # 摘要 (title[:500])
            summary = title[:500]

            # 计算 SHA256 event_id
            hash_str = f"sse{published_at}{title}{url}"
            event_id = hashlib.sha256(hash_str.encode('utf-8')).hexdigest()

            normalized = {
                # Canonical v1 schema
                "id": event_id,
                "title": f"[{sec_name}] {title}",
                "source": "sse",
                "published_at": published_at,
                "url": url,
                "summary": summary,
                "content": title,
                "symbols": symbols,
                "fetched_at": ingest_time,
                # Backward-compat aliases
                "event_id": event_id,
                "event_type": event_type,
                "event_time": published_at,
                "ingest_time": ingest_time,
                "importance": "UNKNOWN",
                "sentiment": None,
                "confidence": None,
                "raw": raw_item,
            }
            return normalized

        except Exception as e:
            logger.error(f"[SseNewsProvider] normalize 失败: {e}\n{traceback.format_exc()}")
            return None

    def _infer_event_type(
        self, title: str, bulletin_type: str, bulletin_heading: str
    ) -> str:
        """从公告标题/类型推断事件类型。"""
        combined = f"{title} {bulletin_type} {bulletin_heading}"

        # 业绩报告类
        if any(k in title for k in [
            "年报", "半年报", "季报", "业绩预告", "业绩快报",
            "年度报告", "半年度报告", "季度报告"
        ]):
            return "earnings"

        # 风险类
        if any(k in title for k in [
            "风险提示", "退市风险", "立案调查", "异常波动",
            "暂停上市", "终止上市", "ST", "*ST"
        ]):
            return "risk"

        # 公司行动类
        if any(k in title for k in [
            "减持", "增持", "回购", "股份变动", "权益变动",
            "分红", "派息", "转增", "送股"
        ]):
            return "corporate_action"

        # 治理类
        if any(k in title for k in [
            "股东大会", "董事会", "监事会", "独立董事",
            "公司章程", "制度", "管理办法"
        ]):
            return "governance"

        return "announcement"

    def health_check(self) -> Dict[str, Any]:
        """返回健康度状态 (继承基类 + 额外字段)。"""
        base = super().health_check()
        base["provider_type"] = "sse"
        base["endpoint"] = self.SSE_API
        return base
