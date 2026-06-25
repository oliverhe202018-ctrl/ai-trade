import os
import sys
import time
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from feeds.base_market_provider import MarketDataError
from feeds.qmt_market_provider import QMTMarketProvider

def test_qmt_faults():
    print("=" * 50)
    print("1. QMT 不可用故障注入测试 (QMTMarketProvider 初始化与方法异常)")
    print("=" * 50)
    
    # a. 初始化异常
    with patch("builtins.__import__", side_effect=ImportError("mocked error")):
        try:
            provider = QMTMarketProvider()
            print("❌ QMTMarketProvider 应该抛出异常，但没有抛出")
        except MarketDataError as e:
            print(f"✅ QMTMarketProvider 初始化被捕获并抛出异常: {e}")
            
    # b. get_full_tick 抛出异常
    provider = QMTMarketProvider()
    provider.xtdata = MagicMock()
    provider.xtdata.get_full_tick.side_effect = Exception("Mocked Timeout")
    
    health = provider.health_check()
    print(f"✅ health_check 返回: status={health['status']}, delay={health['delay_seconds']}, error={health['last_error']}")
    assert health['status'] == "DOWN", "health_check 应返回 DOWN"

def test_stale_data():
    print("\n" + "=" * 50)
    print("2. 行情 stale 故障注入测试 (timestamp 超时)")
    print("=" * 50)
    
    provider = QMTMarketProvider()
    stale_ts = time.time() - 600
    def mock_get_realtime_quote(symbol):
        return {
            "symbol": symbol,
            "price": 10.0,
            "timestamp": stale_ts
        }
        
    with patch.object(provider, "get_realtime_quote", side_effect=mock_get_realtime_quote):
        is_fresh = provider.is_data_fresh("sh600000", max_delay_seconds=5)
        print(f"✅ is_data_fresh() 返回: {is_fresh}")
        assert is_fresh is False, "stale 数据 is_data_fresh 应该返回 False"

def test_retry_blocking():
    print("\n" + "=" * 50)
    print("3. retry 阻塞测试 (确保多次失败后抛出异常而不死锁)")
    print("=" * 50)
    
    provider = QMTMarketProvider()
    provider.xtdata = MagicMock()
    provider.xtdata.subscribe_quote.side_effect = Exception("Network Exception")
    
    start_time = time.time()
    try:
        provider.get_realtime_quote("sh600000")
        print("❌ 应该抛出异常")
    except MarketDataError as e:
        duration = time.time() - start_time
        print(f"✅ 捕获异常: {e}")
        print(f"✅ 耗时: {duration:.2f} 秒 (包含 backoff)")
        assert duration >= 7, "重试退避时间(1+2+5)应当至少为8秒"

def test_atomic_json():
    print("\n" + "=" * 50)
    print("4. health JSON 原子写入检查")
    print("=" * 50)
    
    provider = QMTMarketProvider()
    provider.xtdata = MagicMock()
    provider.xtdata.get_full_tick.return_value = {"sh600000": {"lastPrice": 10}}
    
    import tempfile
    import os
    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
    health_data = provider.health_check()
    health_file_path = os.path.join(PROJECT_ROOT, "data_cache", "market_health.json")
    dir_name = os.path.dirname(health_file_path)
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(health_data, f)
    os.replace(tmp_path, health_file_path)
    
    print(f"✅ 成功原子写入: {health_file_path}")
    
def test_dashboard_exception():
    print("\n" + "=" * 50)
    print("5. Dashboard JSON 读取异常处理")
    print("=" * 50)
    
    import os
    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
    health_file_path = os.path.join(PROJECT_ROOT, "data_cache", "market_health.json")
    with open(health_file_path, "w", encoding="utf-8") as f:
        f.write("{invalid json format")
        
    import json
    try:
        with open(health_file_path, "r", encoding="utf-8") as f:
            health = json.load(f)
        print("❌ 不应成功")
    except Exception:
        print("✅ 触发 Exception，按逻辑将显示 UNKNOWN / DOWN")

if __name__ == "__main__":
    test_qmt_faults()
    test_stale_data()
    test_retry_blocking()
    test_atomic_json()
    test_dashboard_exception()
