import os
import sys
import json
import time
import tempfile
import threading
from typing import Dict, Any

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger
from feeds.cninfo_news_provider import CninfoNewsProvider
from feeds.cls_news_provider import ClsNewsProvider
from feeds.news_event_store import NewsEventStore

class NewsEventBus:
    def __init__(self):
        self.providers = {}
        self.store = NewsEventStore()
        self.health_file = os.path.join(PROJECT_ROOT, "data_cache", "news_health.json")
        self._lock = threading.Lock()
        
    def register_provider(self, name: str, provider):
        self.providers[name] = provider
        
    def initialize_from_config(self, config: Dict[str, Any]):
        news_cfg = config.get("news_data", {})
        if not news_cfg.get("enabled", False):
            logger.info("[NewsEventBus] 资讯源被全局禁用")
            return
            
        provider_cfg = news_cfg.get("providers", {})
        if provider_cfg.get("cninfo", {}).get("enabled", False):
            self.register_provider("cninfo", CninfoNewsProvider())
        if provider_cfg.get("cls", {}).get("enabled", False):
            self.register_provider("cls", ClsNewsProvider())
            
        logger.info(f"[NewsEventBus] 初始化完成，已挂载 {len(self.providers)} 个 Provider")

    def run_polling_cycle(self):
        """执行一次全量拉取，并写入健康状态"""
        for name, provider in self.providers.items():
            try:
                raw_items = provider.fetch_latest(50)
                if not raw_items:
                    continue
                    
                events_to_save = []
                for item in raw_items:
                    norm_event = provider.normalize(item)
                    if norm_event:
                        events_to_save.append(norm_event)
                        
                if events_to_save:
                    saved_count = self.store.save_events(events_to_save)
                    logger.debug(f"[NewsEventBus] {name} 获取 {len(events_to_save)} 条，成功写入 {saved_count} 条")
            except Exception as e:
                import traceback
                logger.error(f"[NewsEventBus] 轮询 {name} 时崩溃: {e}\n{traceback.format_exc()}")
                
        self._write_health_json()

    def _write_health_json(self):
        """原子写入健康度监控文件"""
        health_data = {
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
            "providers": {}
        }
        for name, provider in self.providers.items():
            health_data["providers"][name] = provider.health_check()
            
        try:
            with self._lock:
                os.makedirs(os.path.dirname(self.health_file), exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(self.health_file), suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(health_data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.health_file)
        except Exception as e:
            import traceback
            logger.error(f"[NewsEventBus] 写入 news_health.json 失败: {e}\n{traceback.format_exc()}")

# 全局单例
_bus_instance = None

def get_news_bus() -> NewsEventBus:
    global _bus_instance
    if _bus_instance is None:
        _bus_instance = NewsEventBus()
    return _bus_instance
