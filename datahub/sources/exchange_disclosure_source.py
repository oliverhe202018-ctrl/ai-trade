import sys, os
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import requests
import logging
from datetime import datetime
import time

logger = logging.getLogger(__name__)

class ExchangeDisclosureSource:
    def __init__(self):
        self.name = "szse"
        self.enabled = True
        
    def fetch(self, symbols=None):
        """
        Fetch from SZSE or similar. 
        As a fallback/supplementary source. We will return a dummy or limited subset to avoid complexity in this mock example.
        In reality, one could query szse.cn APIs.
        """
        events = []
        try:
            # We will use AkShare as a proxy for exchange data since direct SZSE might have complex tokens
            import akshare as ak
            # Using akshare's stock_zh_a_alerts_cls to get real-time flash news as a proxy for exchange disclosures or market alerts
            # Just to have a secondary source that doesn't rely purely on cninfo
            df = ak.stock_zh_a_alerts_cls()
            if df is not None and not df.empty:
                for _, row in df.head(20).iterrows():
                    title = row.get("标题", "")
                    content = row.get("内容", "")
                    pub_time = row.get("时间", "")
                    
                    if not title and not content:
                        continue
                        
                    # Usually alerts don't have symbol mapped directly if it's general market, but some might.
                    # We will treat them as market-wide events if symbol is missing.
                    events.append({
                        "event_id": f"szse_alert_{int(time.time() * 1000)}",
                        "symbol": "", # Market wide or to be extracted
                        "name": "",
                        "event_type": "disclosure",
                        "title": title or content[:50],
                        "published_at": str(pub_time),
                        "source": self.name,
                        "url": "",
                        "raw_category": "flash_alert",
                        "importance": "medium",
                        "reason": ""
                    })
        except Exception as e:
            logger.error(f"[ExchangeDisclosureSource] Fetch failed: {e}")
            raise
            
        return events
