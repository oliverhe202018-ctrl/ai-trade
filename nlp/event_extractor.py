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

from datahub.storage import get_db_conn

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
                cursor.execute("SELECT id, symbol, title, content, content_hash FROM raw_news WHERE status = 'raw' LIMIT 50")
                rows = cursor.fetchall()
                
                if not rows:
                    time.sleep(5)
                    continue
                    
                for row in rows:
                    news_id = row['id']
                    symbol = row['symbol']
                    title = row['title'] or ''
                    content = row['content'] or ''
                    content_hash = row['content_hash']
                    
                    if content_hash:
                        cursor.execute("SELECT event_type, summary, polarity, key_facts, confidence, novelty, risk_flags FROM event_cards WHERE content_hash = ? LIMIT 1", (content_hash,))
                        existing_card = cursor.fetchone()
                        if existing_card:
                            cursor.execute('''
                                INSERT INTO event_cards (symbol, event_type, summary, source_news_id, polarity, key_facts, content_hash, confidence, novelty, risk_flags)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                                existing_card['risk_flags']
                            ))
                            cursor.execute("UPDATE raw_news SET status = 'processed' WHERE id = ?", (news_id,))
                            conn.commit()
                            logger.info(f"Reused existing event_card for news_id {news_id} (symbol {symbol}) due to identical content_hash")
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
                    cursor.execute('''
                        INSERT INTO event_cards (symbol, event_type, summary, source_news_id, polarity, key_facts, content_hash, confidence, novelty, risk_flags)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        event_card['risk_flags']
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
