"""
tests/test_eastmoney_news_provider.py — EastMoney Phase 2 Recovery 验收测试
运行: python -m pytest tests/test_eastmoney_news_provider.py -v
所有测试默认使用 mock, 不访问真实网络。
"""
import os, sys, json, pytest
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

from feeds.eastmoney_news_provider import (
    EastMoneyNewsProvider, _resolve_symbol, _SEARCH_TYPES, _SECTOR_KEYWORDS
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def provider():
    return EastMoneyNewsProvider(max_pages=2, categories=["stock"])


@pytest.fixture
def full_provider():
    return EastMoneyNewsProvider(
        max_pages=3,
        categories=["stock", "announcement", "report", "flash", "sector"],
        request_delay_min=0.01,
        request_delay_max=0.02,
        sector_max_keywords=2,
    )


# ═══════════════════════════════════════════════════════════
# 1. Initialization & Config
# ═══════════════════════════════════════════════════════════

class TestInit:
    def test_default_init(self):
        p = EastMoneyNewsProvider()
        assert p.max_pages == 5
        assert p.categories == ["stock", "announcement", "report"]
        assert p.page_size == 20

    def test_custom_init(self):
        p = EastMoneyNewsProvider(max_pages=3, categories=["stock", "flash"],
                                  page_size=15, sector_max_keywords=5)
        assert p.max_pages == 3
        assert p.categories == ["stock", "flash"]
        assert p.page_size == 15
        assert p.sector_max_keywords == 5

    def test_delay_params(self):
        p = EastMoneyNewsProvider(request_delay_min=0.5, request_delay_max=1.5)
        assert p.request_delay_min == 0.5
        assert p.request_delay_max == 1.5

    def test_source_name(self):
        p = EastMoneyNewsProvider()
        assert p.source_name == "eastmoney"


# ═══════════════════════════════════════════════════════════
# 2. Symbol Resolution & Filtering
# ═══════════════════════════════════════════════════════════

class TestSymbolResolution:
    @pytest.mark.parametrize("code,expected", [
        ("600519.SH", "sh600519"),
        ("SH600519", "sh600519"),
        ("sh600519", "sh600519"),
        ("600519", "sh600519"),
        ("000001.SZ", "sz000001"),
        ("SZ000001", "sz000001"),
        ("sz000001", "sz000001"),
        ("000001", "sz000001"),
        ("300750.SZ", "sz300750"),
        ("688981.SH", "sh688981"),
        ("603259", "sh603259"),
        ("301123", "sz301123"),
    ])
    def test_valid_a_stock(self, code, expected):
        assert _resolve_symbol(code) == expected

    @pytest.mark.parametrize("code", [
        "159001", "510300", "512000", "513100", "588000", "589001",  # ETF
        "110000", "111000", "113001", "118001", "123000", "127000", "128000",  # CB
        "204001", "131810", "019000",  # repo
        "200001", "900901",  # B股
        "", None, "INVALID", "12345", "1234567",  # invalid
    ])
    def test_excluded_or_invalid(self, code):
        assert _resolve_symbol(code) is None

    def test_empty_list_not_market_overview(self):
        p = EastMoneyNewsProvider(categories=["stock"])
        # fetch_latest with empty symbols list
        n = p.normalize({"title": "test", "symbol_code": "", "_extracted_symbols": [],
                         "article_url": "", "publish_time_raw": "2026-01-01 00:00:00",
                         "category": "stock", "content": "x"})
        assert n is not None
        assert n["symbols"] == []


# ═══════════════════════════════════════════════════════════
# 3. Search Types
# ═══════════════════════════════════════════════════════════

class TestSearchTypes:
    def test_stock_type(self):
        assert _SEARCH_TYPES["stock"] == "cmsArticleWebOld"

    def test_announcement_type(self):
        assert _SEARCH_TYPES["announcement"] == "cmsAnnouncementWebOld"

    def test_report_type(self):
        assert _SEARCH_TYPES["report"] == "cmsReportWebOld"

    def test_news_fallback(self):
        assert _SEARCH_TYPES["news"] == "cmsArticleWebOld"


# ═══════════════════════════════════════════════════════════
# 4. Event Type Inference
# ═══════════════════════════════════════════════════════════

class TestEventType:
    @pytest.mark.parametrize("title,category,expected", [
        ("2025年年度报告全文", "announcement", "earnings"),
        ("退市风险警示公告", "announcement", "risk"),
        ("减持公告", "stock", "risk"),
        ("回购公告", "stock", "corporate_action"),
        ("中标公告", "stock", "contract"),
        ("战略合作协议", "stock", "partnership"),
        ("业绩预增150%", "stock", "earnings"),
        ("重组方案", "stock", "corporate_action"),
        ("市场行情分析", "stock", "news"),
        ("研报：目标价上调", "report", "research"),
    ])
    def test_event_type(self, title, category, expected):
        p = EastMoneyNewsProvider()
        assert p._infer_event_type(title, category) == expected


# ═══════════════════════════════════════════════════════════
# 5. Importance Score
# ═══════════════════════════════════════════════════════════

class TestImportance:
    def test_structure_fields(self):
        p = EastMoneyNewsProvider()
        detail = p._calculate_importance("测试标题", "内容", "news", "stock")
        for field in ["base_score", "keyword_score", "category_bonus", "content_bonus",
                       "type_bonus", "final_score", "importance", "matched_keywords"]:
            assert field in detail, f"Missing {field}"

    def test_announcement_category_bonus(self):
        p = EastMoneyNewsProvider()
        detail = p._calculate_importance("一般公告", "", "announcement", "announcement")
        assert detail["category_bonus"] >= 1

    def test_high_impact_keyword(self):
        p = EastMoneyNewsProvider()
        detail = p._calculate_importance("重大合同签约通知", "", "contract", "stock")
        assert detail["keyword_score"] >= 3

    def test_low_importance(self):
        p = EastMoneyNewsProvider()
        detail = p._calculate_importance("市场行情", "", "news", "stock")
        assert detail["importance"] == "LOW"

    def test_medium_importance(self):
        p = EastMoneyNewsProvider()
        # "业绩预告"=medium(+1) + earnings type_bonus(+1) + 长内容(+1) = 3 = MEDIUM
        detail = p._calculate_importance("业绩预告利润增长", "x" * 600, "earnings", "stock")
        assert detail["importance"] == "MEDIUM"

    def test_high_importance(self):
        p = EastMoneyNewsProvider()
        detail = p._calculate_importance("重大合同突破历史新高", "x" * 600, "contract", "announcement")
        assert detail["importance"] == "HIGH"


# ═══════════════════════════════════════════════════════════
# 6. Normalize Schema
# ═══════════════════════════════════════════════════════════

class TestNormalize:
    def test_canonical_fields(self, provider):
        raw = {
            "title": "测试新闻标题",
            "content": "测试内容",
            "article_url": "https://example.com",
            "publish_time_raw": "2026-01-01 10:00:00",
            "category": "stock",
            "symbol_code": "sh600519",
            "_extracted_symbols": ["sh600519"],
        }
        n = provider.normalize(raw)
        assert n is not None
        for field in ["id", "title", "source", "published_at", "url", "summary",
                       "symbols", "event_type", "importance", "importance_detail", "category"]:
            assert field in n, f"Missing {field}"
        assert n["source"] == "eastmoney"
        assert n["symbols"] == ["sh600519"]
        assert "category" in n

    def test_empty_title_returns_none(self, provider):
        assert provider.normalize({"title": ""}) is None

    def test_dedup_key_no_url(self, provider):
        n1 = provider.normalize({
            "title": "测试", "content": "x", "article_url": "",
            "publish_time_raw": "2026-01-01 00:00:00", "category": "stock",
            "symbol_code": "", "_extracted_symbols": [],
        })
        n2 = provider.normalize({
            "title": "测试", "content": "x", "article_url": "",
            "publish_time_raw": "2026-01-01 00:00:00", "category": "stock",
            "symbol_code": "", "_extracted_symbols": [],
        })
        assert n1 is not None and n2 is not None
        assert n1["event_id"] == n2["event_id"]  # same hash = dedup works

    def test_em_tag_cleaned(self, provider):
        n = provider.normalize({
            "title": "<em>重要</em>公告标题",
            "content": "<em>highlight</em>",
            "article_url": "", "publish_time_raw": "",
            "category": "stock", "symbol_code": "", "_extracted_symbols": [],
        })
        assert n is not None
        assert "<em>" not in n["title"]


# ═══════════════════════════════════════════════════════════
# 7. Flash Field Variants (mock)
# ═══════════════════════════════════════════════════════════

class TestFlashMock:
    def test_flash_field_fallback(self, full_provider):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "data": {
                    "list": [
                        {"Title": "快讯标题1", "url": "http://x", "showTime": "2026-01-01 10:00:00"},
                        {"title": "快讯标题2", "link": "http://y", "time": "2026-01-01 11:00:00"},
                    ]
                }
            }
            mock_get.return_value = mock_resp

            items = full_provider._fetch_flash_news(limit=5)
            assert len(items) == 2
            assert items[0]["title"] == "快讯标题1"
            assert items[0]["article_url"] == "http://x"
            assert items[0]["category"] == "flash"
            assert items[1]["title"] == "快讯标题2"


# ═══════════════════════════════════════════════════════════
# 8. Pagination Mock
# ═══════════════════════════════════════════════════════════

class TestPaginationMock:
    def test_totalcount_zero_stops(self, full_provider):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = 'jQuery_em({"totalCount":0,"result":{}})'
            mock_get.return_value = mock_resp

            items = full_provider._fetch_paginated("600519", "cmsArticleWebOld", "stock", 10)
            assert len(items) == 0
            # Only 1 request was made
            assert mock_get.call_count == 1

    def test_403_stops_but_keeps_previous(self, full_provider):
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                r = MagicMock()
                r.status_code = 200
                r.text = 'jQuery_em({"totalCount":5,"result":{"cmsArticleWebOld":[{"title":"page1","url":"","date":"","content":""}]}});'
                return r
            else:
                r = MagicMock()
                r.status_code = 403
                return r

        with patch("requests.get", side_effect=side_effect):
            with patch("time.sleep"):
                items = full_provider._fetch_paginated("600519", "cmsArticleWebOld", "stock", 10)
                assert len(items) == 1
                assert items[0]["title"] == "page1"

    def test_empty_page_stops(self, full_provider):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = 'jQuery_em({"totalCount":10,"result":{"cmsArticleWebOld":[]}});'
            mock_get.return_value = mock_resp

            with patch("time.sleep"):
                items = full_provider._fetch_paginated("600519", "cmsArticleWebOld", "stock", 10)
                assert len(items) == 0


# ═══════════════════════════════════════════════════════════
# 9. Sector Keywords
# ═══════════════════════════════════════════════════════════

class TestSector:
    def test_keyword_pool_not_empty(self):
        assert len(_SECTOR_KEYWORDS) >= 10

    def test_sector_max_keywords(self, full_provider):
        assert full_provider.sector_max_keywords == 2


# ═══════════════════════════════════════════════════════════
# 10. Time Parsing
# ═══════════════════════════════════════════════════════════

class TestTimeParsing:
    def test_empty_raw(self):
        result = EastMoneyNewsProvider._parse_time("")
        assert "2026" in result  # defaults to now

    def test_unix_timestamp_seconds(self):
        result = EastMoneyNewsProvider._parse_time("1700000000")
        assert "2023" in result

    def test_datetime_string(self):
        result = EastMoneyNewsProvider._parse_time("2026-01-15 10:30:00")
        assert "2026-01-15 10:30:00" == result
