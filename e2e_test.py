import sqlite3
import os
import time
from datahub.storage import get_db_conn
from nlp.event_extractor import worker_process_raw_news
import threading

print('--- DB VERIFICATION ---')
with get_db_conn() as conn:
    print('PRAGMA table_info(raw_news):')
    for row in conn.execute('PRAGMA table_info(raw_news)').fetchall():
        print(dict(row))

    print('\nSELECT status, COUNT(*) FROM raw_news GROUP BY status:')
    for row in conn.execute('SELECT status, COUNT(*) FROM raw_news GROUP BY status').fetchall():
        print(dict(row))

    print('\nSELECT id,symbol,status,source_url FROM raw_news ORDER BY id DESC LIMIT 5:')
    for row in conn.execute('SELECT id,symbol,status,source_url FROM raw_news ORDER BY id DESC LIMIT 5').fetchall():
        print(dict(row))

    print('\nSELECT id,symbol,event_type,publish_time,stale,source_url FROM event_cards ORDER BY id DESC LIMIT 5:')
    for row in conn.execute('SELECT id,symbol,event_type,publish_time,stale,source_url FROM event_cards ORDER BY id DESC LIMIT 5').fetchall():
        print(dict(row))

print('\n--- END-TO-END VALIDATION ---')
with get_db_conn() as conn:
    conn.execute("INSERT INTO raw_news (symbol, title, content, publish_time, provider, status) VALUES ('sh600000', 'E2E Validation Test', 'This is a great day for AI trading! The market is very positive.', '2026-06-25 10:00:00', 'test', 'raw')")
    conn.commit()

print('Inserted test row into raw_news. Starting extractor worker...')
t = threading.Thread(target=worker_process_raw_news, daemon=True)
t.start()

# wait to let LLaMA process the news
time.sleep(20)

with get_db_conn() as conn:
    raw = conn.execute("SELECT id, status FROM raw_news WHERE title='E2E Validation Test' ORDER BY id DESC LIMIT 1").fetchone()
    print(f'raw_news status after extraction: {raw["status"]}')
    card = conn.execute('SELECT * FROM event_cards WHERE source_news_id=? LIMIT 1', (raw['id'],)).fetchone()
    if card:
        print(f'event_cards output: {dict(card)}')
    else:
        print('event_cards output: None')
