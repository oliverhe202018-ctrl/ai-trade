"""
feeds/cninfo_news_provider.py — 巨潮资讯(CNINFO)公告 Provider v2 (Phase 4 优化升级)

Tier: 1 (API)
数据源: 巨潮资讯网 www.cninfo.com.cn (深交所/上交所指定信息披露平台)
覆盖范围: 沪深两市全量A股公告 (359,210+条)

v2 增强 (Phase 4):
  - P0: 分页遍历 (totalpages/hasMore驱动, max_pages=5)
  - P1: 多栏目轮询 (sse + szse 双市场)
  - P1: 时间窗口过滤 (seDate: YYYY-MM-DD~YYYY-MM-DD)
  - P2: 速率控制 (随机延时0.5~2.0s, UA轮换)
  - P2: 增强事件分类 (5种类型: earnings/risk/corporate_action/governance/announcement)
  - P2: 强化错误处理 (响应结构验证, JSON解析保护)

v1 保留功能:
  - SHA256 event_id dedup
  - v1 canonical schema (9字段) + backward-compat aliases
  - BaseNewsProvider 健康检查
  - retry_with_backoff 装饰器
"""

import hashlib
import json
import os
import random
import sys
import time
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from feeds.base_news_provider import BaseNewsProvider
from core.utils import retry_with_backoff
from core.logger_config import logger


_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


def _random_delay(base: float = 0.5, jitter: float = 1.5) -> float:
    return base + random.uniform(0, jitter)


class CninfoNewsProvider(BaseNewsProvider):
    """巨潮资讯(CNINFO)官方公告 Provider v2。

    接口: POST www.cninfo.com.cn/new/hisAnnouncement/query
    分页: totalpages/hasMore 驱动
    栏目: sse (上交所) + szse (深交所) 双市场轮询
    """

    BASE_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"

    def __init__(self, max_pages: int = 5, recent_hours: int = 24):
        """
        Args:
            max_pages: 最大翻页数 (每个栏目, 默认5)
            recent_hours: 时间窗口 (仅抓取最近N小时公告)
        """
        super().__init__("cninfo")
        self.timeout = 8
        self.max_pages = max_pages
        self.recent_hours = recent_hours
        self._last_call_time = 0
        self._base_delay = 0.5
        self._jitter = 1.5

    def _rate_limit_wait(self) -> None:
        """速率限制 (随机抖动防反爬)。"""
        elapsed = time.time() - self._last_call_time
        target_delay = _random_delay(self._base_delay, self._jitter)
        if elapsed < target_delay:
            time.sleep(target_delay - elapsed)
        self._last_call_time = time.time()

    def _build_date_window(self) -> str:
        """构建 seDate 参数字符串 (YYYY-MM-DD~YYYY-MM-DD)。"""
        end = datetime.now()
        start = end - timedelta(hours=self.recent_hours)
        return f"{start.strftime('%Y-%m-%d')}~{end.strftime('%Y-%m-%d')}"

    @retry_with_backoff(retries=3, backoff_in_seconds=(2, 5, 10))
    def _fetch_page(
        self, column: str = "szse", page_num: int = 1, page_size: int = 30
    ) -> Dict[str, Any]:
        """获取单页公告数据。

        Args:
            column: 交易所代码 (sse/szse/bj)
            page_num: 页码 (1-indexed)
            page_size: 每页条数 (上限~50)

        Returns:
            解析后的 API 响应 dict
        """
        import requests

        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "http://www.cninfo.com.cn",
            "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
            "User-Agent": _random_ua(),
            "X-Requested-With": "XMLHttpRequest",
        }

        data = {
            "pageNum": page_num,
            "pageSize": min(page_size, 50),
            "column": column,
            "hsecName": "",
            "tabName": "fulltext",
            "sortName": "",
            "sortType": "",
            "limit": "",
            "showTitle": "",
            "seDate": self._build_date_window(),
        }

        response = requests.post(
            self.BASE_URL, headers=headers, data=data, timeout=self.timeout
        )
        response.raise_for_status()

        res_json = response.json()
        if not isinstance(res_json, dict):
            raise ValueError(f"CNINFO({column}) 响应不是 JSON 对象: {type(res_json)}")

        if "announcements" not in res_json:
            raise ValueError(
                f"CNINFO({column}) 响应缺少 announcements 字段, "
                f"当前字段: {list(res_json.keys())[:10]}"
            )

        return res_json

    def _fetch_column_announcements(
        self, column: str, limit: int
    ) -> List[Dict[str, Any]]:
        """拉取单个交易所栏目的公告 (带分页)。

        Args:
            column: 交易所代码 (sse/szse)
            limit: 本栏目最大返回条数

        Returns:
            原始公告列表
        """
        items: List[Dict[str, Any]] = []

        try:
            self._rate_limit_wait()
            res = self._fetch_page(column=column, page_num=1)

            announcements = res.get("announcements", [])
            if not announcements:
                return []

            items.extend(announcements)

            # 读取分页信息
            totalpages = res.get("totalpages", 1)
            has_more = res.get("hasMore", False)

            # 计算实际翻页数
            actual_max = min(self.max_pages, int(totalpages))
            for page in range(2, actual_max + 1):
                if len(items) >= limit:
                    break
                if not has_more and page > totalpages:
                    break
                self._rate_limit_wait()
                res2 = self._fetch_page(column=column, page_num=page)
                new_items = res2.get("announcements", [])
                if not new_items:
                    break
                items.extend(new_items)
                has_more = res2.get("hasMore", False)

            return items[:limit]

        except Exception as e:
            logger.error(
                f"[CninfoNewsProvider] 栏目 {column} 抓取失败: {e}\n"
                f"{traceback.format_exc()}"
            )
            return items  # 返回已成功抓取的部分

    def fetch_latest(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取最新公告 (双市场: sse + szse)。

        Args:
            limit: 最大返回条数 (双市场合计)

        Returns:
            原始公告列表
        """
        all_items: List[Dict[str, Any]] = []
        total_fetched = 0

        try:
            # 每栏目分配一半 limit
            per_column_limit = max(10, limit // 2)

            for column in ("sse", "szse"):
                col_items = self._fetch_column_announcements(column, per_column_limit)
                if col_items:
                    # 为每条item附加来源栏目
                    for item in col_items:
                        item["_cninfo_column"] = column
                    all_items.extend(col_items)
                    total_fetched += len(col_items)

            # 去重 (按 title+secCode)
            seen = set()
            deduped = []
            for item in all_items:
                key = (item.get("announcementTitle", ""), item.get("secCode", ""))
                if key not in seen:
                    seen.add(key)
                    deduped.append(item)

            # 记录健康状态
            if deduped:
                first = deduped[0]
                ts_ms = first.get("announcementTime")
                if ts_ms:
                    event_dt = datetime.fromtimestamp(ts_ms / 1000.0)
                    self._mark_success(
                        last_event_time=event_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        event_count=len(deduped),
                    )
                else:
                    self._mark_success(last_event_time="", event_count=len(deduped))
            else:
                self._mark_success(last_event_time="", event_count=0)

            return deduped[:limit]

        except Exception as e:
            self._mark_error(e, traceback.format_exc())
            logger.error(f"[CninfoNewsProvider] 批量抓取崩溃: {e}")
            return all_items[:limit] if all_items else []

    def fetch_since(self, timestamp: float) -> List[Dict[str, Any]]:
        """按时间戳过滤 — 返回最新公告。"""
        return self.fetch_latest(50)

    def normalize(self, raw_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """将 CNINFO 原始公告标准化为 v1 canonical schema。

        v2 增强: 事件类型分类 (5种), 双市场代码绑定。

        Returns:
            标准化 dict (9字段 + backward-compat aliases) 或 None
        """
        try:
            title = raw_item.get("announcementTitle", "").strip()
            if not title:
                return None

            ts_ms = raw_item.get("announcementTime")
            if not ts_ms:
                return None

            event_time = datetime.fromtimestamp(ts_ms / 1000.0).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            ingest_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            sec_code = raw_item.get("secCode", "").strip()
            sec_name = raw_item.get("secName", "").strip()
            column = raw_item.get("_cninfo_column", "")

            # 股票代码绑定 (沪深 → sh/sz前缀)
            symbols = []
            if sec_code and len(sec_code) == 6 and sec_code.isdigit():
                if sec_code.startswith(("6", "9")):
                    symbols.append(f"sh{sec_code}")
                else:
                    symbols.append(f"sz{sec_code}")

            # 构建 URL (公告详情页)
            ann_id = raw_item.get("announcementId", "")
            url_path = raw_item.get("adjunctUrl", "")
            if ann_id:
                url = f"http://www.cninfo.com.cn/new/disclosure/detail?announcementId={ann_id}"
            elif url_path:
                url = f"http://static.cninfo.com.cn/{url_path}"
            else:
                url = ""

            # 增强事件类型推断 (v2: 5种类型)
            event_type = self._infer_event_type(title)

            # SHA256 event_id
            hash_str = f"cninfo{event_time}{title}{url}"
            event_id = hashlib.sha256(hash_str.encode("utf-8")).hexdigest()

            normalized = {
                # Canonical v1 schema
                "id": event_id,
                "title": f"[{sec_name}] {title}" if sec_name else title,
                "source": "cninfo",
                "published_at": event_time,
                "url": url,
                "summary": title[:500],
                "content": title,
                "symbols": symbols,
                "fetched_at": ingest_time,
                # Backward-compat aliases
                "event_id": event_id,
                "event_type": event_type,
                "event_time": event_time,
                "ingest_time": ingest_time,
                "importance": "UNKNOWN",
                "sentiment": None,
                "confidence": None,
                "raw": raw_item,
            }
            return normalized

        except Exception as e:
            logger.error(
                f"[CninfoNewsProvider] normalize 失败: {e}\n"
                f"{traceback.format_exc()}"
            )
            return None

    def _infer_event_type(self, title: str) -> str:
        """从公告标题推断事件类型 (v2增强: 5种分类)。"""
        # 业绩报告类
        if any(
            k in title
            for k in [
                "年报", "年度报告", "半年报", "半年度报告",
                "季报", "季度报告", "业绩预告", "业绩快报",
            ]
        ):
            return "earnings"

        # 风险类
        if any(
            k in title
            for k in [
                "风险提示", "退市风险", "立案调查", "异常波动",
                "暂停上市", "终止上市", "ST", "*ST",
                "行政处罚", "监管措施",
            ]
        ):
            return "risk"

        # 公司行动类
        if any(
            k in title
            for k in [
                "减持", "增持", "回购", "股份变动", "权益变动",
                "分红", "派息", "转增", "送股", "利润分配",
            ]
        ):
            return "corporate_action"

        # 治理类
        if any(
            k in title
            for k in [
                "股东大会", "董事会", "监事会", "独立董事",
                "公司章程", "制度", "管理办法", "内部控制",
            ]
        ):
            return "governance"

        return "announcement"

    def health_check(self) -> Dict[str, Any]:
        """返回健康度状态。"""
        base = super().health_check()
        base["provider_type"] = "cninfo"
        base["endpoint"] = self.BASE_URL
        return base
