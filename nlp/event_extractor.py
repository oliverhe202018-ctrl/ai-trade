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
        import os
        llm_base_url = os.getenv("LLM_BASE_URL", "http://localhost:8080")
        
        # If it's llama.cpp, we use the OpenAI compatible endpoint:
        # http://localhost:8080/v1/chat/completions
        response = requests.post(
            f"{llm_base_url}/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": "You are a helpful financial assistant."},
                    {"role": "user", "content": prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1
            },
            timeout=15
        )
        if response.status_code == 200:
            result_json = response.json().get('choices', [{}])[0].get('message', {}).get('content', '{}')
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
    from core.health_bus import write_heartbeat
    import json
    from pathlib import Path
    import os
    
    logger.info("Starting background worker for radar event processing...")
    project_root = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    events_file = project_root / "data_cache" / "events" / "latest_events.json"
    
    last_processed_ids = set()
    
    while True:
        try:
            if not events_file.exists():
                write_heartbeat(
                    channel="L3",
                    status="NO_INPUT",
                    source="nlp/event_extractor.py",
                    message="NO_INPUT",
                    extra={"events_processed": 0}
                )
                time.sleep(5)
                continue
                
            try:
                with events_file.open("r", encoding="utf-8") as f:
                    events = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load latest_events.json: {e}")
                events = []
                
            new_events = [e for e in events if e.get("event_id") not in last_processed_ids]
            
            if not new_events:
                write_heartbeat(
                    channel="L3",
                    status="NO_INPUT",
                    source="nlp/event_extractor.py",
                    message="NO_INPUT",
                    extra={"events_processed": 0}
                )
                time.sleep(5)
                continue
                
            processed_count = 0
            with get_db_conn() as conn:
                cursor = conn.cursor()
                
                for event in new_events:
                    event_id = event.get('event_id')
                    symbol = event.get('symbol', '')
                    title = event.get('title', '')
                    publish_time = event.get('published_at', '')
                    event_type = event.get('event_type', 'announcement')
                    source_url = event.get('url', '')
                    importance = event.get('importance', 'low')
                    reason = event.get('reason', '')
                    
                    if not symbol or not title:
                        last_processed_ids.add(event_id)
                        continue
                        
                    # We skip LLM generation for basic events to save cost
                    # Just map the title as summary
                    summary = title
                    polarity = 0
                    if importance == "high":
                        polarity = -0.5 if event_type in ("abnormal_volatility", "risk_warning", "reduction") else 0.5
                    
                    content_hash = f"{event_id}_{symbol}"
                    fetch_time = datetime.now().isoformat()
                    
                    cursor.execute('''
                        INSERT INTO event_cards (symbol, event_type, summary, source_news_id, polarity, key_facts, content_hash, confidence, novelty, risk_flags, publish_time, fetch_time, stale, source_url)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        symbol,
                        event_type,
                        summary,
                        0, # No raw_news id anymore
                        polarity,
                        json.dumps([reason], ensure_ascii=False) if reason else "[]",
                        content_hash,
                        0.9, # High confidence for official announcements
                        0.8,
                        "[]",
                        publish_time,
                        fetch_time,
                        0, # not stale yet
                        source_url
                    ))
                    
                    last_processed_ids.add(event_id)
                    processed_count += 1
                
                conn.commit()
                
            # Limit the set size to prevent memory leak
            if len(last_processed_ids) > 2000:
                last_processed_ids = set(list(last_processed_ids)[-1000:])
            
            if processed_count > 0:
                write_heartbeat(
                    channel="L3",
                    status="OK",
                    source="nlp/event_extractor.py",
                    message="events extracted",
                    extra={"events_processed": processed_count}
                )
                
        except Exception as e:
            logger.error(f"Error in worker_process_raw_news: {e}")
            try:
                write_heartbeat(
                    channel="L3",
                    status="ERROR",
                    source="nlp/event_extractor.py",
                    message=str(e)
                )
            except Exception as hb_err:
                pass
            time.sleep(5)
            
        time.sleep(10)

if __name__ == '__main__':
    t = threading.Thread(target=worker_process_raw_news, daemon=True)
    t.start()
    
    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            break
