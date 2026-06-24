import sys, os
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import time
import logging
import sqlite3
import threading
import concurrent.futures
from abc import ABC, abstractmethod
from datetime import datetime

from datahub.storage import get_db_conn, init_db
from datahub.circuit_breaker import CircuitBreaker

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class NewsProvider(ABC):
    def __init__(self, name):
        self.name = name

    @abstractmethod
    def fetch(self, symbol):
        pass

class AkshareProvider(NewsProvider):
    def __init__(self):
        super().__init__('akshare')
        self.last_call_time = 0
        self.rate_limit_delay = 1.0

    def fetch(self, symbol):
        import akshare as ak
        
        current_time = time.time()
        time_since_last_call = current_time - self.last_call_time
        if time_since_last_call < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - time_since_last_call)
            
        self.last_call_time = time.time()
        
        try:
            df = ak.stock_news_em(symbol=symbol)
            if df is None or df.empty:
                return []
            
            results = []
            for _, row in df.iterrows():
                results.append({
                    'title': row.get('新闻标题', ''),
                    'content': row.get('新闻内容', ''),
                    'publish_time': row.get('发布时间', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                    'raw_data': row.to_json()
                })
            return results
        except Exception as e:
            logger.error(f"Error fetching data from akshare for {symbol}: {e}")
            raise

def save_news(symbol, provider_name, news_items):
    with get_db_conn() as conn:
        cursor = conn.cursor()
        for item in news_items:
            try:
                cursor.execute('''
                    INSERT INTO raw_news (symbol, title, content, publish_time, provider, raw_data)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    symbol, 
                    item['title'], 
                    item.get('content', ''), 
                    item['publish_time'], 
                    provider_name,
                    item.get('raw_data', '{}')
                ))
            except sqlite3.IntegrityError:
                pass
        conn.commit()

def fetch_worker(provider, symbol, timeout=15):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(provider.fetch, symbol)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.error(f"Timeout fetching from {provider.name} for {symbol}")
            raise
        except Exception as e:
            logger.error(f"Exception fetching from {provider.name} for {symbol}: {e}")
            raise

def get_held_stocks():
    return ["600519", "000858", "000001"]

def scheduler_loop():
    init_db()
    providers = [AkshareProvider()]
    circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout_minutes=5)
    
    logger.info("Starting scheduler loop...")
    
    while True:
        try:
            symbols = get_held_stocks()
            for symbol in symbols:
                for provider in providers:
                    if not circuit_breaker.allow(provider.name):
                        logger.warning(f"Circuit breaker open for {provider.name}, skipping.")
                        continue
                        
                    try:
                        news_items = fetch_worker(provider, symbol, timeout=20)
                        circuit_breaker.success(provider.name)
                        if news_items:
                            save_news(symbol, provider.name, news_items)
                            logger.info(f"Saved {len(news_items)} news items for {symbol} from {provider.name}.")
                    except Exception as e:
                        circuit_breaker.failure(provider.name)
                        logger.error(f"Fetch failed for {provider.name}: {e}")
            
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")
            
        logger.info("Sleeping for 3 minutes...")
        time.sleep(180)

if __name__ == '__main__':
    scheduler_loop()
