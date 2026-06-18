"""
大盘环境锁模块 - 三档市场环境检测
RISK_OFF：禁止买入（上证跌破20日线且当日跌幅>1.5%）
CAUTION：仓位减半（跌破20日线但跌幅<1.5%）
RISK_ON：正常买入
"""
import sys
import time
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

from advanced_factors import http_session

# 进程级内存缓存，60秒 TTL
_CACHE_INDEX_DATA = None
_CACHE_TIMESTAMP = 0
_CACHE_TTL = 60  # 缓存有效时间（秒）


class MarketEnvironmentFilter:
    """
    大盘环境三档锁：
    - RISK_OFF：禁止买入（上证跌破20日线且当日跌幅>1.5%）
    - CAUTION：仓位减半（跌破20日线但跌幅<1.5%）
    - RISK_ON：正常买入
    """

    def get_index_data(self, symbol: str = "sh000001", days: int = 30) -> pd.DataFrame:
        """获取上证指数或沪深300近N日数据（带60秒TTL内存缓存）"""
        global _CACHE_INDEX_DATA, _CACHE_TIMESTAMP

        now = time.time()
        if _CACHE_INDEX_DATA is not None and (now - _CACHE_TIMESTAMP) < _CACHE_TTL:
            return _CACHE_INDEX_DATA

        df = ak.stock_zh_index_daily(symbol=symbol)
        df = df.tail(days).reset_index(drop=True)
        df["ma20"] = df["close"].rolling(20).mean()

        _CACHE_INDEX_DATA = df
        _CACHE_TIMESTAMP = now
        return df

    def get_market_index_fallback(self, symbol: str = "sh000001", days: int = 30) -> pd.DataFrame:
        """
        备用方案：通过新浪财经接口获取指数日线
        当 akshare 网络异常或超时使用
        """
        prefix = "sh" if symbol.startswith("sh") else "sz"
        code = symbol[2:]
        url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={days + 20}"
        try:
            resp = http_session.get(url, timeout=8)
            klines = resp.json()
            if not klines:
                raise ValueError("返回空数据")

            records = []
            for k in klines:
                records.append({
                    "date": k["day"],
                    "open": float(k["open"]),
                    "high": float(k["high"]),
                    "low": float(k["low"]),
                    "close": float(k["close"]),
                    "volume": float(k["volume"]),
                })
            df = pd.DataFrame(records)
            if df.empty:
                raise ValueError("无有效数据")
            df["ma20"] = df["close"].rolling(20).mean()
            return df
        except Exception as e:
            sys.stderr.write(f"[WARN] 新浪指数备用源也失败: {e}\n")
            return pd.DataFrame()

    def assess_market_regime(self) -> dict:
        """
        返回当前市场状态
        {
            'regime': 'RISK_ON' | 'CAUTION' | 'RISK_OFF',
            'position_limit': 1.0 | 0.5 | 0.0,
            'reason': str
        }
        """
        # 尝试 akshare，失败则走新浪备用
        try:
            df = self.get_index_data()
        except Exception:
            df = self.get_market_index_fallback()

        if df.empty or len(df) < 4:
            # 数据获取失败，默认 CAUTION 保守模式
            return {
                "regime": "CAUTION",
                "position_limit": 0.5,
                "reason": "大盘数据获取失败，降半仓保守运行",
            }

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        ma20 = latest["ma20"]
        close = latest["close"]
        daily_chg = (close - prev["close"]) / prev["close"]

        below_ma20 = close < ma20
        severe_drop = daily_chg < -0.015  # 单日跌超1.5%

        # 连续下跌判断（3日内跌幅）
        if len(df) >= 4:
            three_day_chg = (close - df.iloc[-4]["close"]) / df.iloc[-4]["close"]
            trend_weak = three_day_chg < -0.03
        else:
            trend_weak = False

        if below_ma20 and (severe_drop or trend_weak):
            return {
                "regime": "RISK_OFF",
                "position_limit": 0.0,
                "reason": f'上证跌破MA20且{"单日跌幅" if severe_drop else "3日跌幅"}异常，环境锁启动',
            }
        elif below_ma20:
            return {
                "regime": "CAUTION",
                "position_limit": 0.5,
                "reason": "上证跌破MA20，仓位减半观察",
            }
        else:
            return {
                "regime": "RISK_ON",
                "position_limit": 1.0,
                "reason": "大盘站上MA20，正常运行",
            }

    def can_open_position(self) -> tuple:
        """买入前调用此方法作为门卫"""
        regime = self.assess_market_regime()
        if regime["position_limit"] == 0.0:
            return False, regime["reason"]
        return True, regime["reason"]


if __name__ == "__main__":
    mf = MarketEnvironmentFilter()
    can_buy, reason = mf.can_open_position()
    regime = mf.assess_market_regime()
    print(f"市场状态: {regime['regime']}")
    print(f"可买入: {can_buy}")
    print(f"原因: {reason}")
