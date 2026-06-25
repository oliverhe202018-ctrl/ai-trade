import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
import pandas as pd

class MarketDataError(Exception):
    """行情接口统一异常类"""
    pass

class BaseMarketProvider(ABC):
    """
    统一实盘行情数据提供者接口。
    要求所有返回数据结构中强制带有 'timestamp' (float) 字段。
    """
    
    @abstractmethod
    def get_realtime_quote(self, symbol: str) -> Dict:
        """
        获取最新单一快照
        Returns:
            Dict: 包含 'price', 'volume', 'amount', 'timestamp' 等字段
        """
        pass
        
    @abstractmethod
    def get_bars(self, symbol: str, period: str = "1m", count: int = 120) -> pd.DataFrame:
        """
        获取K线数据
        Returns:
            pd.DataFrame: 包含 'open', 'high', 'low', 'close', 'volume', 'timestamp' 等列
        """
        pass
        
    @abstractmethod
    def get_orderbook(self, symbol: str) -> Dict:
        """
        获取盘口深度数据
        Returns:
            Dict: 包含 'askPrice', 'askVol', 'bidPrice', 'bidVol', 'timestamp' 等字段，均为List
        """
        pass
        
    @abstractmethod
    def get_market_snapshot(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        批量获取快照
        Returns:
            Dict[str, Dict]: key 为 symbol, value 为类似 get_realtime_quote 返回的字典
        """
        pass
        
    def is_data_fresh(self, symbol: str, max_delay_seconds: int = 5) -> bool:
        """
        校验最后一次获取的数据是否新鲜
        Returns:
            bool: 是否在最大延迟范围内
        """
        try:
            quote = self.get_realtime_quote(symbol)
            if not quote or "timestamp" not in quote:
                return False
            return time.time() - quote["timestamp"] <= max_delay_seconds
        except Exception:
            return False

    @abstractmethod
    def health_check(self) -> Dict:
        """
        获取数据源健康状态
        Returns:
            Dict: {
                "source": "...", 
                "status": "OK" | "STALE" | "DOWN", 
                "last_timestamp": "...", 
                "delay_seconds": 0, 
                "last_error": "..."
            }
        """
        pass
