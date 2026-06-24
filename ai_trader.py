import sys, os
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__)) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import json
import time
import uuid
import logging
import requests
import zmq
from jsonschema import validate, ValidationError
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
            SELECT id, symbol, event_type, polarity, confidence, novelty, summary, key_facts, risk_flags, source_url, publish_time, stale 
            FROM event_cards 
            WHERE symbol = ? AND publish_time >= ? AND stale = 0
            ORDER BY publish_time DESC
        ''', (symbol, cutoff_time.strftime('%Y-%m-%d %H:%M:%S')))
        
        now = datetime.now()
        for row in cursor.fetchall():
            pub_time_str = row['publish_time']
            if not pub_time_str:
                logger.warning(f"[EVENT_PUBLISH_TIME_MISSING] [{symbol}] Event ID {row['id']} 缺失 publish_time，保守跳过不入 Prompt")
                continue
                
            try:
                pub_time = datetime.strptime(pub_time_str, '%Y-%m-%d %H:%M:%S')
                if (now - pub_time).total_seconds() > 1800:
                    logger.info(f"[STALE_EVENT] [{symbol}] 资讯 (ID {row['id']}) 距今超过 30 分钟 ({pub_time_str})，已失去时效性，标记为空事件并忽略")
                    continue
            except Exception:
                logger.warning(f"[EVENT_PUBLISH_TIME_MISSING] [{symbol}] Event ID {row['id']} publish_time 格式解析失败，保守跳过")
                continue
                
            try:
                key_facts = json.loads(row['key_facts']) if row['key_facts'] else []
            except json.JSONDecodeError:
                key_facts = []
                    
            events.append({
                "id": row['id'],
                "symbol": row['symbol'],
                "event_type": row['event_type'],
                "polarity": row['polarity'],
                "confidence": row['confidence'],
                "novelty": row['novelty'],
                "summary": row['summary'],
                "key_facts": key_facts,
                "risk_flags": row['risk_flags'],
                "source_url": row['source_url'],
                "publish_time": pub_time_str,
                "stale": 0
            })
            
    if not events:
        return None, []
        
    event_ids = [e['id'] for e in events]
    return json.dumps(events, ensure_ascii=False), event_ids

def get_rule_signal(symbol, market_data):
    return {"action": "buy", "confidence": 0.85, "strategy": "momentum"}

VETO_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["confirm", "veto", "reduce", "hold"]},
        "reason": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1}
    },
    "required": ["action", "reason", "confidence"]
}

def call_llm_veto(rule_signal, events_json):
    prompt = f"""
We have a rule-based trading signal: {json.dumps(rule_signal, ensure_ascii=False)}
Recent news events for this stock: {events_json}

Based on these events, should we proceed with the rule signal?
IMPORTANT:
- 如果没有新鲜事件 (If there are no fresh events or events_json is empty), you MUST NOT forcefully return 'confirm'.
- stale 事件 (stale=1) 不能作为买入或确认 (confirm) 的依据 (Stale events cannot be used as a buy basis).
Return a JSON object conforming strictly to this schema:
{json.dumps(VETO_SCHEMA)}
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
            
            try:
                validate(instance=parsed, schema=VETO_SCHEMA)
            except ValidationError as ve:
                logger.error(f"[SCHEMA_VALIDATION_FAIL] LLM schema mismatch: {ve.message}")
                return "veto", 0.0
                
            raw_action = parsed.get('action', 'veto')
            if not isinstance(raw_action, str):
                logger.error(f"[LLM_FAILSAFE] Action field is not a string: {type(raw_action)}")
                return "veto", 0.0
            
            action = raw_action.lower()
            if action not in ["confirm", "veto", "reduce", "hold"]:
                logger.error(f"[LLM_FAILSAFE] Invalid action value: {action}")
                action = "veto"
                
            confidence = parsed.get('confidence', 0.0)
            return action, confidence
        else:
            logger.error(f"[LLM_FAILSAFE] HTTP Error: {response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"[LLM_FAILSAFE] Network disconnected: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"[LLM_FAILSAFE] LLM returned invalid JSON: {e}")
    except Exception as e:
        logger.error(f"[LLM_FAILSAFE] Unexpected error: {e}")
    
    logger.warning("[CONSERVATIVE_VETO] Defaulting to 'veto' due to failure or invalid response.")
    return "veto", 0.0

def decide(symbol, market_data):
    decision_id = str(uuid.uuid4())
    rule_signal = get_rule_signal(symbol, market_data)
    
    events_json, event_ids = get_local_events(symbol, lookback_minutes=180)
    
    if not events_json:
        original_action = rule_signal['action'].lower()
        if original_action in ["sell", "reduce", "veto"]:
            logger.info(f"[{symbol}] No fresh events. Allowing defensive action: {original_action}")
            return original_action, [], None, decision_id
        else:
            logger.info(f"[{symbol}] [NO_FRESH_EVENTS_HOLD] No fresh events. Holding instead of {original_action}.")
            return "hold", [], None, decision_id
        
    logger.info(f"[{symbol}] Found recent events. Forwarding to LLM for veto check...")
    llm_action, confidence = call_llm_veto(rule_signal, events_json)
        
    # [TRADE_TRACE] for LLM Veto
    llm_audit = {
        "decision_id": decision_id,
        "symbol": symbol,
        "event_ids": event_ids,
        "llm_action": llm_action,
        "confidence": confidence
    }
    logger.info(f"[TRADE_TRACE] {json.dumps(llm_audit)}")
    
    logger.info(f"[{symbol}] LLM veto result: {llm_action}")
    
    return llm_action, event_ids, events_json, decision_id

class MarketDataError(Exception):
    pass

def final_risk_gate(symbol, action, market_data, events=None, portfolio=None, decision_id=None):
    from core.trading_state import get_trading_state, TradingState
    from core.risk_manager import _load_hyperparams
    
    def _block(reason):
        logger.warning(f"[RISK_GATE_BLOCK] [{symbol}] {reason}")
        if decision_id:
            logger.info(f"[TRADE_TRACE] {json.dumps({'decision_id': decision_id, 'risk_gate_result': 'BLOCKED', 'block_reason': reason})}")
        return "veto"
        
    def _pass(passed_action):
        if decision_id:
            logger.info(f"[TRADE_TRACE] {json.dumps({'decision_id': decision_id, 'risk_gate_result': 'PASSED', 'block_reason': ''})}")
        return passed_action
    
    if action in ['veto', 'hold']:
        return action
        
    if action == 'reduce':
        logger.info(f"[RISK_GATE] [{symbol}] Action is reduce, allowing risk reduction.")
        return _pass("reduce")
    
    if action not in ['buy', 'confirm']:
        return _block(f"Unknown action {action}")
        
    if not market_data:
        return _block("Market data is empty")
        
    price = market_data.get('price', 0)
    if price <= 0:
        return _block(f"Invalid price {price}")
        
    if events:
        import json
        try:
            evs = json.loads(events) if isinstance(events, str) else events
            for e in evs:
                if e.get('stale') is True:
                    return _block("Stale event detected")
        except Exception as ex:
            return _block(f"Event parsing failed: {ex}")
            
    if not portfolio:
        return _block("Portfolio is missing, cannot verify position limits")
        
    try:
        if get_trading_state() == TradingState.FROZEN.value:
            return _block("System is FROZEN")
    except Exception as e:
        logger.error(f"[RISK_GATE_BLOCK] Failed to get trading state: {e}")
        
    params = _load_hyperparams()
    max_position_pct = params.get('max_single_pct', 0.15)
    max_daily_loss = params.get('stop_loss_pct', -0.08)
    
    cash = portfolio.get('cash', 0.0)
    positions = portfolio.get('positions', {})
    daily_loss_pct = portfolio.get('daily_loss_pct', 0.0)
    
    if daily_loss_pct <= max_daily_loss:
        return _block("Daily loss threshold exceeded")
        
    total_value = cash
    for c, pos in positions.items():
        total_value += pos.get('shares', 0) * pos.get('avg_price', 0)
        
    if total_value > 0 and symbol in positions:
        current_value = positions[symbol].get('shares', 0) * price
        if (current_value / total_value) >= max_position_pct:
            return _block("Concentration/Max position exceeded")
            
    logger.info(f"[RISK_GATE_PASS] [{symbol}] Passed all risk checks")
    return _pass(action)

def fetch_market_data(symbol):
    try:
        from feeds.market_data import fetch_realtime_and_fundamentals
        raw_data = fetch_realtime_and_fundamentals(symbol)
        
        price = float(raw_data.get("latest_price", 0.0))
        if price <= 0:
            logger.error(f"[INVALID_MARKET_DATA] [{symbol}] Invalid price {price}")
            raise MarketDataError("Invalid or incomplete market data structure")
            
        return {
            "price": price,
            "volume": 0
        }
    except MarketDataError:
        raise
    except Exception as e:
        logger.error(f"[MARKET_DATA_FAIL] [{symbol}] {e}")
        raise MarketDataError(f"Data fetch failed: {str(e)}")

def main_loop():
    from core.state_manager import load_portfolio
    
    context = zmq.Context()
    pub_socket = context.socket(zmq.PUB)
    # HWM (High Water Mark) to drop messages if live_trader is dead
    pub_socket.setsockopt(zmq.SNDHWM, 1000)
    
    AI_TRADER_PUB_ENDPOINT = "tcp://127.0.0.1:5557"
    pub_socket.bind(AI_TRADER_PUB_ENDPOINT)
    logger.info(f"[ZMQ_BIND] source=ai_trader endpoint={AI_TRADER_PUB_ENDPOINT}")
    
    symbols = ["600519", "000858"]
    
    logger.info(f"Starting AI Trader main loop... ZeroMQ PUB bounded at {AI_TRADER_PUB_ENDPOINT}.")
    while True:
        portfolio = load_portfolio() or {"cash": 100000.0, "positions": {}}
        for symbol in symbols:
            try:
                market_data = fetch_market_data(symbol)
                
                raw_action, event_ids, events_json, decision_id = decide(symbol, market_data)
                final_action = final_risk_gate(symbol, raw_action, market_data, events=events_json, portfolio=portfolio, decision_id=decision_id)
                
                trade_id = str(uuid.uuid4())
                
                mapped_action = ""
                if final_action in ['confirm', 'buy']:
                    mapped_action = "BUY"
                    logger.info(f"[{symbol}] Executing trade: BUY")
                elif final_action == 'reduce':
                    mapped_action = "REDUCE"
                    logger.info(f"[{symbol}] Executing trade: REDUCE position")
                elif final_action in ['veto', 'hold']:
                    logger.info(f"[{symbol}] Trade execution {final_action.upper()}, VETOED/HOLD, not publishing.")
                    continue
                else:
                    logger.warning(f"[{symbol}] Unknown final action: {final_action}")
                    continue
                
                # TODO: 替换为正式的仓位管理函数。当前使用保守策略：计算约 10000 元的整百股数。
                price = market_data.get('price', 0)
                calculated_shares = 100
                if price > 0:
                    calculated_shares = max(100, int((10000 / price) // 100 * 100))
                
                # ZMQ Publishing
                trade_payload = {
                    "order_id": trade_id,
                    "trade_id": trade_id,
                    "decision_id": decision_id,
                    "source": "ai_trader",
                    "code": symbol,
                    "symbol": symbol,
                    "action": mapped_action,
                    "shares": calculated_shares,
                    "signal_price": price,
                    "reason": "AI verified rule signal",
                    "event_ids": event_ids,
                    "timestamp": datetime.now().isoformat()
                }
                
                try:
                    pub_socket.send_string(f"TRADE_SIGNAL {json.dumps(trade_payload, ensure_ascii=False)}", flags=zmq.NOBLOCK)
                except zmq.ZMQError as e:
                    logger.error(f"[ZMQ_PUBLISH_FAIL] Failed to publish trade signal for {symbol}: {e}")
                
                # Structured Logging for Audit
                audit_log = {
                    "trade_id": trade_id,
                    "decision_id": decision_id,
                    "symbol": symbol,
                    "action": mapped_action,
                    "event_ids": event_ids,
                    "publish_time": trade_payload["timestamp"],
                    "event_type": "TRADE_DECISION"
                }
                logger.info(f"[TRADE_TRACE] {json.dumps(audit_log)}")
                
            except MarketDataError as e:
                logger.error(f"[{symbol}] Market data exception: {e}. Skipping trade logic.")
                continue
            except Exception as e:
                logger.error(f"[{symbol}] Unexpected loop error: {e}")
                continue
                
        time.sleep(60)

if __name__ == '__main__':
    main_loop()
