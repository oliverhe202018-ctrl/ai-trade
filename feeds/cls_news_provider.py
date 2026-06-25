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

class ClsNewsProvider(BaseNewsProvider):
    def __init__(self):
        super().__init__("cls")
        self.timeout = 5
        self.base_url = "https://m.cls.cn/nodeapi/telegraphList"
        
    @retry_with_backoff(retries=3, backoff_in_seconds=(2, 5, 10))
    def _fetch_page(self) -> Dict[str, Any]:
        params = {
            "app": "CailianpressWeb",
            "os": "web",
            "sv": "8.2.2",
            "sign": "38a8e1dc4a6e344bd541ec5ba12920f0" # 简单绕过或通过特定格式
        }
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*"
        }
        
        response = requests.get(self.base_url, params=params, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        
        res_json = response.json()
        if not isinstance(res_json, dict) or "data" not in res_json or "roll_data" not in res_json["data"]:
            raise ValueError("CLS 响应格式异常，缺少 data.roll_data 字段")
            
        return res_json

    def fetch_latest(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            res = self._fetch_page()
            roll_data = res["data"]["roll_data"]
            
            if roll_data:
                first_item = roll_data[0]
                ts = first_item.get("ctime", 0)
                if ts:
                    event_dt = datetime.fromtimestamp(ts)
                    self._mark_success(last_event_time=event_dt.strftime("%Y-%m-%d %H:%M:%S"), event_count=len(roll_data))
                else:
                    self._mark_success(last_event_time="")
                    
            return roll_data[:limit]
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [403, 429]:
                logger.warning(f"[ClsNewsProvider] 被限流或拦截 (HTTP {e.response.status_code}): {e}")
            self._mark_error(e, traceback.format_exc())
            return []
        except Exception as e:
            self._mark_error(e, traceback.format_exc())
            logger.error(f"[ClsNewsProvider] 抓取失败: {e}")
            return []

    def fetch_since(self, timestamp: float) -> List[Dict[str, Any]]:
        return self.fetch_latest(50)

    def normalize(self, raw_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            content = raw_item.get("content", "").strip()
            title = raw_item.get("title", "").strip()
            if not title and content:
                # 若无标题，取前20个字作为标题
                title = content[:20] + "..." if len(content) > 20 else content
                
            if not title and not content:
                return None
                
            ts = raw_item.get("ctime")
            if not ts:
                return None
                
            event_time = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            ingest_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 解析关联的股票代码 (如果有)
            symbols = []
            subjects = raw_item.get("subjects", [])
            for subj in subjects:
                sec_code = subj.get("secu_code")
                if sec_code:
                    symbols.append(sec_code)
                    
            level = raw_item.get("level", "C")
            importance = "A" if level == "A" else "C" # 简化：如果是加红(A)则高优
            
            url = f"https://www.cls.cn/detail/{raw_item.get('id')}" if raw_item.get('id') else ""
            
            # Hash
            hash_str = f"cls{event_time}{title}{url}" if url else f"cls{event_time}{title}{content[:200]}"
            event_id = hashlib.sha256(hash_str.encode('utf-8')).hexdigest()
            
            return {
                "event_id": event_id,
                "source": "cls",
                "event_type": "flash",
                "event_time": event_time,
                "ingest_time": ingest_time,
                "symbols": symbols,
                "title": title,
                "content": content,
                "url": url,
                "importance": importance,
                "sentiment": None,
                "confidence": None,
                "raw": raw_item
            }
        except Exception as e:
            logger.error(f"[ClsNewsProvider] 标准化解析异常: {e}\n{traceback.format_exc()}")
            return None
