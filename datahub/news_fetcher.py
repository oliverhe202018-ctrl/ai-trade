import sys, os
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import time
import hashlib
import logging
import sqlite3
import threading
import concurrent.futures
from abc import ABC, abstractmethod
from datetime import datetime

from datahub.storage import get_db_conn, init_db
from datahub.circuit_breaker import CircuitBreaker
from core.state_manager import load_portfolio

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

class EastMoneyProvider(NewsProvider):
    def __init__(self):
        super().__init__('eastmoney')

    def fetch(self, symbol):
        # EastMoney specific fetcher logic (fallback)
        logger.info(f"Fetching from EastMoney API directly for {symbol}")
        # Assuming minimal fallback logic or returning empty to avoid crash if not implemented
        return []

def save_news(symbol, provider_name, news_items):
    import re
    def _normalize(text):
        text = text or ""
        text = re.sub(r'<[^>]+>', '', text)
        return re.sub(r'\s+', ' ', text).strip()
        
    with get_db_conn() as conn:
        cursor = conn.cursor()
        for item in news_items:
            try:
                title = item.get('title', '').strip()
                content = item.get('content', '').strip()
                publish_time = item.get('publish_time', '')
                
                if content:
                    norm = _normalize(content)
                    content_hash = hashlib.md5(norm.encode('utf-8')).hexdigest()
                else:
                    logger.warning(f"[WEAK_CONTENT_HASH] symbol={symbol} title='{title}'")
                    weak_str = f"{title}_{publish_time}_{provider_name}"
                    content_hash = hashlib.md5(weak_str.encode('utf-8')).hexdigest()
                
                cursor.execute('''
                    INSERT INTO raw_news (symbol, title, content, content_hash, publish_time, provider, raw_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    symbol, 
                    title, 
                    content, 
                    content_hash,
                    publish_time, 
                    provider_name,
                    item.get('raw_data', '{}')
                ))
            except sqlite3.IntegrityError as e:
                logger.info(f"Hash conflict or duplicate skipped for symbol={symbol}, title={item.get('title')}: {e}")
                pass
        conn.commit()

def fetch_worker(provider, symbol, timeout=15):
    max_retries = 3
    for attempt in range(max_retries):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(provider.fetch, symbol)
            try:
                return future.result(timeout=timeout)
            except Exception as e:
                backoff_time = min(60 * (2 ** attempt), 600)
                logger.warning(f"[BACKOFF] Attempt {attempt+1}/{max_retries} failed for {provider.name} on {symbol}: {e}. Retrying in {backoff_time}s.")
                if attempt < max_retries - 1:
                    time.sleep(backoff_time)
                else:
                    raise

def get_held_stocks():
    default_watchlist = ["600519", "000858", "000001"]
    try:
        portfolio = load_portfolio()
        if portfolio and "positions" in portfolio and portfolio["positions"]:
            live_stocks = list(portfolio["positions"].keys())
            logger.info(f"[HELD_STOCKS_FROM_PORTFOLIO] 成功挂载真实持仓池，共 {len(live_stocks)} 只标的")
            return live_stocks
        else:
            logger.warning("[HELD_STOCKS_FALLBACK] 真实持仓为空，退化降级至系统级后备观察名单")
            return default_watchlist
    except Exception as e:
        logger.error(f"[HELD_STOCKS_FALLBACK] 加载真实持仓发生异常: {e}，强制启用安全后备名单")
        return default_watchlist

def scheduler_loop():
    init_db()
    import random
    primary_provider = AkshareProvider()
    fallback_provider = EastMoneyProvider()
    circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout_minutes=5)
    
    logger.info("Starting scheduler loop...")
    
    while True:
        try:
            symbols = get_held_stocks()
            for symbol in symbols:
                current_provider = primary_provider
                
                if not circuit_breaker.allow(current_provider.name):
                    logger.warning(f"[PROVIDER_FAILOVER] Circuit breaker open for {current_provider.name}, falling back to {fallback_provider.name}.")
                    current_provider = fallback_provider
                    if not circuit_breaker.allow(current_provider.name):
                        logger.warning(f"Circuit breaker open for fallback {current_provider.name} too, skipping.")
                        continue
                        
                try:
                    news_items = fetch_worker(current_provider, symbol, timeout=20)
                    circuit_breaker.success(current_provider.name)
                    if news_items:
                        save_news(symbol, current_provider.name, news_items)
                        logger.info(f"Saved {len(news_items)} news items for {symbol} from {current_provider.name}.")
                except Exception as e:
                    circuit_breaker.failure(current_provider.name)
                    logger.error(f"Fetch failed for {current_provider.name}: {e}")
                    
                    if current_provider == primary_provider:
                        logger.info(f"[PROVIDER_FAILOVER] Immediate fallback to {fallback_provider.name} after primary failure.")
                        try:
                            if circuit_breaker.allow(fallback_provider.name):
                                news_items = fetch_worker(fallback_provider, symbol, timeout=20)
                                circuit_breaker.success(fallback_provider.name)
                                if news_items:
                                    save_news(symbol, fallback_provider.name, news_items)
                        except Exception as fb_e:
                            circuit_breaker.failure(fallback_provider.name)
                            logger.error(f"Fallback fetch failed: {fb_e}")
            
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")
            
        base_sleep = 180
        jitter = random.uniform(10, 60)
        sleep_time = base_sleep + jitter
        logger.info(f"Sleeping for {sleep_time:.2f} seconds...")
        time.sleep(sleep_time)

if __name__ == '__main__':
    scheduler_loop()
