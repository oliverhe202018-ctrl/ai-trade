import sqlite3
import pytest
import os
from datetime import datetime, timedelta
import sys

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__)) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from datahub.storage import init_db, DB_PATH
from ai_trader import get_local_events

def setup_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()

def insert_event(symbol, publish_time, stale=0, created_at=None):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO event_cards (symbol, event_type, summary, publish_time, stale, created_at)
            VALUES (?, 'news', 'test', ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
        ''', (symbol, publish_time.strftime('%Y-%m-%d %H:%M:%S') if publish_time else None, stale, created_at))
        conn.commit()

def test_publish_time_yesterday():
    setup_db()
    yesterday = datetime.now() - timedelta(days=1)
    insert_event("TEST1", yesterday, stale=0)
    events_json, _ = get_local_events("TEST1")
    assert events_json is None

def test_stale_1():
    setup_db()
    recent = datetime.now() - timedelta(minutes=5)
    insert_event("TEST2", recent, stale=1)
    events_json, _ = get_local_events("TEST2")
    assert events_json is None

def test_publish_time_10_mins():
    setup_db()
    recent = datetime.now() - timedelta(minutes=10)
    insert_event("TEST3", recent, stale=0)
    events_json, _ = get_local_events("TEST3")
    assert events_json is not None
    assert "TEST3" in events_json

if __name__ == "__main__":
    pytest.main(["-v", __file__])
