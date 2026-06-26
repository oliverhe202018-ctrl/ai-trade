"""
feeds/eastmoney_news_provider.py — 东方财富资讯 Provider (Phase 2 Recovery)

Tier: 1 (API)
数据源: search-api-web.eastmoney.com
覆盖栏目:
  - 个股新闻 (search type: cmsArticleWebOld)
  - 公告 (search type: cmsAnnouncementWebOld)
  - 研报 (search type: cmsReportWebOld)
  - 7x24 快讯 (np-listapi.eastmoney.com + fallback search)
  - 板块资讯 (sector keyword rotation)

Phase 2 Recovery 增强:
  - 5栏目全量实现
  - 多type分页 (totalCount 驱动, max_pages 硬上限)
  - 防反爬 (随机延时 1.0-2.5s, 5个UA轮换, Referer动态)
  - Symbol 精准过滤 (A股允许, ETF/可转债/逆回购/B股排除)
  - 增强去重 (title+URL, url空fallback)
  - 8种事件类型推断
  - 可解释重要性评分 (base/keyword/category/content/type → final)
  - normalize v1 canonical schema
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
from typing import Dict, List, Any, Optional, Set, Tuple

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from feeds.base_news_provider import BaseNewsProvider
from core.logger_config import logger

# ═══════════════════════════════════════════════════════════
# Anti-crawl helpers
# ═══════════════════════════════════════════════════════════

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

def _random_ua() -> str:
    return random.choice(_USER_AGENTS)

# ═══════════════════════════════════════════════════════════
# Symbol resolution and filtering
# ═══════════════════════════════════════════════════════════

# A股有效前缀
_VALID_STOCK_PREFIXES = {
    '600', '601', '603', '605',  # 沪市主板
    '688',                        # 科创板
    '000', '001', '002', '003',  # 深市主板/中小板
    '300', '301',                 # 创业板
}

# 排除列表 (ETF/可转债/逆回购/B股)
_EXCLUDED_PREFIXES = {
    # ETF
    '159', '510', '511', '512', '513', '515', '516', '517', '518',
    '560', '561', '562', '563', '588', '589',
    # 可转债
    '110', '111', '113', '118', '123', '127', '128',
    # 逆回购
    '204', '131', '019',
    # B股
    '200', '900',
}


def _resolve_symbol(code: str) -> Optional[str]:
    """将各种格式的股票代码标准化为 sh{6}/sz{6}。

    支持: 纯6位数字, SH600519, sh600519, 600519.SH, 000001.SZ, SZ000001 等.
    返回: 'sh600519' / 'sz000001' 或 None (无效).
    """
    if not code or not isinstance(code, str):
        return None

    clean = code.strip().upper()

    # 剥离后缀 (.SH / .SZ)
    for suffix in ['.SH', '.SZ']:
        clean = clean.replace(suffix, '')

    # 剥离前缀 (SH / SZ) — 优先处理长前缀
    if clean.startswith('SH') and len(clean) == 8:
        clean = clean[2:]
    elif clean.startswith('SZ') and len(clean) == 8:
        clean = clean[2:]

    # 必须是6位纯数字
    if not clean.isdigit() or len(clean) != 6:
        return None

    # 排除ETF/可转债/逆回购/B股
    for prefix in _EXCLUDED_PREFIXES:
        if clean.startswith(prefix):
            return None

    # 验证A股前缀
    valid = False
    for prefix in _VALID_STOCK_PREFIXES:
        if clean.startswith(prefix):
            valid = True
            break
    if not valid:
        return None

    # 沪市 (6/9开头) → sh, 深市 (0/2/3开头) → sz
    if clean[0] in ('6', '9'):
        return f"sh{clean}"
    else:
        return f"sz{clean}"


def _is_valid_stock_code(code: str) -> bool:
    """检查6位数字是否为有效A股代码 (非ETF/可转债/逆回购/B股)。"""
    return _resolve_symbol(code) is not None


# ═══════════════════════════════════════════════════════════
# Search API types
# ═══════════════════════════════════════════════════════════

_SEARCH_TYPES = {
    "stock": "cmsArticleWebOld",
    "news": "cmsArticleWebOld",
    "announcement": "cmsAnnouncementWebOld",
    "report": "cmsReportWebOld",
}

# Sector keywords pool
_SECTOR_KEYWORDS = [
    "人工智能", "新能源", "半导体", "医药", "消费",
    "金融", "房地产", "汽车", "军工", "光伏",
    "锂电池", "储能", "数据要素", "机器人", "低空经济",
    "算力", "芯片", "创新药", "白酒", "电力",
]


class EastMoneyNewsProvider(BaseNewsProvider):
    """东方财富资讯 Provider (Phase 2 Recovery)。

    五栏目: stock / announcement / report / flash / sector
    分页: totalCount 驱动, max_pages 硬上限
    """

    SEARCH_API = "https://search-api-web.eastmoney.com/search/jsonp"
    FLASH_API = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"

    def __init__(
        self,
        max_pages: int = 5,
        recent_hours: Optional[int] = None,
        categories: Optional[List[str]] = None,
        request_delay_min: float = 1.0,
        request_delay_max: float = 2.5,
        page_size: int = 20,
        sector_max_keywords: int = 3,
    ):
        super().__init__("eastmoney")
        self.timeout = 15
        self._last_call_time = 0.0
        self.max_pages = max_pages
        self.recent_hours = recent_hours
        self.categories = categories or ["stock", "announcement", "report"]
        self.request_delay_min = request_delay_min
        self.request_delay_max = request_delay_max
        self.page_size = page_size
        self.sector_max_keywords = sector_max_keywords

    def _rate_limit_wait(self) -> None:
        """随机延时防反爬。"""
        elapsed = time.time() - self._last_call_time
        target = self.request_delay_min + random.uniform(0, self.request_delay_max - self.request_delay_min)
        if elapsed < target:
            time.sleep(target - elapsed)
        self._last_call_time = time.time()

    # ── Public API ────────────────────────────────────────────

    def fetch_latest(self, limit: int = 50, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """批量拉取资讯 (五栏目 + per-symbol 多type + flash + sector)。

        Args:
            limit: 每只股票或每类别最大返回条数
            symbols: 股票代码列表, None 使用预置热门股

        Returns:
            原始文章列表 (含 category, _extracted_symbols, publish_time_raw 等)
        """
        import requests

        all_articles: List[Dict[str, Any]] = []
        success_count = 0
        error_count = 0

        # ── 1. Flash (7x24快讯) ──
        if "flash" in self.categories:
            self._rate_limit_wait()
            try:
                flash_items = self._fetch_flash_news(limit=min(limit, 30))
                all_articles.extend(flash_items)
                success_count += 1
            except Exception as e:
                error_count += 1
                logger.warning(f"[EastMoney] flash 抓取失败: {e}")

        # ── 2. Sector (板块关键词轮询) ──
        if "sector" in self.categories:
            kw_count = min(self.sector_max_keywords, len(_SECTOR_KEYWORDS))
            selected_kw = random.sample(_SECTOR_KEYWORDS, kw_count) if kw_count > 0 else []
            for kw in selected_kw:
                self._rate_limit_wait()
                try:
                    sec_items = self._fetch_sector_articles(kw, limit=min(limit, 15))
                    all_articles.extend(sec_items)
                    success_count += 1
                except Exception as e:
                    error_count += 1
                    logger.warning(f"[EastMoney] sector({kw}) 失败: {e}")

        # ── 3. Per-symbol 多类别拉取 ──
        if symbols is None:
            symbols = self._default_symbols()

        symbol_categories = [c for c in self.categories if c not in ("flash", "sector")]

        for code in symbols:
            resolved = _resolve_symbol(code)
            if resolved is None:
                continue
            clean_code = resolved[2:]  # strip sh/sz prefix for API keyword

            self._rate_limit_wait()
            try:
                stock_articles: List[Dict[str, Any]] = []
                for cat in symbol_categories:
                    search_type = _SEARCH_TYPES.get(cat, _SEARCH_TYPES["stock"])
                    page_items = self._fetch_paginated(
                        clean_code, search_type, cat, limit=limit
                    )
                    stock_articles.extend(page_items)

                # Per-symbol dedup
                seen_urls: Set[str] = set()
                deduped: List[Dict[str, Any]] = []
                for art in stock_articles:
                    url = art.get("article_url", "")
                    title = art.get("title", "")
                    key = url or f"{title}::{art.get('publish_time_raw','')}::{art.get('category','')}"
                    if key and key in seen_urls:
                        continue
                    seen_urls.add(key)
                    deduped.append(art)

                for article in deduped[:limit]:
                    title = _re.sub(r"</?em>", "", article.get("title", "")).strip()
                    content = _re.sub(r"</?em>", "", article.get("content", "")).strip()
                    content = content.replace("\u3000", "").replace("\r\n", " ")

                    if not title:
                        continue

                    # Extract symbols from title+content
                    extracted_syms: Set[str] = {resolved}
                    combined = f"{title} {content}"
                    for m in _re.finditer(r'(?:^|[^\d])(\d{6})(?=$|[^\d])', combined):
                        candidate = m.group(1)
                        rs = _resolve_symbol(candidate)
                        if rs:
                            extracted_syms.add(rs)

                    all_articles.append({
                        "title": title,
                        "content": content[:2000],
                        "article_url": article.get("article_url", ""),
                        "publish_time_raw": article.get("date", ""),
                        "category": article.get("category", cat),
                        "symbol_code": resolved,
                        "_extracted_symbols": sorted(extracted_syms),
                    })

                success_count += 1

            except Exception as e:
                error_count += 1
                logger.warning(f"[EastMoney] {code} 拉取失败: {e}")

        # ── 4. Global dedup (title based) ──
        seen_titles: Set[str] = set()
        seen_urls: Set[str] = set()
        final: List[Dict[str, Any]] = []
        for art in all_articles:
            title = art.get("title", "").strip()
            url = art.get("article_url", "")

            if url and url in seen_urls:
                continue
            clean_title = _re.sub(r"</?em>", "", title)
            if clean_title in seen_titles:
                continue

            seen_titles.add(clean_title)
            if url:
                seen_urls.add(url)
            final.append(art)

        self._finalize(success_count, error_count, final)
        return final[:limit]

    def fetch_since(self, timestamp: float) -> List[Dict[str, Any]]:
        return self.fetch_latest(50)

    # ── Normalization ──────────────────────────────────────────

    def normalize(self, raw_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """将原始条目标准化为 v1 canonical schema + Phase 2 扩展。"""
        try:
            title = raw_item.get("title", "").strip()
            # Clean HTML tags
            title = _re.sub(r"</?em>", "", title).strip()
            if not title:
                return None

            category = raw_item.get("category", "stock")
            article_url = raw_item.get("article_url", "")
            publish_time_raw = raw_item.get("publish_time_raw", "")
            content = raw_item.get("content", "")[:500]

            # 时间解析
            event_time = self._parse_time(publish_time_raw)
            ingest_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Symbols
            raw_syms = raw_item.get("_extracted_symbols", [])
            symbol_code = raw_item.get("symbol_code", "")
            symbols: List[str] = []
            if symbol_code:
                symbols.append(symbol_code)
            for s in raw_syms:
                if s not in symbols:
                    symbols.append(s)

            # Event ID
            hash_key = f"eastmoney{event_time}{title}{article_url or ''}"
            event_id = hashlib.sha256(hash_key.encode("utf-8")).hexdigest()

            # Event type
            event_type = self._infer_event_type(title, category)

            # Importance score (可解释)
            importance_detail = self._calculate_importance(title, content, event_type, category)
            importance = importance_detail["importance"]

            normalized = {
                # Canonical v1 schema
                "id": event_id,
                "title": title,
                "source": "eastmoney",
                "published_at": event_time,
                "url": article_url,
                "summary": content,
                "content": content,
                "symbols": symbols,
                "fetched_at": ingest_time,
                # Backward-compat aliases
                "event_id": event_id,
                "event_type": event_type,
                "event_time": event_time,
                "ingest_time": ingest_time,
                "importance": importance,
                "sentiment": None,
                "confidence": None,
                # Phase 2 扩展
                "category": category,
                "importance_detail": importance_detail,
                "raw": raw_item,
            }
            return normalized

        except Exception as e:
            logger.error(f"[EastMoney] normalize 失败: {e}\n{traceback.format_exc()}")
            return None

    # ── Event type inference ───────────────────────────────────

    def _infer_event_type(self, title: str, category: str) -> str:
        """推断事件类型 (8种 + fallback)。"""
        if category == "announcement":
            base = "announcement"
        elif category == "report":
            base = "research"
        elif category == "flash":
            base = "flash"
        else:
            base = "news"

        kw_map = {
            "earnings": ["业绩预告", "季报", "年报", "年度报告", "半年报", "半年度报告",
                         "三季报", "中报", "业绩快报", "净利润", "营收", "利润",
                         "预增", "预减", "财报"],
            "risk": ["减持", "违规", "处罚", "退市", "立案", "停牌",
                     "警示", "关注函", "问询函", "监管函", "风险提示"],
            "corporate_action": ["回购", "增持", "分红", "送转", "重组", "并购",
                                 "派息", "权益分派", "股份变动"],
            "contract": ["中标", "重大合同", "订单", "签约"],
            "partnership": ["战略合作", "战略协议", "合作框架"],
        }
        for etype, keywords in kw_map.items():
            for kw in keywords:
                if kw in title:
                    return etype

        return base

    # ── Importance scoring (可解释) ─────────────────────────────

    def _calculate_importance(
        self, title: str, content: str, event_type: str, category: str
    ) -> Dict[str, Any]:
        """计算可解释重要性评分。

        Returns:
            Dict with base_score, keyword_score, category_bonus, content_bonus,
            type_bonus, final_score, importance, matched_keywords
        """
        base_score = 0
        keyword_score = 0
        matched_keywords: List[str] = []

        # Category bonus
        category_bonus_map = {"announcement": 1, "report": 1, "flash": 0, "sector": 0, "stock": 0}
        category_bonus = category_bonus_map.get(category, 0)
        base_score += category_bonus

        # High impact keywords (+3 each, max 1 match)
        high_kw = ["涨停", "跌停", "突破", "历史新高", "重大合同",
                   "战略合作", "重组", "并购", "退市", "立案",
                   "业绩预增", "业绩暴增", "超预期"]
        for kw in high_kw:
            if kw in title:
                keyword_score += 3
                matched_keywords.append(kw)
                break

        # Medium impact keywords (+1 each)
        medium_kw = ["业绩预告", "季报", "年报", "减持", "增持",
                     "中标", "回购", "分红", "估值", "政策"]
        for kw in medium_kw:
            if kw in title:
                keyword_score += 1
                matched_keywords.append(kw)

        # Content length bonus
        content_bonus = 0
        if len(content) > 500:
            content_bonus += 1
        if len(content) > 1000:
            content_bonus += 1

        # Event type bonus
        type_bonus_map = {
            "price_action": 2, "earnings": 1, "risk": 2,
            "contract": 1, "corporate_action": 1, "announcement": 1,
            "research": 1, "flash": 0, "partnership": 1,
        }
        type_bonus = type_bonus_map.get(event_type, 0)

        final_score = base_score + keyword_score + category_bonus + content_bonus + type_bonus

        if final_score >= 5:
            importance = "HIGH"
        elif final_score >= 3:
            importance = "MEDIUM"
        else:
            importance = "LOW"

        return {
            "base_score": base_score,
            "keyword_score": keyword_score,
            "category_bonus": category_bonus,
            "content_bonus": content_bonus,
            "type_bonus": type_bonus,
            "final_score": final_score,
            "importance": importance,
            "matched_keywords": matched_keywords,
        }

    # ── Internal: Paginated search ──────────────────────────────

    def _fetch_paginated(
        self, code: str, search_type: str, category: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """通用分页获取 (totalCount 驱动, max_pages 硬上限)。

        403/429 时停止翻页但保留已有结果。
        """
        import requests

        articles: List[Dict[str, Any]] = []

        for page in range(1, self.max_pages + 1):
            self._rate_limit_wait()

            inner = {
                "uid": "",
                "keyword": code,
                "type": [search_type],
                "client": "web",
                "clientType": "web",
                "clientVersion": "curr",
                "param": {
                    search_type: {
                        "searchScope": "default",
                        "sort": "default",
                        "pageIndex": page,
                        "pageSize": min(self.page_size, 20),
                        "preTag": "<em>",
                        "postTag": "</em>",
                    }
                },
            }
            params = {
                "cb": "jQuery_em",
                "param": json.dumps(inner, ensure_ascii=False),
                "_": str(int(time.time() * 1000)),
            }
            headers = {
                "User-Agent": _random_ua(),
                "Referer": f"https://so.eastmoney.com/news/s?keyword={code}",
            }

            try:
                resp = requests.get(self.SEARCH_API, params=params, headers=headers, timeout=self.timeout)
                if resp.status_code in (403, 429):
                    logger.warning(f"[EastMoney] {category}/{code} p{page} HTTP {resp.status_code}, 停止翻页")
                    break
                resp.raise_for_status()
            except requests.RequestException as e:
                logger.warning(f"[EastMoney] {category}/{code} p{page} 请求失败: {e}, 停止翻页")
                break

            # Parse JSONP
            text = resp.text
            m = _re.search(r"jQuery_em\s*\(\s*({.*})\s*\)\s*;?\s*$", text, _re.DOTALL)
            if not m:
                m = _re.search(r"jQuery_em\s*\(\s*(.*)\s*\)\s*;?\s*$", text, _re.DOTALL)
            if not m:
                logger.debug(f"[EastMoney] {code} JSONP 解析失败, 停止翻页")
                break

            try:
                data = json.loads(m.group(1))
            except json.JSONDecodeError:
                break

            # Read totalCount (page 1 only)
            if page == 1:
                total = data.get("totalCount", 0)
                if total == 0:
                    break
                # If total fits in one page, stop
                if total <= self.page_size:
                    pass  # still add this page

            page_items = data.get("result", {}).get(search_type, [])
            if not page_items:
                break

            for item in page_items:
                item["_category"] = category
            articles.extend(page_items)

            if len(page_items) < self.page_size:
                break  # partial page = last page

        return articles

    # ── Internal: Flash (7x24) ──────────────────────────────────

    def _fetch_flash_news(self, limit: int = 30) -> List[Dict[str, Any]]:
        """获取 7x24 快讯。Fallback: 多路径字段兼容。"""
        import requests

        headers = {
            "User-Agent": _random_ua(),
            "Referer": "https://www.eastmoney.com/",
        }
        params = {
            "client": "web",
            "biz": "web_news_col",
            "column": "100",
            "needContent": "1",
            "pageSize": str(min(limit, 30)),
            "pageIndex": "1",
        }

        resp = requests.get(self.FLASH_API, params=params, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        # Multi-path fallback
        items = data.get("data", {}).get("list", [])
        if not items:
            items = data.get("data", {}).get("data", [])
        if not items:
            items = data.get("result", {}).get("list", [])

        results = []
        for item in items:
            title = item.get("title") or item.get("Title", "")
            url = item.get("url") or item.get("link", "")
            pub_time = item.get("showTime") or item.get("time") or item.get("publish_time", "")

            if not title:
                continue

            results.append({
                "title": title.strip(),
                "content": title.strip(),
                "article_url": url,
                "publish_time_raw": pub_time,
                "category": "flash",
                "symbol_code": "",
                "_extracted_symbols": [],
            })

        return results[:limit]

    # ── Internal: Sector ────────────────────────────────────────

    def _fetch_sector_articles(self, keyword: str, limit: int = 15) -> List[Dict[str, Any]]:
        """板块关键词搜索 (使用 stock search type)。"""
        import requests

        search_type = _SEARCH_TYPES["stock"]
        inner = {
            "uid": "",
            "keyword": keyword,
            "type": [search_type],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                search_type: {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": min(limit, 20),
                    "preTag": "<em>",
                    "postTag": "</em>",
                }
            },
        }
        params = {
            "cb": "jQuery_em",
            "param": json.dumps(inner, ensure_ascii=False),
            "_": str(int(time.time() * 1000)),
        }
        headers = {
            "User-Agent": _random_ua(),
            "Referer": f"https://so.eastmoney.com/news/s?keyword={keyword}",
        }

        resp = requests.get(self.SEARCH_API, params=params, headers=headers, timeout=self.timeout)
        resp.raise_for_status()

        text = resp.text
        m = _re.search(r"jQuery_em\s*\(\s*({.*})\s*\)\s*;?\s*$", text, _re.DOTALL)
        if not m:
            return []

        data = json.loads(m.group(1))
        page_items = data.get("result", {}).get(search_type, [])

        results = []
        for item in page_items:
            title = _re.sub(r"</?em>", "", item.get("title", "")).strip()
            if not title:
                continue

            syms: Set[str] = set()
            combined = f"{title} {item.get('content','')}"
            for mm in _re.finditer(r'(?:^|[^\d])(\d{6})(?=$|[^\d])', combined):
                rs = _resolve_symbol(mm.group(1))
                if rs:
                    syms.add(rs)

            results.append({
                "title": title,
                "content": _re.sub(r"</?em>", "", item.get("content", ""))[:500],
                "article_url": item.get("url", ""),
                "publish_time_raw": item.get("date", ""),
                "category": "sector",
                "symbol_code": "",
                "_extracted_symbols": sorted(syms),
            })

        return results[:limit]

    # ── Time parsing ────────────────────────────────────────────

    @staticmethod
    def _parse_time(raw: str) -> str:
        if not raw:
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Try timestamp (seconds)
        if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.isdigit()):
            try:
                ts = int(raw)
                if ts > 1e12:  # milliseconds
                    ts //= 1000
                return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OSError):
                pass
        # Try common formats
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]:
            try:
                return datetime.strptime(str(raw), fmt).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Finalize ────────────────────────────────────────────────

    def _finalize(self, success_count: int, error_count: int, all_news: List[Dict[str, Any]]) -> None:
        if success_count > 0:
            self._mark_success(
                last_event_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                event_count=len(all_news),
            )
        else:
            self._mark_error(
                RuntimeError(f"全部 {success_count + error_count} 次抓取失败"),
                traceback.format_exc(),
            )
        logger.info(
            f"[EastMoney] 完成: {success_count}成功/{error_count}失败, 共 {len(all_news)} 条"
        )

    @staticmethod
    def _default_symbols() -> List[str]:
        return list(dict.fromkeys([
            "600519", "601318", "601398", "600036", "600900",
            "600276", "601012", "601899", "601166", "600000",
            "600030", "600050", "601088", "601857", "600309",
            "000001", "000002", "000333", "000651", "000858",
            "000568", "000725", "000063", "000776", "002415",
            "002475", "002594", "002230", "002714", "002142",
            "300059", "300750", "300015", "300124", "300308",
            "300760", "300274", "300502", "300394",
            "688981", "688111", "688036", "688012", "688008",
            "688256", "688041", "688120",
        ]))
