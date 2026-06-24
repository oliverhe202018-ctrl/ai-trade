import sqlite3
import pytest
import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__)) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from datahub.storage import init_db, DB_PATH
from datahub.news_fetcher import save_news

def setup_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()

def get_db_rows():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT id, symbol, title, content_hash FROM raw_news")
        return c.fetchall()

def test_duplicate_content():
    setup_db()
    
    # 1. 同正文不同标题，只保存一次
    news1 = {
        'title': 'Title A',
        'content': 'This is the exact same content.',
        'publish_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    news2 = {
        'title': 'Title B',
        'content': 'This is the exact same content.',
        'publish_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    save_news("TEST1", "test_provider", [news1, news2])
    
    rows = get_db_rows()
    assert len(rows) == 1, "Duplicate content should not be saved"

def test_different_content_same_title():
    setup_db()
    
    # 2. 不同正文相同标题，可以保存
    news1 = {
        'title': 'Same Title',
        'content': 'This is content A.',
        'publish_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    news2 = {
        'title': 'Same Title',
        'content': 'This is content B.',
        'publish_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    save_news("TEST2", "test_provider", [news1, news2])
    
    rows = get_db_rows()
    assert len(rows) == 2, "Different contents should be saved"

def test_empty_content():
    setup_db()
    
    # 3. 空正文不会导致所有 NULL 绕过 UNIQUE
    news1 = {
        'title': 'Title A',
        'content': '',
        'publish_time': '2023-01-01 10:00:00'
    }
    news2 = {
        'title': 'Title A',
        'content': '',
        'publish_time': '2023-01-01 10:00:00'
    }
    news3 = {
        'title': 'Title B',
        'content': '',
        'publish_time': '2023-01-01 10:00:00'
    }
    
    save_news("TEST3", "test_provider", [news1, news2, news3])
    rows = get_db_rows()
    assert len(rows) == 2, "Empty content should use weak hash and de-dup on title/time"

if __name__ == "__main__":
    pytest.main(["-v", __file__])
