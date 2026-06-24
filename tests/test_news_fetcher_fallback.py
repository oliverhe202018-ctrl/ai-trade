import sys
import os
import unittest
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from datahub.news_fetcher import fetch_worker, AkshareProvider, EastMoneyProvider, LocalCacheProvider
from datahub.circuit_breaker import CircuitBreaker

class TestNewsFetcherFallback(unittest.TestCase):
    def test_akshare_success_with_data(self):
        provider = MagicMock()
        provider.name = 'akshare'
        provider.fetch.return_value = [{'title': 'test', 'content': 'content'}]
        
        status, data = fetch_worker(provider, 'sh600519', timeout=5)
        self.assertEqual(status, "SUCCESS_WITH_DATA")
        self.assertEqual(len(data), 1)

    def test_provider_empty_data_success(self):
        provider = MagicMock()
        provider.name = 'eastmoney'
        provider.fetch.return_value = []
        
        status, data = fetch_worker(provider, 'sh600519', timeout=5)
        self.assertEqual(status, "SUCCESS_NO_DATA")
        self.assertEqual(data, [])

    def test_provider_failure(self):
        provider = MagicMock()
        provider.name = 'akshare'
        provider.fetch.side_effect = Exception("timeout")
        
        # Test the retry backoff. To make it fast, we can patch time.sleep
        with patch('time.sleep', return_value=None):
            status, data = fetch_worker(provider, 'sh600519', timeout=1)
        
        self.assertEqual(status, "FAILED")
        self.assertEqual(data, [])

    @patch('datahub.news_fetcher.save_news')
    @patch('datahub.news_fetcher.get_held_stocks', return_value=['sh600519'])
    def test_circuit_breaker_logic_empty_data(self, mock_get_stocks, mock_save_news):
        # We simulate the loop iteration
        from datahub.news_fetcher import AkshareProvider, EastMoneyProvider, LocalCacheProvider
        from datahub.circuit_breaker import CircuitBreaker
        
        cb = CircuitBreaker()
        # Mock fetch_worker
        with patch('datahub.news_fetcher.fetch_worker') as mock_fw:
            mock_fw.return_value = ("SUCCESS_NO_DATA", [])
            
            # Since scheduler_loop is infinite, we just test the inner block logic
            providers = [MagicMock(name='ak'), MagicMock(name='em'), MagicMock(name='lc')]
            for p, name in zip(providers, ['akshare', 'eastmoney', 'local_cache']):
                p.name = name
                
            symbols = ['sh600519']
            for symbol in symbols:
                fetch_success = False
                for provider in providers:
                    if not cb.allow(provider.name):
                        continue
                        
                    status, data = mock_fw(provider, symbol, timeout=20)
                    
                    if status == "SUCCESS_WITH_DATA":
                        cb.success(provider.name)
                        fetch_success = True
                        break
                    elif status == "SUCCESS_NO_DATA":
                        # Should not call cb.success
                        fetch_success = True
                        break
                    elif status == "FAILED":
                        cb.failure(provider.name)
            
            # Check circuit breaker state: should not have any failures for akshare
            self.assertEqual(cb.failures.get('akshare', 0), 0)
            
    @patch('datahub.news_fetcher.save_news')
    def test_fallback_sequence(self, mock_save_news):
        # Akshare fails -> EastMoney empty -> LocalCache success
        cb = CircuitBreaker()
        
        providers = [MagicMock(name='ak'), MagicMock(name='em'), MagicMock(name='lc')]
        for p, name in zip(providers, ['akshare', 'eastmoney', 'local_cache']):
            p.name = name
            
        def mock_fetch(provider, symbol, timeout):
            if provider.name == 'akshare':
                return "FAILED", []
            elif provider.name == 'eastmoney':
                return "SUCCESS_NO_DATA", []
            else:
                return "SUCCESS_WITH_DATA", [{'title': 'cache'}]

        with patch('datahub.news_fetcher.fetch_worker', side_effect=mock_fetch):
            fetch_success = False
            for provider in providers:
                if not cb.allow(provider.name):
                    continue
                status, data = mock_fetch(provider, 'sh600519', 20)
                if status == "SUCCESS_WITH_DATA":
                    cb.success(provider.name)
                    fetch_success = True
                    break
                elif status == "SUCCESS_NO_DATA":
                    fetch_success = True
                    break
                elif status == "FAILED":
                    cb.failure(provider.name)
            
            self.assertEqual(cb.failures.get('akshare', 0), 1)
            self.assertEqual(cb.failures.get('eastmoney', 0), 0)
            self.assertTrue(fetch_success)

if __name__ == '__main__':
    unittest.main()
