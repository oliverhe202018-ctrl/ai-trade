import sys, os
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import sqlite3
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), 'datahub.db')

def init_db():
    with get_db_conn() as conn:
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS raw_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT,
                publish_time DATETIME NOT NULL,
                provider TEXT NOT NULL,
                raw_data TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, title, publish_time)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_raw_news_symbol ON raw_news(symbol)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_raw_news_publish_time ON raw_news(publish_time)')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS event_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                event_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                impact_score REAL,
                source_news_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(source_news_id) REFERENCES raw_news(id)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_event_cards_symbol ON event_cards(symbol)')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS provider_health (
                provider_name TEXT PRIMARY KEY,
                consecutive_failures INTEGER DEFAULT 0,
                last_failure_time DATETIME,
                last_success_time DATETIME,
                status TEXT DEFAULT 'active'
            )
        ''')
        conn.commit()

@contextmanager
def get_db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

if __name__ == '__main__':
    init_db()
