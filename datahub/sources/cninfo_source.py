import sys, os
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import requests
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class CninfoSource:
    def __init__(self):
        self.name = "cninfo"
        self.enabled = True
        
    def fetch(self, symbols=None):
        """
        Fetch latest announcements.
        If symbols is provided, we can filter, but cninfo API might be easier to just fetch latest market-wide and filter locally.
        For simplicity and free tier, we fetch the latest page.
        """
        events = []
        try:
            url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
            headers = {
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "X-Requested-With": "XMLHttpRequest"
            }
            # Fetch latest A-share announcements
            data = {
                "pageNum": "1",
                "pageSize": "30",
                "column": "szse",
                "hsecninfo": "",
                "tabName": "fulltext",
                "sortName": "",
                "sortType": "",
                "isHLtitle": "true"
            }
            response = requests.post(url, headers=headers, data=data, timeout=10)
            response.raise_for_status()
            res_json = response.json()
            announcements = res_json.get("announcements") or []
            
            for item in announcements:
                sec_code = item.get("secCode")
                if not sec_code:
                    continue
                    
                title = item.get("announcementTitle", "").replace("<em>", "").replace("</em>", "")
                
                # Format time
                pub_time_ms = item.get("announcementTime")
                if pub_time_ms:
                    pub_time = datetime.fromtimestamp(pub_time_ms/1000.0).strftime('%Y-%m-%d %H:%M:%S')
                else:
                    pub_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                # URL
                adjunct_url = item.get("adjunctUrl")
                link = f"http://www.cninfo.com.cn/new/disclosure/detail?orgId={item.get('orgId')}&announcementId={item.get('announcementId')}&announcementTime={item.get('announcementTime')}" if adjunct_url else ""
                
                if symbols and sec_code not in symbols:
                    continue
                    
                events.append({
                    "event_id": f"cninfo_{item.get('announcementId')}",
                    "symbol": sec_code,
                    "name": item.get("secName", ""),
                    "event_type": "announcement", # To be refined by radar
                    "title": title,
                    "published_at": pub_time,
                    "source": self.name,
                    "url": link,
                    "raw_category": item.get("announcementTypeName", ""),
                    "importance": "low",
                    "reason": ""
                })
        except Exception as e:
            logger.error(f"[CninfoSource] Fetch failed: {e}")
            raise
            
        return events
