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
            
        try:
            cursor.execute('ALTER TABLE event_cards ADD COLUMN polarity TEXT')
        except sqlite3.OperationalError:
            pass
            
        try:
            cursor.execute('ALTER TABLE event_cards ADD COLUMN key_facts TEXT')
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

def generate_event_card(news_id, symbol, cleaned_text):
    prompt = f"""
Please extract an event card from the following news text. 
Strictly return ONLY a valid JSON object with this exact structure:
{{
  "symbol": "{symbol}",
  "event_type": "Company News|Market Event|Policy Change|Other",
  "polarity": "positive|negative|neutral",
  "summary": "Brief summary max 50 chars",
  "key_facts": ["fact1", "fact2"]
}}

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
            
            if len(parsed.get('summary', '')) > 50:
                parsed['summary'] = parsed['summary'][:47] + '...'
                
            return {
                "symbol": str(parsed.get('symbol', symbol)),
                "event_type": str(parsed.get('event_type', 'Other')),
                "polarity": str(parsed.get('polarity', 'neutral')),
                "summary": str(parsed.get('summary', '')),
                "key_facts": [str(k) for k in parsed.get('key_facts', [])]
            }
    except requests.exceptions.RequestException as e:
        logger.warning(f"Local LLaMA call failed for news_id {news_id}, falling back to rules: {e}")
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON from LLaMA for news_id {news_id}: {e}")
        
    return {
        "symbol": symbol,
        "event_type": "Other",
        "polarity": "neutral",
        "summary": cleaned_text[:50],
        "key_facts": [cleaned_text[:100]]
    }

def worker_process_raw_news():
    setup_tables()
    logger.info("Starting background worker for raw news processing...")
    
    while True:
        try:
            with get_db_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, symbol, title, content FROM raw_news WHERE status = 'raw' LIMIT 50")
                rows = cursor.fetchall()
                
                if not rows:
                    time.sleep(5)
                    continue
                    
                for row in rows:
                    news_id = row['id']
                    symbol = row['symbol']
                    title = row['title'] or ''
                    content = row['content'] or ''
                    
                    combined_text = f"{title}. {content}"
                    cleaned_text = clean_text(combined_text)
                    
                    if not cleaned_text:
                        cursor.execute("UPDATE raw_news SET status = 'skipped' WHERE id = ?", (news_id,))
                        conn.commit()
                        continue
                    
                    event_card = generate_event_card(news_id, symbol, cleaned_text)
                    
                    key_facts_json = json.dumps(event_card.get('key_facts', []), ensure_ascii=False)
                    cursor.execute('''
                        INSERT INTO event_cards (symbol, event_type, summary, source_news_id, polarity, key_facts)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        event_card['symbol'],
                        event_card['event_type'],
                        event_card['summary'],
                        news_id,
                        event_card['polarity'],
                        key_facts_json
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
