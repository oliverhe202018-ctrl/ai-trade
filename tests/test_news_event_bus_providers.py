"""
tests/test_news_event_bus_providers.py — NewsEventBus 全量 Provider 集成测试
运行: python -m pytest tests/test_news_event_bus_providers.py -v
"""
import os, sys, pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

import yaml
from feeds.news_event_bus import NewsEventBus
from feeds.cninfo_news_provider import CninfoNewsProvider
from feeds.rss_news_provider import RssNewsProvider
from feeds.sse_news_provider import SseNewsProvider
from feeds.szse_news_provider import SzseNewsProvider
from feeds.eastmoney_news_provider import EastMoneyNewsProvider
from feeds.cls_news_provider import ClsNewsProvider


@pytest.fixture
def cfg():
    with open(os.path.join(PROJECT_ROOT, "config", "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture
def bus(cfg):
    b = NewsEventBus()
    b.initialize_from_config(cfg)
    return b


class TestNewsEventBusProviders:
    """测试 NewsEventBus 正确注册所有 6 个 Provider"""

    CLASS_MAP = {
        "cninfo": CninfoNewsProvider,
        "eastmoney": EastMoneyNewsProvider,
        "sse": SseNewsProvider,
        "szse": SzseNewsProvider,
        "rss": RssNewsProvider,
        "cls": ClsNewsProvider,
    }

    def test_all_six_registered(self, bus):
        assert len(bus.providers) == 6, f"Expected 6 providers, got {len(bus.providers)}"

    @pytest.mark.parametrize("name", ["cninfo","eastmoney","sse","szse","rss","cls"])
    def test_provider_registered(self, bus, name):
        assert name in bus.providers, f"{name} not registered"

    @pytest.mark.parametrize("name", ["cninfo","eastmoney","sse","szse","rss","cls"])
    def test_provider_correct_class(self, bus, name):
        assert isinstance(bus.providers[name], self.CLASS_MAP[name])

    def test_cninfo_config_params(self, bus):
        cninfo = bus.providers["cninfo"]
        assert cninfo.max_pages == 5
        assert cninfo.recent_hours == 24

    def test_rss_config_params(self, bus):
        rss = bus.providers["rss"]
        assert len(rss.feeds) == 2
        assert rss.feeds[0]["weight"] == 1.0
        assert rss.feeds[1]["weight"] == 0.8

    def test_all_providers_have_rate_limit(self, bus):
        for name in ["cninfo","sse","szse","rss"]:
            assert hasattr(bus.providers[name], "_rate_limit_wait"), f"{name} missing _rate_limit_wait"

    def test_all_providers_have_event_type(self, bus):
        for name in ["cninfo","sse","szse","rss"]:
            assert hasattr(bus.providers[name], "_infer_event_type"), f"{name} missing _infer_event_type"

    def test_cninfo_disabled(self):
        cfg_disabled = {"news_data": {"enabled": True, "providers": {"cninfo": {"enabled": False}}}}
        b = NewsEventBus()
        b.initialize_from_config(cfg_disabled)
        assert "cninfo" not in b.providers
