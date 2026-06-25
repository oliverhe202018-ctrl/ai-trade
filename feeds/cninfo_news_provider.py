import os
import sys
import json
import hashlib
import traceback
import requests
from datetime import datetime
from typing import List, Dict, Any, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from feeds.base_news_provider import BaseNewsProvider
from core.utils import retry_with_backoff
from core.logger_config import logger

class CninfoNewsProvider(BaseNewsProvider):
    def __init__(self):
        super().__init__("cninfo")
        self.timeout = 8
        self.base_url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
        
    @retry_with_backoff(retries=3, backoff_in_seconds=(2, 5, 10))
    def _fetch_page(self, page_num: int = 1) -> Dict[str, Any]:
        headers = {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "http://www.cninfo.com.cn",
            "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest"
        }
        
        data = {
            "pageNum": page_num,
            "pageSize": 30,
            "column": "szse",
            "hsecName": "",
            "tabName": "fulltext",
            "sortName": "",
            "sortType": "",
            "limit": "",
            "showTitle": "",
            "seDate": ""
        }
        
        response = requests.post(self.base_url, headers=headers, data=data, timeout=self.timeout)
        response.raise_for_status()
        
        res_json = response.json()
        if not isinstance(res_json, dict) or "announcements" not in res_json:
            raise ValueError("CNINFO 响应格式异常，缺少 announcements 字段")
            
        return res_json

    def fetch_latest(self, limit: int = 50) -> List[Dict[str, Any]]:
        raw_items = []
        try:
            res = self._fetch_page(1)
            announcements = res.get("announcements", [])
            
            if announcements:
                # 记录第一条事件的时间
                first_time = announcements[0].get("announcementTime", 0)
                if first_time:
                    event_dt = datetime.fromtimestamp(first_time / 1000.0)
                    self._mark_success(last_event_time=event_dt.strftime("%Y-%m-%d %H:%M:%S"), event_count=len(announcements))
                else:
                    self._mark_success(last_event_time="")
                    
            raw_items.extend(announcements)
            return raw_items[:limit]
        except Exception as e:
            self._mark_error(e, traceback.format_exc())
            logger.error(f"[CninfoNewsProvider] 抓取失败: {e}")
            return []

    def fetch_since(self, timestamp: float) -> List[Dict[str, Any]]:
        # TODO: 按时间戳翻页
        return self.fetch_latest(50)

    def normalize(self, raw_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            title = raw_item.get("announcementTitle", "").strip()
            if not title:
                return None
                
            ts_ms = raw_item.get("announcementTime")
            if not ts_ms:
                return None
                
            event_time = datetime.fromtimestamp(ts_ms / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
            ingest_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            sec_code = raw_item.get("secCode", "")
            sec_name = raw_item.get("secName", "")
            
            # 格式化代码 (例如 000001 -> 000001.SZ)
            symbols = []
            if sec_code:
                if sec_code.startswith("6"):
                    symbols.append(f"{sec_code}.SH")
                else:
                    symbols.append(f"{sec_code}.SZ")
                    
            url_path = raw_item.get("adjunctUrl", "")
            url = f"http://www.cninfo.com.cn/new/disclosure/detail?announcementId={raw_item.get('announcementId')}" if raw_item.get('announcementId') else ""
            if not url and url_path:
                url = f"http://static.cninfo.com.cn/{url_path}"
                
            # 基础分类映射
            event_type = "announcement"
            if any(k in title for k in ["风险", "异常波动", "减持", "退市", "立案", "违规"]):
                event_type = "risk"
            elif any(k in title for k in ["业绩预告", "业绩快报", "年报", "季报", "半年报"]):
                event_type = "earnings"
            elif any(k in title for k in ["中标", "重大合同", "战略合作"]):
                event_type = "announcement"
                
            # 计算 Hash
            hash_str = f"cninfo{event_time}{title}{url}"
            event_id = hashlib.sha256(hash_str.encode('utf-8')).hexdigest()
            
            return {
                "event_id": event_id,
                "source": "cninfo",
                "event_type": event_type,
                "event_time": event_time,
                "ingest_time": ingest_time,
                "symbols": symbols,
                "title": f"[{sec_name}] {title}",
                "content": title,
                "url": url,
                "importance": "UNKNOWN",
                "sentiment": None,
                "confidence": None,
                "raw": raw_item
            }
        except Exception as e:
            logger.error(f"[CninfoNewsProvider] 标准化解析异常: {e}\n{traceback.format_exc()}")
            return None
