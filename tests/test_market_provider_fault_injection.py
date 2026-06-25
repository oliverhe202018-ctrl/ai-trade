import os
import sys
import time
import json
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feeds.base_market_provider import MarketDataError
from feeds.qmt_market_provider import QMTMarketProvider

class TestMarketProviderFaultInjection(unittest.TestCase):
    
    def test_scenario_a_qmt_missing(self):
        """场景 A：QMT / xtdata 不存在"""
        with patch("builtins.__import__", side_effect=ImportError("Mocked missing xtquant")):
            with self.assertRaises(MarketDataError) as ctx:
                provider = QMTMarketProvider()
            self.assertIn("xtquant", str(ctx.exception))
            
    def test_scenario_b_get_full_tick_exception(self):
        """场景 B：get_full_tick 抛异常"""
        provider = QMTMarketProvider()
        provider.xtdata = MagicMock()
        provider.xtdata.get_full_tick.side_effect = RuntimeError("Mocked QMT Timeout")
        
        health = provider.health_check()
        self.assertEqual(health["status"], "DOWN")
        self.assertIn("last_error", health)
        self.assertIn("Mocked QMT Timeout", health["last_error"])
        
    def test_scenario_c_timestamp_stale(self):
        """场景 C：行情 timestamp 超时"""
        provider = QMTMarketProvider()
        stale_ts = time.time() - 300 # 5 分钟前
        
        def mock_get_quote(symbol):
            return {"symbol": symbol, "price": 10.0, "timestamp": stale_ts}
            
        with patch.object(provider, "get_realtime_quote", side_effect=mock_get_quote):
            is_fresh = provider.is_data_fresh("sh600000", max_delay_seconds=5)
            self.assertFalse(is_fresh)
            
    def test_scenario_d_missing_fields(self):
        """场景 D：行情字段缺失 - 在 live_trader 中测试更多，此处测试 Provider容错"""
        provider = QMTMarketProvider()
        provider.xtdata = MagicMock()
        # Mock tick missing volume and amount
        provider.xtdata.get_full_tick.return_value = {"600000.SH": {"lastPrice": 10.0}}
        tick = provider.get_realtime_quote("sh600000")
        self.assertEqual(tick["volume"], 0)
        self.assertEqual(tick["amount"], 0.0)
        
    def test_scenario_e_health_json_atomic(self):
        """场景 E：health JSON 损坏或写入中断"""
        import tempfile
        import os
        from core.dashboard import sidebar_status_panel
        
        # 验证原子写入逻辑存在于 brain_node，此处验证 Dashboard 处理
        PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        health_file_path = os.path.join(PROJECT_ROOT, "data_cache", "market_health.json")
        os.makedirs(os.path.dirname(health_file_path), exist_ok=True)
        
        # 写入损坏的 JSON
        with open(health_file_path, "w", encoding="utf-8") as f:
            f.write("{invalid json")
            
        # Mock streamlit 验证不崩溃
        with patch("core.dashboard.st") as mock_st:
            sidebar_status_panel()
            # 应该调用了 st.sidebar.markdown("**主数据源**: UNKNOWN") 等等
            mock_st.sidebar.markdown.assert_any_call("**主数据源**: UNKNOWN")

if __name__ == "__main__":
    unittest.main()
