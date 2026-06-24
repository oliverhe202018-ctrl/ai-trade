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
                    'symbol': symbol,
                    'title': row.get('新闻标题', ''),
                    'content': row.get('新闻内容', ''),
                    'publish_time': row.get('发布时间', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                    'provider': self.name,
                    'source_url': row.get('文章链接', ''),
                    'raw_data': row.to_json(force_ascii=False)
                })
            return results
        except Exception as e:
            logger.error(f"Error fetching data from akshare for {symbol}: {e}")
            raise

class EastMoneyProvider(NewsProvider):
    def __init__(self):
        super().__init__('eastmoney')

    def fetch(self, symbol):
        import requests
        import json
        import re
        from datetime import datetime
        
        logger.info(f"Fetching from EastMoney API directly for {symbol}")
        url = "https://search-api-web.eastmoney.com/search/jsonp"
        inner_param = {
            "uid": "",
            "keyword": symbol,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": 10,
                    "preTag": "<em>",
                    "postTag": "</em>",
                }
            },
        }
        params = {
            "cb": "jQuery123456",
            "param": json.dumps(inner_param, ensure_ascii=False),
            "_": str(int(time.time() * 1000)),
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": f"https://so.eastmoney.com/news/s?keyword={symbol}"
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        
        text = response.text
        match = re.search(r'jQuery123456\((.*)\)', text)
        if not match:
            raise ValueError("Invalid JSONP response")
            
        data = json.loads(match.group(1))
        articles = data.get("result", {}).get("cmsArticleWebOld", [])
        
        results = []
        for row in articles:
            title = re.sub(r'</?em>', '', row.get("title", ""))
            content = re.sub(r'</?em>', '', row.get("content", ""))
            content = content.replace('\u3000', '').replace('\r\n', ' ')
            
            results.append({
                'symbol': symbol,
                'title': title,
                'content': content,
                'publish_time': row.get("date", datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                'provider': self.name,
                'source_url': row.get("url", ""),
                'raw_data': json.dumps(row, ensure_ascii=False)
            })
        
        return results

class LocalCacheProvider(NewsProvider):
    def __init__(self):
        super().__init__('local_cache')

    def fetch(self, symbol):
        from datahub.storage import get_db_conn
        from datetime import datetime, timedelta
        
        logger.info(f"[NEWS_FETCH_DEGRADED] Fallback to LocalCacheProvider for {symbol}. Reading local cache.")
        results = []
        try:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                # 仅读取 24 小时内的新鲜数据
                cutoff_time = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
                
                cursor.execute('''
                    SELECT title, content, publish_time, raw_data, source_url 
                    FROM raw_news 
                    WHERE symbol = ? AND publish_time >= ?
                    ORDER BY publish_time DESC LIMIT 10
                ''', (symbol, cutoff_time))
                
                rows = cursor.fetchall()
                for row in rows:
                    results.append({
                        'symbol': symbol,
                        'title': row[0],
                        'content': row[1],
                        'publish_time': row[2],
                        'provider': self.name,
                        'source_url': row[4] if len(row) > 4 else '',
                        'raw_data': row[3]
                    })
        except Exception as e:
            logger.error(f"Error reading from LocalCacheProvider for {symbol}: {e}")
            
        return results

def save_news(symbol, provider_name, news_items):
    import re
    import json
    def _normalize(text):
        text = text or ""
        text = re.sub(r'<[^>]+>', '', text)
        return re.sub(r'\s+', ' ', text).strip()
        
    def extract_source_url(item):
        if item.get("source_url"):
            return item["source_url"]
        raw_data = item.get("raw_data")
        if raw_data:
            if isinstance(raw_data, str):
                try:
                    data = json.loads(raw_data)
                except Exception:
                    data = {}
            elif isinstance(raw_data, dict):
                data = raw_data
            else:
                data = {}
                
            if isinstance(data, dict):
                for key in ['url', 'source_url', 'link', '新闻链接']:
                    if key in data and data.get(key):
                        return str(data[key])
        return ""

    with get_db_conn() as conn:
        cursor = conn.cursor()
        for item in news_items:
            try:
                title = item.get('title', '').strip()
                content = item.get('content', '').strip()
                publish_time = item.get('publish_time', '')
                source_url = extract_source_url(item)
                
                if content:
                    norm = _normalize(content)
                    content_hash = hashlib.md5(norm.encode('utf-8')).hexdigest()
                else:
                    logger.warning(f"[WEAK_CONTENT_HASH] symbol={symbol} title='{title}'")
                    weak_str = f"{title}_{publish_time}_{provider_name}"
                    content_hash = hashlib.md5(weak_str.encode('utf-8')).hexdigest()
                
                cursor.execute('''
                    INSERT INTO raw_news (symbol, title, content, content_hash, publish_time, provider, raw_data, status, source_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'raw', ?)
                ''', (
                    symbol, 
                    title, 
                    content, 
                    content_hash,
                    publish_time, 
                    provider_name,
                    item.get('raw_data', '{}'),
                    source_url
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
                result = future.result(timeout=timeout)
                if result:
                    return "SUCCESS_WITH_DATA", result
                else:
                    return "SUCCESS_NO_DATA", []
            except Exception as e:
                backoff_time = min(60 * (2 ** attempt), 600)
                logger.warning(f"[BACKOFF] Attempt {attempt+1}/{max_retries} failed for {provider.name} on {symbol}: {e}. Retrying in {backoff_time}s.")
                if attempt < max_retries - 1:
                    time.sleep(backoff_time)
                else:
                    logger.error(f"[PROVIDER_FAIL] All attempts failed for {provider.name} on {symbol}.")
                    return "FAILED", []
    return "FAILED", []

def get_held_stocks():
    fallback_env = os.getenv("NEWS_FALLBACK_WATCHLIST", "")
    default_watchlist = [s.strip() for s in fallback_env.split(",")] if fallback_env else []
    
    try:
        portfolio = load_portfolio()
        if portfolio and "positions" in portfolio and portfolio["positions"]:
            live_stocks = list(portfolio["positions"].keys())
            logger.info(f"[HELD_STOCKS_FROM_PORTFOLIO] 成功挂载真实持仓池，共 {len(live_stocks)} 只标的")
            return live_stocks
        else:
            if default_watchlist:
                logger.warning("[HELD_STOCKS_FALLBACK] 真实持仓为空，退化降级至系统级后备观察名单")
            else:
                logger.warning("[HELD_STOCKS_FALLBACK] 真实持仓为空且未配置 NEWS_FALLBACK_WATCHLIST，跳过本轮抓取")
            return default_watchlist
    except Exception as e:
        logger.error(f"[HELD_STOCKS_FALLBACK] 加载真实持仓发生异常: {e}，强制启用安全后备名单" if default_watchlist else f"[HELD_STOCKS_FALLBACK] 加载真实持仓发生异常: {e}，且无配置后备名单")
        return default_watchlist

def process_symbol_with_providers(symbol, providers, circuit_breaker):
    fetch_success = False
    for provider in providers:
        if not circuit_breaker.allow(provider.name):
            logger.warning(f"[PROVIDER_FAILOVER] Circuit breaker open for {provider.name}, skipping.")
            continue
            
        status, data = fetch_worker(provider, symbol, timeout=20)
        
        if status == "SUCCESS_WITH_DATA":
            circuit_breaker.success(provider.name)
            save_news(symbol, provider.name, data)
            logger.info(f"Saved {len(data)} news items for {symbol} from {provider.name}.")
            fetch_success = True
            break
        elif status == "SUCCESS_NO_DATA":
            logger.info(f"[PROVIDER_NO_DATA] {provider.name} returned empty news for {symbol}.")
            if provider.name == "local_cache":
                logger.warning(f"[NEWS_FETCH_DEGRADED] local cache also empty for {symbol}")
                fetch_success = False
                break
            else:
                logger.info(f"[PROVIDER_NO_DATA_CONTINUE_FALLBACK] Continuing to next provider for {symbol}.")
                continue
        elif status == "FAILED":
            circuit_breaker.failure(provider.name)
            logger.error(f"[PROVIDER_FAILOVER] Fetch failed for {provider.name} on {symbol}")
            continue
            
    if not fetch_success:
        logger.warning(f"[NEWS_FETCH_DEGRADED] All providers failed or empty for {symbol}.")
        
    return fetch_success


def scheduler_loop():
    init_db()
    import random
    providers = [AkshareProvider(), EastMoneyProvider(), LocalCacheProvider()]
    circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout_minutes=5)
    
    logger.info("Starting scheduler loop...")
    
    while True:
        try:
            symbols = get_held_stocks()
            for symbol in symbols:
                process_symbol_with_providers(symbol, providers, circuit_breaker)
            
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")
            
        base_sleep = 180
        jitter = random.uniform(10, 60)
        sleep_time = base_sleep + jitter
        logger.info(f"Sleeping for {sleep_time:.2f} seconds...")
        time.sleep(sleep_time)

if __name__ == '__main__':
    scheduler_loop()
