import sys, os
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import re
import json
import time
import logging
import sqlite3
import threading
import requests
import jsonschema
from jsonschema import validate, ValidationError
from datetime import datetime, timezone

from datahub.storage import get_db_conn

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

STALE_THRESHOLD_MINUTES = 240

def parse_datetime_safe(value):
    if not value:
        return None
    try:
        v = str(value).replace('Z', '+00:00')
        return datetime.fromisoformat(v)
    except Exception:
        pass
    
    try:
        return datetime.strptime(str(value).split('.')[0].strip(), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def compute_stale_flag(publish_time_dt, now=None):
    if not publish_time_dt:
        return 1
    if now is None:
        if publish_time_dt.tzinfo is not None:
            now = datetime.now(timezone.utc)
        else:
            now = datetime.now()
    try:
        diff_minutes = (now - publish_time_dt).total_seconds() / 60.0
        if -1440 <= diff_minutes <= STALE_THRESHOLD_MINUTES:
            return 0
    except Exception:
        pass
    return 1

def extract_source_url(raw_data):
    if not raw_data:
        return ""
    try:
        data = json.loads(raw_data)
        if isinstance(data, dict):
            for key in ['url', 'source_url', 'link']:
                if key in data and data.get(key):
                    return str(data[key])
    except Exception:
        pass
    return ""

def setup_tables():
    with get_db_conn() as conn:
        cursor = conn.cursor()
        
        try:
            cursor.execute('ALTER TABLE raw_news ADD COLUMN status TEXT DEFAULT "raw"')
        except sqlite3.OperationalError:
            pass
            
        # Initialize old records to raw if status is NULL
        cursor.execute('UPDATE raw_news SET status = "raw" WHERE status IS NULL')
        conn.commit()

def clean_text(raw_text):
    if not raw_text:
        return ""
        
    text = raw_text
    
    text = re.sub(r'<[^>]+>', '', text)
    
    disclaimers = [
        r'本文不构成投资建议',
        r'投资有风险，入市需谨慎',
        r'不代表本台立场',
        r'免责声明.*'
    ]
    for pattern in disclaimers:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        
    text = re.sub(r'(?:涨幅|跌幅|现价|最高|最低)[\s:：]*[+-]?\d+(?:\.\d+)?%?', '', text)
    text = re.sub(r'\b\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?\b', '', text)
    
    text = re.sub(r'\s+', ' ', text).strip()
    return text

EVENT_CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string"},
        "event_type": {"type": "string"},
        "polarity": {"type": "string", "enum": ["positive", "negative", "neutral"]},
        "summary": {"type": "string"},
        "key_facts": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "novelty": {"type": "number", "minimum": 0, "maximum": 1},
        "risk_flags": {"type": "string"}
    },
    "required": ["symbol", "event_type", "polarity", "summary", "key_facts", "confidence", "novelty", "risk_flags"]
}

def generate_event_card(news_id, symbol, cleaned_text):
    prompt = f"""
Please extract an event card from the following news text. 
Strictly return ONLY a valid JSON object with this exact structure:
{json.dumps(EVENT_CARD_SCHEMA)}

Text:
{cleaned_text[:2000]}
"""
    try:
        response = requests.post(
            'http://localhost:11434/api/generate',
            json={
                "model": "llama3",
                "prompt": prompt,
                "stream": False,
                "format": "json"
            },
            timeout=15
        )
        if response.status_code == 200:
            result_json = response.json().get('response', '{}')
            parsed = json.loads(result_json)
            
            try:
                validate(instance=parsed, schema=EVENT_CARD_SCHEMA)
            except ValidationError as ve:
                logger.error(f"[SCHEMA_VALIDATION_FAIL] EventExtractor LLM schema mismatch: {ve.message}")
                return None
            
            if len(parsed.get('summary', '')) > 50:
                parsed['summary'] = parsed['summary'][:47] + '...'
                
            return parsed
            
    except requests.exceptions.RequestException as e:
        logger.warning(f"Local LLaMA call failed for news_id {news_id}: {e}")
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON from LLaMA for news_id {news_id}: {e}")
        
    logger.error(f"[SCHEMA_VALIDATION_FAIL] Fallback generation skipped to prevent bad data.")
    return None

def worker_process_raw_news():
    setup_tables()
    logger.info("Starting background worker for raw news processing...")
    
    while True:
        try:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, symbol, title, content, publish_time, created_at, provider, raw_data, content_hash FROM raw_news WHERE status = 'raw' LIMIT 50")
                rows = cursor.fetchall()
                
                if not rows:
                    time.sleep(5)
                    continue
                    
                for row in rows:
                    news_id = row['id']
                    symbol = row['symbol']
                    title = row['title'] or ''
                    content = row['content'] or ''
                    raw_publish_time = row['publish_time']
                    raw_created_at = row['created_at']
                    provider = row['provider']
                    raw_data = row['raw_data']
                    content_hash = row['content_hash']
                    
                    source_url = extract_source_url(raw_data)
                    if not source_url:
                        logger.info(f"[EVENT_SOURCE_URL_MISSING] Source URL not found for news_id {news_id}")
                        
                    publish_time_dt = parse_datetime_safe(raw_publish_time)
                    if publish_time_dt:
                        publish_time_str = raw_publish_time
                        stale = compute_stale_flag(publish_time_dt)
                    else:
                        publish_time_str = raw_publish_time
                        stale = 1
                        logger.warning(f"[EVENT_PUBLISH_TIME_MISSING] Missing or invalid publish_time for news_id {news_id}")
                        
                    fetch_time = raw_created_at if raw_created_at else datetime.now().isoformat()
                    
                    if content_hash:
                        cursor.execute("SELECT event_type, summary, polarity, key_facts, confidence, novelty, risk_flags FROM event_cards WHERE content_hash = ? LIMIT 1", (content_hash,))
                        existing_card = cursor.fetchone()
                        if existing_card:
                            logger.info(f"[EVENT_CARD_INSERT] Reusing event_card for news_id {news_id}")
                            logger.info(f"[EVENT_CARD_STALE] news_id {news_id} stale flag is {stale}")
                            cursor.execute('''
                                INSERT INTO event_cards (symbol, event_type, summary, source_news_id, polarity, key_facts, content_hash, confidence, novelty, risk_flags, publish_time, fetch_time, stale, source_url)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                symbol,
                                existing_card['event_type'],
                                existing_card['summary'],
                                news_id,
                                existing_card['polarity'],
                                existing_card['key_facts'],
                                content_hash,
                                existing_card['confidence'],
                                existing_card['novelty'],
                                existing_card['risk_flags'],
                                publish_time_str,
                                fetch_time,
                                stale,
                                source_url
                            ))
                            cursor.execute("UPDATE raw_news SET status = 'processed' WHERE id = ?", (news_id,))
                            conn.commit()
                            continue
                    
                    combined_text = f"{title}. {content}"
                    cleaned_text = clean_text(combined_text)
                    
                    if not cleaned_text:
                        cursor.execute("UPDATE raw_news SET status = 'skipped' WHERE id = ?", (news_id,))
                        conn.commit()
                        continue
                    
                    event_card = generate_event_card(news_id, symbol, cleaned_text)
                    
                    if not event_card:
                        logger.warning(f"[SCHEMA_VALIDATION_FAIL] Skipping news_id {news_id} because of bad AI generation")
                        cursor.execute("UPDATE raw_news SET status = 'skipped_bad_schema' WHERE id = ?", (news_id,))
                        conn.commit()
                        continue
                    
                    key_facts_json = json.dumps(event_card.get('key_facts', []), ensure_ascii=False)
                    
                    logger.info(f"[EVENT_CARD_INSERT] Generated new event_card for news_id {news_id}")
                    logger.info(f"[EVENT_CARD_STALE] news_id {news_id} stale flag is {stale}")
                    cursor.execute('''
                        INSERT INTO event_cards (symbol, event_type, summary, source_news_id, polarity, key_facts, content_hash, confidence, novelty, risk_flags, publish_time, fetch_time, stale, source_url)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        event_card['symbol'],
                        event_card['event_type'],
                        event_card['summary'],
                        news_id,
                        event_card['polarity'],
                        key_facts_json,
                        content_hash,
                        event_card['confidence'],
                        event_card['novelty'],
                        event_card['risk_flags'],
                        publish_time_str,
                        fetch_time,
                        stale,
                        source_url
                    ))
                    
                    cursor.execute("UPDATE raw_news SET status = 'processed' WHERE id = ?", (news_id,))
                    conn.commit()
                    
                    logger.info(f"Processed news_id {news_id} for symbol {symbol}")
                    
        except Exception as e:
            logger.error(f"Error in worker_process_raw_news: {e}")
            time.sleep(5)

if __name__ == '__main__':
    t = threading.Thread(target=worker_process_raw_news, daemon=True)
    t.start()
    
    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            break
