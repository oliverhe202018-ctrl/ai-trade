import sys, os
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import time
import json
import logging
import random
from pathlib import Path
from datetime import datetime
from core.health_bus import write_heartbeat
from core.state_manager import load_portfolio
from datahub.circuit_breaker import CircuitBreaker

# Sources
from datahub.sources.cninfo_source import CninfoSource
from datahub.sources.exchange_disclosure_source import ExchangeDisclosureSource
from datahub.sources.market_anomaly_source import MarketAnomalySource

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

EVENTS_DIR = Path(PROJECT_ROOT) / "data_cache" / "events"

def get_watchlist():
    """Get the targeted symbols to monitor."""
    try:
        portfolio = load_portfolio()
        if portfolio and "positions" in portfolio and portfolio["positions"]:
            return list(portfolio["positions"].keys())
    except Exception as e:
        logger.warning(f"Failed to load portfolio for watchlist: {e}")
        
    fallback_env = os.getenv("NEWS_FALLBACK_WATCHLIST", "")
    if fallback_env:
        return [s.strip() for s in fallback_env.split(",")]
    
    return []

def classify_event(title: str) -> tuple[str, str, str]:
    """
    Very basic heuristic classification based on title keywords.
    Returns (event_type, importance, reason)
    """
    title = title.lower()
    
    if "异常波动" in title:
        return ("abnormal_volatility", "high", "股票交易异常波动")
    elif "停牌" in title or "复牌" in title:
        return ("suspension_resume", "high", "停复牌")
    elif "业绩预告" in title:
        return ("earnings_forecast", "high", "业绩预告")
    elif "业绩快报" in title:
        return ("earnings_flash", "high", "业绩快报")
    elif "回购" in title:
        return ("buyback", "medium", "股份回购")
    elif "减持" in title:
        return ("reduction", "high", "股东减持")
    elif "增持" in title:
        return ("increase", "medium", "股东增持")
    elif "问询函" in title:
        return ("inquiry", "high", "监管问询")
    elif "风险提示" in title:
        return ("risk_warning", "high", "风险提示")
    elif "担保" in title:
        return ("guarantee", "low", "对外担保")
    elif "重大合同" in title:
        return ("major_contract", "medium", "重大合同")
    elif "控制权变更" in title:
        return ("control_change", "high", "控制权变更")
        
    return ("announcement", "low", "常规公告")

def save_events_to_cache(events):
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    
    # latest_events.json
    latest_file = EVENTS_DIR / "latest_events.json"
    tmp_file = latest_file.with_suffix(".json.tmp")
    
    # We may want to merge with existing or just keep a rolling window.
    # For now, we just overwrite with the latest batch to keep it simple and stateless.
    # A real system would append or dedup. Let's do a simple read-append-dedup.
    existing_events = []
    if latest_file.exists():
        try:
            with latest_file.open("r", encoding="utf-8") as f:
                existing_events = json.load(f)
        except:
            pass
            
    # Dedup by event_id
    existing_map = {e["event_id"]: e for e in existing_events}
    for e in events:
        existing_map[e["event_id"]] = e
        
    # Sort by published_at desc, keep last 500
    merged = list(existing_map.values())
    merged.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    merged = merged[:500]
    
    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, latest_file)
    
def save_source_status(statuses):
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    status_file = EVENTS_DIR / "source_status.json"
    tmp_file = status_file.with_suffix(".json.tmp")
    
    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(statuses, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, status_file)

def run_radar():
    sources = [
        CninfoSource(),
        ExchangeDisclosureSource(),
        MarketAnomalySource()
    ]
    circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout_minutes=5)
    
    logger.info("Starting Event Radar loop...")
    
    while True:
        try:
            watchlist = get_watchlist()
            logger.info(f"[EVENT_RADAR] Watchlist: {watchlist}")
            
            all_events = []
            source_statuses = {}
            
            for source in sources:
                if not circuit_breaker.allow(source.name):
                    source_statuses[source.name] = {
                        "enabled": source.enabled,
                        "status": "CIRCUIT_OPEN",
                        "items": 0,
                        "last_error": "Circuit breaker open"
                    }
                    continue
                    
                try:
                    logger.info(f"Fetching from {source.name}...")
                    raw_events = source.fetch(symbols=watchlist)
                    
                    # Classify events
                    for e in raw_events:
                        evt_type, importance, reason = classify_event(e.get("title", ""))
                        # Only override if it wasn't strictly set by source or if it's default
                        if e.get("event_type") in ("announcement", "disclosure", ""):
                            e["event_type"] = evt_type
                        e["importance"] = importance
                        e["reason"] = reason
                        all_events.append(e)
                        
                    circuit_breaker.success(source.name)
                    source_statuses[source.name] = {
                        "enabled": source.enabled,
                        "status": "OK",
                        "items": len(raw_events),
                        "last_error": None
                    }
                except Exception as e:
                    circuit_breaker.failure(source.name)
                    logger.error(f"Source {source.name} failed: {e}")
                    source_statuses[source.name] = {
                        "enabled": source.enabled,
                        "status": "ERROR",
                        "items": 0,
                        "last_error": str(e)
                    }
            
            if all_events:
                save_events_to_cache(all_events)
                
            save_source_status(source_statuses)
            
            if all_events:
                write_heartbeat(
                    channel="L2",
                    status="OK",
                    source="datahub/event_radar.py",
                    message=f"events fetched: {len(all_events)}",
                    extra={"events_count": len(all_events), "sources": list(source_statuses.keys())}
                )
            else:
                write_heartbeat(
                    channel="L2",
                    status="EMPTY",
                    source="datahub/event_radar.py",
                    message="no events fetched",
                    extra={"events_count": 0, "sources": list(source_statuses.keys())}
                )
            
        except Exception as e:
            logger.error(f"Radar loop error: {e}")
            try:
                write_heartbeat(
                    channel="L2",
                    status="ERROR",
                    source="datahub/event_radar.py",
                    message=str(e)
                )
            except:
                pass
                
        sleep_time = random.uniform(60, 120)
        logger.info(f"Sleeping for {sleep_time:.2f} seconds...")
        time.sleep(sleep_time)

if __name__ == '__main__':
    run_radar()
