"""
tests/test_news_provider_safety.py — 新闻 Provider 安全隔离测试
运行: python -m pytest tests/test_news_provider_safety.py -v
"""
import os, sys, re, pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

FORBIDDEN_IMPORTS = ["live_trader", "brain_node", "broker_adapter", "trading_state"]
PROVIDER_FILES = [
    "feeds/cninfo_news_provider.py",
    "feeds/eastmoney_news_provider.py",
    "feeds/sse_news_provider.py",
    "feeds/szse_news_provider.py",
    "feeds/rss_news_provider.py",
    "feeds/cls_news_provider.py",
]


class TestNewsProviderSafety:
    """验证所有新闻 Provider 不导入交易相关模块"""

    @pytest.mark.parametrize("file_path", PROVIDER_FILES)
    def test_no_trade_imports(self, file_path):
        full = os.path.join(PROJECT_ROOT, file_path)
        with open(full, "r", encoding="utf-8") as f:
            content = f.read()
        for fb in FORBIDDEN_IMPORTS:
            found = re.search(rf'from.*{fb}|import.*{fb}', content)
            assert not found, f"{file_path} imports forbidden module '{fb}'"

    def test_allow_trade_trigger_false(self):
        import yaml
        with open(os.path.join(PROJECT_ROOT, "config", "config.yaml"), "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert cfg["news_data"]["allow_trade_trigger"] is False

    def test_allow_state_mutation_false(self):
        import yaml
        with open(os.path.join(PROJECT_ROOT, "config", "config.yaml"), "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert cfg["news_data"]["allow_state_mutation"] is False

    def test_readonly_true(self):
        import yaml
        with open(os.path.join(PROJECT_ROOT, "config", "config.yaml"), "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert cfg["news_data"]["readonly"] is True
