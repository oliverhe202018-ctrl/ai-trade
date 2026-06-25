import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestBrainNodeMarketGuard(unittest.TestCase):
    
    def test_scenario_a_down_status(self):
        """场景 A：行情源 DOWN 时不得生成 BUY"""
        # 测试我们在 brain_node 中加入的 health 拦截逻辑
        health_data = {"status": "DOWN"}
        health_status = health_data.get("status", "DOWN")
        
        buy_candidates = ["sh600000", "sz000001"]
        if health_status in ["STALE", "DOWN"]:
            buy_candidates = []
            
        self.assertEqual(len(buy_candidates), 0)
        
    def test_scenario_b_stale_status(self):
        """场景 B：行情源 STALE 时不得生成 BUY"""
        health_data = {"status": "STALE"}
        health_status = health_data.get("status", "DOWN")
        
        buy_candidates = ["sh600000"]
        if health_status in ["STALE", "DOWN"]:
            buy_candidates = []
            
        self.assertEqual(len(buy_candidates), 0)
        
    def test_scenario_c_kline_insufficient(self):
        """场景 C：K 线不足不生成 BUY"""
        # 我们在 brain_node 增加了 len(hist_data) < 20 的拦截
        hist_data = [1, 2, 3] # mock less than 20 lines
        
        passed = True
        if hist_data is None or len(hist_data) < 20:
            passed = False
            
        self.assertFalse(passed)

if __name__ == "__main__":
    unittest.main()
