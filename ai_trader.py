import sys, os
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__)) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import json
import time
import logging
import requests
from datetime import datetime, timedelta

from datahub.storage import get_db_conn

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_local_events(symbol, lookback_minutes=180):
    cutoff_time = datetime.now() - timedelta(minutes=lookback_minutes)
    events = []
    
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT event_type, summary, polarity, key_facts 
            FROM event_cards 
            WHERE symbol = ? AND created_at >= ?
            ORDER BY created_at DESC
        ''', (symbol, cutoff_time.strftime('%Y-%m-%d %H:%M:%S')))
        
        for row in cursor.fetchall():
            try:
                key_facts = json.loads(row['key_facts']) if row['key_facts'] else []
            except json.JSONDecodeError:
                key_facts = []
                
            events.append({
                "event_type": row['event_type'],
                "summary": row['summary'],
                "polarity": row['polarity'],
                "key_facts": key_facts
            })
            
    return json.dumps(events, ensure_ascii=False) if events else None

def get_rule_signal(symbol, market_data):
    return {"action": "buy", "confidence": 0.85, "strategy": "momentum"}

def call_llm_veto(rule_signal, events_json):
    prompt = f"""
We have a rule-based trading signal: {json.dumps(rule_signal, ensure_ascii=False)}
Recent news events for this stock: {events_json}

Based on these events, should we proceed with the rule signal?
Return a JSON object with a single key 'action' which must be one of: 'confirm', 'veto', 'reduce'.
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
            timeout=5
        )
        if response.status_code == 200:
            result_json = response.json().get('response', '{}')
            parsed = json.loads(result_json)
            action = parsed.get('action', 'confirm').lower()
            if action in ['confirm', 'veto', 'reduce']:
                return action
    except requests.exceptions.RequestException as e:
        logger.warning(f"LLM veto request failed: {e}")
    except json.JSONDecodeError as e:
        logger.warning(f"LLM returned invalid JSON: {e}")
    
    return "confirm"

def decide(symbol, market_data):
    rule_signal = get_rule_signal(symbol, market_data)
    
    events_json = get_local_events(symbol, lookback_minutes=180)
    
    if not events_json:
        rule_signal['status'] = "FOLLOW_RULE_ONLY"
        logger.info(f"[{symbol}] No recent events. FOLLOW_RULE_ONLY. Initial action: {rule_signal['action']}")
        return rule_signal['action']
        
    logger.info(f"[{symbol}] Found recent events. Forwarding to LLM for veto check...")
    llm_action = call_llm_veto(rule_signal, events_json)
    logger.info(f"[{symbol}] LLM veto result: {llm_action}")
    
    return llm_action

class MarketDataError(Exception):
    pass

def fetch_market_data(symbol):
    try:
        data = {"price": 150.0, "volume": 10000}
        if not data or "price" not in data:
            raise MarketDataError("Invalid or incomplete market data structure")
        return data
    except Exception as e:
        raise MarketDataError(f"Data fetch failed: {str(e)}")

def main_loop():
    symbols = ["600519", "000858"]
    
    logger.info("Starting AI Trader main loop...")
    while True:
        for symbol in symbols:
            try:
                market_data = fetch_market_data(symbol)
                
                final_action = decide(symbol, market_data)
                
                if final_action in ['confirm', 'buy']:
                    logger.info(f"[{symbol}] Executing trade: BUY")
                elif final_action == 'reduce':
                    logger.info(f"[{symbol}] Executing trade: REDUCE position")
                elif final_action == 'veto':
                    logger.info(f"[{symbol}] Trade execution VETOED")
                
            except MarketDataError as e:
                logger.error(f"[{symbol}] Market data exception: {e}. Skipping trade logic.")
                continue
            except Exception as e:
                logger.error(f"[{symbol}] Unexpected loop error: {e}")
                continue
                
        time.sleep(60)

if __name__ == '__main__':
    main_loop()
