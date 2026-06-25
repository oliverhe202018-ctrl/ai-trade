import abc
import time
import traceback
from typing import List, Dict, Any, Optional

class BaseNewsProvider(abc.ABC):
    def __init__(self, name: str):
        self._name = name
        self._last_fetch_time = "1970-01-01 00:00:00"
        self._last_event_time = "1970-01-01 00:00:00"
        self._last_error = ""
        self._status = "UNKNOWN"
        self._event_count_24h = 0
        
    @property
    def source_name(self) -> str:
        return self._name

    @abc.abstractmethod
    def fetch_latest(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取最新资讯"""
        pass

    @abc.abstractmethod
    def fetch_since(self, timestamp: float) -> List[Dict[str, Any]]:
        """获取指定时间之后的资讯"""
        pass

    @abc.abstractmethod
    def normalize(self, raw_item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """将原始数据标准化为统一的 NewsEvent 格式"""
        pass

    def health_check(self) -> Dict[str, Any]:
        """返回当前 Provider 的健康度状态"""
        delay_seconds = 0
        try:
            import datetime
            if self._last_fetch_time != "1970-01-01 00:00:00":
                fetch_dt = datetime.datetime.strptime(self._last_fetch_time, "%Y-%m-%d %H:%M:%S")
                delay_seconds = (datetime.datetime.now() - fetch_dt).total_seconds()
        except Exception:
            pass

        return {
            "source": self.source_name,
            "status": self._status,
            "last_fetch_time": self._last_fetch_time,
            "last_event_time": self._last_event_time,
            "delay_seconds": max(0, int(delay_seconds)),
            "last_error": self._last_error,
            "event_count_24h": self._event_count_24h
        }

    def _mark_success(self, last_event_time: str, event_count: int = 0):
        import datetime
        self._status = "OK"
        self._last_error = ""
        self._last_fetch_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if last_event_time:
            self._last_event_time = last_event_time
        self._event_count_24h += event_count

    def _mark_error(self, e: Exception, trace: str):
        self._status = "DOWN"
        self._last_error = f"{str(e)} | {trace}"
