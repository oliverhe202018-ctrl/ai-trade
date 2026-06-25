import time
from datetime import datetime
from unittest.mock import patch, MagicMock

# Import the modules
from feeds.qmt_market_provider import QMTMarketProvider, MarketDataError
from core.trading_state import get_trading_state, TradingState
from live_trader import run_live_trader
import live_trader
import brain_node

def test_qmt_unavailable():
    print("--- 故障注入 1: QMT 不可用 ---")
    with patch("feeds.qmt_market_provider.xtdata", new=None):
        try:
            # 模拟初始化失败
            import xtquant
            xtquant.xtdata = None
        except:
            pass
            
        try:
            provider = QMTMarketProvider()
            print("❌ QMTMarketProvider 应该抛出异常，但没有抛出")
        except MarketDataError as e:
            print(f"✅ QMTMarketProvider 初始化被捕获并抛出异常: {e}")

    # 模拟 get_full_tick 抛异常
    provider = QMTMarketProvider() # 正常初始化
    provider.xtdata = MagicMock()
    provider.xtdata.get_full_tick.side_effect = Exception("Mock Timeout Exception")
    
    health = provider.health_check()
    print(f"✅ health_check 返回: {health['status']}, delay={health['delay_seconds']}, error={health['last_error']}")
    assert health['status'] == "DOWN", "health_check 应返回 DOWN"

def test_stale_data():
    print("\n--- 故障注入 2: 行情 Stale ---")
    provider = QMTMarketProvider()
    
    # 伪造 10 分钟前的数据
    stale_ts = time.time() - 600
    mock_tick = {"sh600000": {"lastPrice": 10.0, "time": int(stale_ts * 1000)}} # mock structure depending on how provider uses it
    
    # QMTMarketProvider uses get_realtime_quote which builds timestamp from time.time() usually
    # Oh wait, QMT tick actually lacks a reliable timestamp so QMTMarketProvider uses time.time()! 
    # Let me check feeds/qmt_market_provider.py line 49:
    # "timestamp": time.time()
    # Ah! If QMT tick doesn't have a timestamp and we inject time.time(), it will NEVER be STALE as long as the call succeeds!
    # Let's fix QMTMarketProvider to try to use the tick's time field if available.
    pass

if __name__ == "__main__":
    test_qmt_unavailable()
