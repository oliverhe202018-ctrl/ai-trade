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
        from datahub.news_fetcher import AkshareProvider, EastMoneyProvider, LocalCacheProvider, process_symbol_with_providers
        from datahub.circuit_breaker import CircuitBreaker
        
        cb = CircuitBreaker()
        with patch('datahub.news_fetcher.fetch_worker') as mock_fw:
            mock_fw.return_value = ("SUCCESS_NO_DATA", [])
            
            providers = [MagicMock(name='ak'), MagicMock(name='em'), MagicMock(name='lc')]
            for p, name in zip(providers, ['akshare', 'eastmoney', 'local_cache']):
                p.name = name
                
            symbols = ['sh600519']
            for symbol in symbols:
                process_symbol_with_providers(symbol, providers, cb)
            
            self.assertEqual(cb.failures.get('akshare', 0), 0)

    @patch('datahub.news_fetcher.save_news')
    def test_fallback_sequence(self, mock_save_news):
        from datahub.news_fetcher import process_symbol_with_providers
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
            fetch_success = process_symbol_with_providers('sh600519', providers, cb)
            
            self.assertEqual(cb.failures.get('akshare', 0), 1)
            self.assertEqual(cb.failures.get('eastmoney', 0), 0)
            self.assertTrue(fetch_success)

    @patch('datahub.news_fetcher.save_news')
    def test_akshare_failed_eastmoney_success(self, mock_save_news):
        from datahub.news_fetcher import process_symbol_with_providers
        cb = CircuitBreaker()
        providers = [MagicMock(name='ak'), MagicMock(name='em'), MagicMock(name='lc')]
        for p, name in zip(providers, ['akshare', 'eastmoney', 'local_cache']):
            p.name = name
            
        def mock_fetch(provider, symbol, timeout):
            if provider.name == 'akshare':
                return "FAILED", []
            elif provider.name == 'eastmoney':
                return "SUCCESS_WITH_DATA", [{'title': 'test'}]
            else:
                return "SUCCESS_WITH_DATA", [{'title': 'cache'}]

        with patch('datahub.news_fetcher.fetch_worker', side_effect=mock_fetch):
            fetch_success = process_symbol_with_providers('sh600519', providers, cb)
            
            mock_save_news.assert_called_once_with('sh600519', 'eastmoney', [{'title': 'test'}])
            self.assertEqual(cb.failures.get('akshare', 0), 1)
            self.assertTrue(fetch_success)

    @patch('datahub.news_fetcher.save_news')
    def test_success_no_data_continues_to_local_cache(self, mock_save_news):
        from datahub.news_fetcher import process_symbol_with_providers
        cb = CircuitBreaker()
        providers = [MagicMock(name='ak'), MagicMock(name='em'), MagicMock(name='lc')]
        for p, name in zip(providers, ['akshare', 'eastmoney', 'local_cache']):
            p.name = name
            
        def mock_fetch(provider, symbol, timeout):
            if provider.name == 'akshare':
                return "SUCCESS_NO_DATA", []
            elif provider.name == 'eastmoney':
                return "SUCCESS_NO_DATA", []
            else:
                return "SUCCESS_WITH_DATA", [{'title': 'cache'}]

        with patch('datahub.news_fetcher.fetch_worker', side_effect=mock_fetch):
            fetch_success = process_symbol_with_providers('sh600519', providers, cb)
                    
            mock_save_news.assert_called_once_with('sh600519', 'local_cache', [{'title': 'cache'}])
            self.assertTrue(fetch_success)
            self.assertEqual(cb.failures.get('akshare', 0), 0)
            self.assertEqual(cb.failures.get('eastmoney', 0), 0)

    @patch('datahub.news_fetcher.save_news')
    def test_all_providers_no_data_degraded(self, mock_save_news):
        from datahub.news_fetcher import process_symbol_with_providers
        cb = CircuitBreaker()
        providers = [MagicMock(name='ak'), MagicMock(name='em'), MagicMock(name='lc')]
        for p, name in zip(providers, ['akshare', 'eastmoney', 'local_cache']):
            p.name = name
            
        def mock_fetch(provider, symbol, timeout):
            return "SUCCESS_NO_DATA", []

        with patch('datahub.news_fetcher.fetch_worker', side_effect=mock_fetch):
            fetch_success = process_symbol_with_providers('sh600519', providers, cb)
                    
            mock_save_news.assert_not_called()
            self.assertFalse(fetch_success)

    def test_provider_output_missing_required_fields(self):
        # We test how save_news handles missing required fields.
        import sqlite3
        from contextlib import contextmanager
        from datahub.news_fetcher import save_news
        
        import tempfile
        import os
        
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, 'test_missing.db')
        
        @contextmanager
        def get_test_db():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()
            
        with patch('datahub.news_fetcher.get_db_conn', side_effect=get_test_db):
            with get_test_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE raw_news (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT,
                        content_hash TEXT,
                        publish_time DATETIME NOT NULL,
                        provider TEXT NOT NULL,
                        raw_data TEXT,
                        status TEXT DEFAULT 'raw',
                        source_url TEXT DEFAULT '',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(symbol, content_hash)
                    )
                ''')
                conn.commit()
            
            # This missing field scenario should not throw unhandled exception or insert NULL for non-null constraint
            bad_data = [{'content': 'some content'}] # missing title, publish_time
            save_news('sh123', 'test_provider', bad_data)
            
            with get_test_db() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT count(*) FROM raw_news')
                count = cursor.fetchone()[0]
                self.assertEqual(count, 1)

    def test_raw_news_status_migration_and_extractor_consumption(self):
        import sqlite3
        from contextlib import contextmanager
        from datahub.storage import init_db
        import datahub.storage
        import tempfile
        import os
        
        # Setup temporary sqlite database
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, 'test_migration.db')
        
        @contextmanager
        def get_test_db():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

        with patch('datahub.storage.get_db_conn', side_effect=get_test_db):
            with get_test_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE raw_news (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        title TEXT NOT NULL,
                        content TEXT,
                        content_hash TEXT,
                        publish_time DATETIME NOT NULL,
                        provider TEXT NOT NULL,
                        raw_data TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(symbol, content_hash)
                    )
                ''')
                conn.commit()
                
            # Run init_db to trigger migration
            init_db()
            
            # Check if status column exists
            with get_test_db() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(raw_news)")
                columns = {row['name'] for row in cursor.fetchall()}
                self.assertIn("status", columns)
                self.assertIn("source_url", columns)

if __name__ == '__main__':
    unittest.main()
