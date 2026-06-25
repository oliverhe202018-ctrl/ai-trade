import sys, os
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import logging
from datetime import datetime
import time

logger = logging.getLogger(__name__)

class MarketAnomalySource:
    def __init__(self):
        self.name = "market_anomaly"
        self.enabled = True
        
    def fetch(self, symbols=None):
        """
        Fetch market anomalies, e.g. limit up/down stocks, high turnover.
        Using akshare for simplicity since this is an analytical source.
        """
        events = []
        try:
            import akshare as ak
            df = ak.stock_lhb_stock_statistic_em(symbol="近一月") # Just a proxy for dragon tiger list
            if df is not None and not df.empty:
                for _, row in df.head(10).iterrows():
                    symbol = str(row.get("代码", ""))
                    name = row.get("名称", "")
                    reason = row.get("上榜原因", "")
                    
                    if not symbol:
                        continue
                        
                    if symbols and symbol not in symbols:
                        continue
                        
                    events.append({
                        "event_id": f"lhb_{symbol}_{int(time.time())}",
                        "symbol": symbol,
                        "name": name,
                        "event_type": "abnormal_volatility",
                        "title": f"【龙虎榜】{name} ({symbol}) 上榜",
                        "published_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        "source": self.name,
                        "url": "",
                        "raw_category": "龙虎榜",
                        "importance": "high",
                        "reason": reason
                    })
        except Exception as e:
            logger.error(f"[MarketAnomalySource] Fetch failed: {e}")
            raise
            
        return events
