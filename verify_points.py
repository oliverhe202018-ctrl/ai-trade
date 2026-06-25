import sys
import os
import json
import time
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from datahub.news_fetcher import EastMoneyProvider, save_news
from datahub.storage import get_db_conn

def verify_1():
    print("=== 1. EastMoneyProvider Verification ===")
    provider = EastMoneyProvider()
    try:
        results = provider.fetch("sh600519")
        print(f"Fetch success. Count: {len(results)}")
        if results:
            print("First result keys:", list(results[0].keys()))
            print("Title:", results[0]['title'])
    except Exception as e:
        print(f"Error: {e}")

def verify_3_and_4():
    print("\n=== 3. and 4. DB Insert and Event Extractor ===")
    # 3. Simulate save_news
    test_items = [{
        'symbol': 'test_verify',
        'title': 'Mock News for Verification',
        'content': 'This is a mock news content to verify insertion.',
        'publish_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'provider': 'eastmoney',
        'raw_data': '{}'
    }]
    save_news('test_verify', 'eastmoney', test_items)
    print("Saved 1 mock news item.")

    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, content_hash FROM raw_news WHERE symbol='test_verify' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        print(f"raw_news verified: id={row[0]}, title='{row[1]}', hash='{row[2]}'")
        news_id = row[0]
        hash_val = row[2]

        # 4. Simulate event_extractor inserting event_cards
        from nlp.event_extractor import compute_stale_flag, parse_datetime_safe
        
        # Manually trigger what event_extractor does
        publish_dt = parse_datetime_safe(test_items[0]['publish_time'])
        stale = compute_stale_flag(publish_dt)
        print(f"Computed stale flag for just now: {stale} (Expected: 0)")
        
        cursor.execute('''
            INSERT INTO event_cards (symbol, event_type, summary, source_news_id, polarity, key_facts, content_hash, confidence, novelty, risk_flags, publish_time, fetch_time, stale, source_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            'test_verify', 'VerifyEvent', 'Summary', news_id, 'Neutral', '[]', hash_val, 0.9, 0.5, '[]', test_items[0]['publish_time'], datetime.now().strftime('%Y-%m-%d %H:%M:%S'), stale, ''
        ))
        conn.commit()
        
        cursor.execute("SELECT id, stale, publish_time FROM event_cards WHERE symbol='test_verify' ORDER BY id DESC LIMIT 1")
        card_row = cursor.fetchone()
        print(f"event_cards verified: id={card_row[0]}, stale={card_row[1]}")

if __name__ == '__main__':
    verify_1()
    verify_3_and_4()
