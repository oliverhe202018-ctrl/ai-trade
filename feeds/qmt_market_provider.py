import time
import logging
import traceback
import pandas as pd
from typing import Dict, List, Optional
from feeds.base_market_provider import BaseMarketProvider, MarketDataError
from core.utils import retry_with_backoff

logger = logging.getLogger(__name__)

class QMTMarketProvider(BaseMarketProvider):
    def __init__(self):
        try:
            from xtquant import xtdata
            self.xtdata = xtdata
            logger.info("[QMTMarketProvider] xtdata 初始化成功")
        except ImportError as e:
            logger.critical(f"[QMTMarketProvider] xtquant 未安装或导入失败: {traceback.format_exc()}")
            raise MarketDataError("缺少 xtquant 模块，QMT 行情通道无法启动") from e

    def _to_qmt_code(self, symbol: str) -> str:
        if symbol.endswith(".SH") or symbol.endswith(".SZ"):
            return symbol
        prefix = symbol[:2].lower()
        code = symbol[2:]
        if prefix == "sh":
            return f"{code}.SH"
        elif prefix == "sz":
            return f"{code}.SZ"
        return symbol

    def _to_std_code(self, qmt_code: str) -> str:
        if qmt_code.endswith(".SH"):
            return f"sh{qmt_code[:6]}"
        if qmt_code.endswith(".SZ"):
            return f"sz{qmt_code[:6]}"
        return qmt_code

    @retry_with_backoff(retries=3, backoff_in_seconds=(1, 2, 5))
    def get_realtime_quote(self, symbol: str) -> Dict:
        qmt_code = self._to_qmt_code(symbol)
        try:
            self.xtdata.subscribe_quote(qmt_code, period='1d', start_time='', end_time='', count=0, callback=None)
            tick = self.xtdata.get_full_tick([qmt_code]).get(qmt_code)
            if not tick:
                raise MarketDataError(f"未获取到 {qmt_code} 的 tick 数据")
            
            return {
                "symbol": symbol,
                "price": tick.get("lastPrice", tick.get("lastClose", 0.0)),
                "volume": tick.get("volume", 0),
                "amount": tick.get("amount", 0.0),
                "open": tick.get("open", 0.0),
                "high": tick.get("high", 0.0),
                "low": tick.get("low", 0.0),
                "timestamp": time.time()
            }
        except Exception as e:
            logger.error(f"[QMTMarketProvider] 获取 {symbol} 实时报价异常: \n{traceback.format_exc()}")
            raise MarketDataError(f"获取 {symbol} 实时报价失败: {e}")

    @retry_with_backoff(retries=3, backoff_in_seconds=(1, 2, 5))
    def get_bars(self, symbol: str, period: str = "1d", count: int = 120) -> pd.DataFrame:
        qmt_code = self._to_qmt_code(symbol)
        try:
            self.xtdata.subscribe_quote(qmt_code, period=period, start_time='', end_time='', count=count, callback=None)
            data = self.xtdata.get_market_data_ex(
                field_list=['open', 'high', 'low', 'close', 'volume', 'amount'],
                stock_list=[qmt_code],
                period=period,
                count=count
            )
            df = data.get(qmt_code)
            if df is None or df.empty:
                raise MarketDataError(f"未获取到 {qmt_code} 的 K线数据")
            
            df = df.copy()
            df['timestamp'] = df.index
            return df
        except Exception as e:
            logger.error(f"[QMTMarketProvider] 获取 {symbol} {period} K线异常: \n{traceback.format_exc()}")
            raise MarketDataError(f"获取 {symbol} {period} K线失败: {e}")

    @retry_with_backoff(retries=3, backoff_in_seconds=(1, 2, 5))
    def get_orderbook(self, symbol: str) -> Dict:
        qmt_code = self._to_qmt_code(symbol)
        try:
            self.xtdata.subscribe_quote(qmt_code, period='1d', start_time='', end_time='', count=0, callback=None)
            tick = self.xtdata.get_full_tick([qmt_code]).get(qmt_code)
            if not tick:
                raise MarketDataError(f"未获取到 {qmt_code} 的 tick 盘口数据")
            
            ask_prices = tick.get("askPrice", [])
            ask_vols = tick.get("askVol", [])
            bid_prices = tick.get("bidPrice", [])
            bid_vols = tick.get("bidVol", [])
            
            if not ask_prices or not bid_prices:
                logger.warning(f"[QMTMarketProvider] {symbol} 盘口字段为空或不完整")
                
            return {
                "symbol": symbol,
                "askPrice": ask_prices,
                "askVol": ask_vols,
                "bidPrice": bid_prices,
                "bidVol": bid_vols,
                "timestamp": time.time()
            }
        except Exception as e:
            logger.error(f"[QMTMarketProvider] 获取 {symbol} 盘口异常: \n{traceback.format_exc()}")
            raise MarketDataError(f"获取 {symbol} 盘口失败: {e}")

    @retry_with_backoff(retries=3, backoff_in_seconds=(2, 4, 8))
    def get_market_snapshot(self, symbols: List[str]) -> Dict[str, Dict]:
        qmt_codes = [self._to_qmt_code(s) for s in symbols]
        try:
            for qc in qmt_codes:
                self.xtdata.subscribe_quote(qc, period='1d', start_time='', end_time='', count=0, callback=None)
            
            ticks = self.xtdata.get_full_tick(qmt_codes)
            
            result = {}
            ts = time.time()
            for std_code, qc in zip(symbols, qmt_codes):
                tick = ticks.get(qc)
                if tick:
                    result[std_code] = {
                        "symbol": std_code,
                        "price": tick.get("lastPrice", tick.get("lastClose", 0.0)),
                        "volume": tick.get("volume", 0),
                        "amount": tick.get("amount", 0.0),
                        "open": tick.get("open", 0.0),
                        "high": tick.get("high", 0.0),
                        "low": tick.get("low", 0.0),
                        "timestamp": ts,
                        "turnover_rate": tick.get("turnoverRatio", 0.0),
                        "askPrice": tick.get("askPrice", []),
                        "askVol": tick.get("askVol", []),
                        "bidPrice": tick.get("bidPrice", []),
                        "bidVol": tick.get("bidVol", [])
                    }
            return result
        except Exception as e:
            logger.error(f"[QMTMarketProvider] 批量获取快照异常: \n{traceback.format_exc()}")
            raise MarketDataError(f"批量获取快照失败: {e}")

    def health_check(self) -> Dict:
        from datetime import datetime
        result = {
            "source": "QMT",
            "status": "DOWN",
            "last_timestamp": None,
            "delay_seconds": 9999,
            "last_error": ""
        }
        try:
            # We can use a highly liquid stock like sh600000 for health check
            tick = self.get_realtime_quote("sh600000")
            ts = tick.get("timestamp", 0)
            if not ts:
                result["last_error"] = "未能获取有效时间戳"
                return result
                
            delay = time.time() - ts
            result["last_timestamp"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            result["delay_seconds"] = int(delay)
            
            if delay > 5:
                result["status"] = "STALE"
            else:
                result["status"] = "OK"
        except Exception as e:
            result["last_error"] = str(e)
            
        return result
