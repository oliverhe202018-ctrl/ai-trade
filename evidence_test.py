import sqlite3
import json
import threading
import time
from unittest.mock import patch
from datetime import datetime
from nlp.event_extractor import worker_process_raw_news
from datahub.storage import get_db_conn

print("1. Insert a test record")
with get_db_conn() as conn:
    conn.execute("INSERT INTO raw_news (symbol, title, content, publish_time, provider, status) VALUES ('sh600519', 'E2E Valid Generation Test', 'Kweichow Moutai reports positive earnings and strong guidance.', datetime('now', 'localtime'), 'test_provider', 'raw')")
    conn.commit()

# 2. Mock generate_event_card
def mock_generate(*args, **kwargs):
    return {
        'symbol': 'sh600519',
        'event_type': 'EarningsSurprise',
        'polarity': 'positive',
        'summary': 'Kweichow Moutai reports positive earnings.',
        'key_facts': ['Strong guidance'],
        'confidence': 0.9,
        'novelty': 0.8,
        'risk_flags': 'None'
    }

print('Starting mocked extractor worker...')
with patch('nlp.event_extractor.generate_event_card', side_effect=mock_generate):
    t = threading.Thread(target=worker_process_raw_news, daemon=True)
    t.start()
    time.sleep(5)

# 3. Check results
with get_db_conn() as conn:
    conn.row_factory = sqlite3.Row
    raw = conn.execute("SELECT * FROM raw_news WHERE title='E2E Valid Generation Test' ORDER BY id DESC LIMIT 1").fetchone()
    print(f'\n[Evidence 1] raw_news.status: {raw["status"]}')
    
    card = conn.execute('SELECT * FROM event_cards WHERE source_news_id=? LIMIT 1', (raw['id'],)).fetchone()
    print(f'\n[Evidence 2] event_cards stale value: {card["stale"]}')
    print(f'Full event_card: {dict(card)}')

# 4. Check ai_trader
print('\n[Evidence 3] Simulating ai_trader reading fresh events:')
with get_db_conn() as conn:
    conn.row_factory = sqlite3.Row
    fresh_events = conn.execute("SELECT * FROM event_cards WHERE symbol='sh600519' AND stale=0 ORDER BY publish_time DESC").fetchall()
    print(f'Found {len(fresh_events)} fresh events for sh600519.')
    for e in fresh_events:
        print(f' - ID: {e["id"]}, Type: {e["event_type"]}, Polarity: {e["polarity"]}, Stale: {e["stale"]}')
