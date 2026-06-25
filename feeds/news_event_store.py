import sqlite3
import os
import sys
import threading
from typing import Dict, Any, List

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger

class NewsEventStore:
    def __init__(self, db_path: str = None):
        if not db_path:
            db_path = os.path.join(PROJECT_ROOT, "data_cache", "news_events.db")
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS news_events(
                        event_id TEXT PRIMARY KEY,
                        source TEXT,
                        event_type TEXT,
                        event_time TEXT,
                        ingest_time TEXT,
                        symbols TEXT,
                        title TEXT,
                        content TEXT,
                        url TEXT,
                        importance TEXT,
                        sentiment REAL,
                        confidence REAL,
                        raw_json TEXT
                    )
                ''')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_event_time ON news_events(event_time DESC)')
                conn.commit()
        except Exception as e:
            logger.error(f"[NewsEventStore] 初始化数据库失败: {e}")

    def save_event(self, event: Dict[str, Any]) -> bool:
        """保存单个事件，如果已存在则忽略"""
        if not event or not event.get("event_id"):
            return False

        try:
            with self._lock:
                with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                    cursor = conn.cursor()
                    
                    # symbols 需要转为字符串存储
                    import json
                    symbols_str = json.dumps(event.get("symbols", []))
                    raw_str = json.dumps(event.get("raw", {}))
                    
                    cursor.execute('''
                        INSERT OR IGNORE INTO news_events (
                            event_id, source, event_type, event_time, ingest_time,
                            symbols, title, content, url, importance, sentiment, confidence, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        event["event_id"], event.get("source", "unknown"), event.get("event_type", "unknown"),
                        event.get("event_time"), event.get("ingest_time"), symbols_str,
                        event.get("title", ""), event.get("content", ""), event.get("url", ""),
                        event.get("importance", "UNKNOWN"), event.get("sentiment"), event.get("confidence"),
                        raw_str
                    ))
                    conn.commit()
                    return cursor.rowcount > 0
        except Exception as e:
            import traceback
            logger.error(f"[NewsEventStore] 写入事件失败: {traceback.format_exc()}")
            return False

    def save_events(self, events: List[Dict[str, Any]]) -> int:
        """批量保存事件"""
        saved_count = 0
        for event in events:
            if self.save_event(event):
                saved_count += 1
        return saved_count

    def get_recent_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近事件"""
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM news_events ORDER BY event_time DESC LIMIT ?', (limit,))
                rows = cursor.fetchall()
                
                results = []
                import json
                for row in rows:
                    item = dict(row)
                    try:
                        item["symbols"] = json.loads(item.get("symbols", "[]"))
                        item["raw"] = json.loads(item.get("raw_json", "{}"))
                    except Exception:
                        item["symbols"] = []
                        item["raw"] = {}
                    results.append(item)
                return results
        except Exception as e:
            logger.error(f"[NewsEventStore] 读取最近事件失败: {e}")
            return []
            
    def count_events(self) -> int:
        try:
            with sqlite3.connect(self.db_path, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM news_events')
                return cursor.fetchone()[0]
        except Exception:
            return 0
