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
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_raw_news_symbol ON raw_news(symbol)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_raw_news_publish_time ON raw_news(publish_time)')
        
        # 自动执行 raw_news 的无损 Migration
        cursor.execute("PRAGMA table_info(raw_news)")
        raw_news_columns = {row['name'] for row in cursor.fetchall()}
        if "content_hash" not in raw_news_columns:
            try:
                cursor.execute("ALTER TABLE raw_news ADD COLUMN content_hash TEXT")
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Migration failed for raw_news content_hash: {e}")
                
        if "status" not in raw_news_columns:
            try:
                cursor.execute("ALTER TABLE raw_news ADD COLUMN status TEXT DEFAULT 'raw'")
                cursor.execute("UPDATE raw_news SET status = 'raw' WHERE status IS NULL")
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Critical migration failed for raw_news status: {e}")
                raise

        if "source_url" not in raw_news_columns:
            try:
                cursor.execute("ALTER TABLE raw_news ADD COLUMN source_url TEXT DEFAULT ''")
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Migration failed for raw_news source_url: {e}")
                
        cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_news_symbol_content_hash ON raw_news(symbol, content_hash)')
        
        # Backfill raw_news content_hash
        import hashlib
        import re
        def _normalize(text):
            text = text or ""
            text = re.sub(r'<[^>]+>', '', text)
            return re.sub(r'\s+', ' ', text).strip()
            
        cursor.execute('SELECT id, symbol, title, content, publish_time, provider FROM raw_news WHERE content_hash IS NULL OR content_hash = ""')
        for row in cursor.fetchall():
            r_id, sym, title, content, ptime, provider = row
            title = title or ""
            content = content or ""
            if content.strip():
                norm = _normalize(content)
                c_hash = hashlib.md5(norm.encode('utf-8')).hexdigest()
            else:
                weak_str = f"{title}_{ptime}_{provider}"
                c_hash = hashlib.md5(weak_str.encode('utf-8')).hexdigest()
                import logging
                logging.getLogger(__name__).warning(f"[WEAK_CONTENT_HASH] backfill symbol={sym} title='{title}'")
                
            try:
                cursor.execute('UPDATE raw_news SET content_hash = ? WHERE id = ?', (c_hash, r_id))
            except sqlite3.IntegrityError:
                # Conflict, duplicate content, keep the original, delete the duplicate
                import logging
                logging.getLogger(__name__).info(f"Duplicate content_hash found during backfill, removing redundant row id {r_id}")
                cursor.execute('DELETE FROM raw_news WHERE id = ?', (r_id,))
        conn.commit()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS event_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                event_type TEXT NOT NULL,
                polarity TEXT,
                confidence REAL DEFAULT 0.5,
                novelty REAL DEFAULT 0.5,
                summary TEXT NOT NULL,
                key_facts TEXT,
                risk_flags TEXT,
                source_url TEXT,
                publish_time DATETIME,
                fetch_time DATETIME,
                stale INTEGER DEFAULT 1,
                impact_score REAL,
                source_news_id INTEGER,
                content_hash TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(source_news_id) REFERENCES raw_news(id)
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_event_cards_symbol_publish ON event_cards(symbol, publish_time)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_event_cards_symbol_stale ON event_cards(symbol, stale)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_event_cards_source_news_id ON event_cards(source_news_id)')
        
        # 自动执行无损 Migration
        cursor.execute("PRAGMA table_info(event_cards)")
        existing_columns = {row['name'] for row in cursor.fetchall()}
        
        migrations = [
            ("polarity", "TEXT"),
            ("confidence", "REAL DEFAULT 0.5"),
            ("novelty", "REAL DEFAULT 0.5"),
            ("key_facts", "TEXT"),
            ("publish_time", "DATETIME"),
            ("fetch_time", "DATETIME"),
            ("stale", "INTEGER DEFAULT 1"),
            ("risk_flags", "TEXT"),
            ("source_url", "TEXT"),
            ("impact_score", "REAL"),
            ("content_hash", "TEXT"),
            ("created_at", "DATETIME DEFAULT CURRENT_TIMESTAMP")
        ]
        
        for col_name, col_type in migrations:
            if col_name not in existing_columns:
                try:
                    cursor.execute(f"ALTER TABLE event_cards ADD COLUMN {col_name} {col_type}")
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"Migration failed for column {col_name}: {e}")
                    
        # Apply defaults to old rows that have NULL in required defaulted fields
        cursor.execute('UPDATE event_cards SET confidence = 0.5 WHERE confidence IS NULL')
        cursor.execute('UPDATE event_cards SET novelty = 0.5 WHERE novelty IS NULL')
        cursor.execute('UPDATE event_cards SET stale = 1 WHERE stale IS NULL OR publish_time IS NULL')
        cursor.execute('UPDATE event_cards SET fetch_time = COALESCE(created_at, CURRENT_TIMESTAMP) WHERE fetch_time IS NULL')
        
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
