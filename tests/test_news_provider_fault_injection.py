import os
import sys
import unittest
from unittest.mock import patch, MagicMock
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feeds.cninfo_news_provider import CninfoNewsProvider
from feeds.cls_news_provider import ClsNewsProvider

class TestNewsProviderFaultInjection(unittest.TestCase):
    
    @patch("requests.post")
    def test_scenario_a_cninfo_timeout(self, mock_post):
        """场景 A：CNINFO 请求超时"""
        mock_post.side_effect = requests.exceptions.Timeout("Mocked timeout")
        provider = CninfoNewsProvider()
        
        # 不应崩溃，应返回空列表
        result = provider.fetch_latest()
        self.assertEqual(result, [])
        
        # 健康度应下降为 DOWN，且 last_error 应包含 traceback
        health = provider.health_check()
        self.assertEqual(health["status"], "DOWN")
        self.assertIn("Mocked timeout", health["last_error"])
        
    @patch("requests.post")
    def test_scenario_a_cninfo_invalid_json(self, mock_post):
        """场景 A：CNINFO 返回缺失字段"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"error": "no announcements field"}
        mock_post.return_value = mock_response
        
        provider = CninfoNewsProvider()
        result = provider.fetch_latest()
        self.assertEqual(result, [])
        self.assertEqual(provider.health_check()["status"], "DOWN")
        self.assertIn("缺少 announcements", provider.health_check()["last_error"])
        
    @patch("requests.get")
    def test_scenario_b_cls_403(self, mock_get):
        """场景 B：CLS 请求 403 拦截"""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_get.side_effect = requests.exceptions.HTTPError("403 Forbidden", response=mock_response)
        
        provider = ClsNewsProvider()
        result = provider.fetch_latest()
        
        self.assertEqual(result, [])
        health = provider.health_check()
        self.assertEqual(health["status"], "DOWN")
        
    @patch("requests.get")
    def test_scenario_b_cls_invalid_structure(self, mock_get):
        """场景 B：CLS 返回字段结构变化"""
        mock_response = MagicMock()
        # 缺少 roll_data
        mock_response.json.return_value = {"data": {"other_data": []}}
        mock_get.return_value = mock_response
        
        provider = ClsNewsProvider()
        result = provider.fetch_latest()
        
        self.assertEqual(result, [])
        health = provider.health_check()
        self.assertEqual(health["status"], "DOWN")
        self.assertIn("缺少 data.roll_data", health["last_error"])

if __name__ == "__main__":
    unittest.main()
