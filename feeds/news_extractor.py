import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger

def fetch_layer1_em_akshare(*args, **kwargs):
    """
    [DEPRECATED] 旧版直接调用 akshare 接口已废弃。
    当前已转移至 NewsEventBus 通过 CNINFO/CLS 统一拉取。
    此函数仅作占位兼容，防止旧调用者崩溃。
    """
    logger.warning("[DEPRECATED] fetch_layer1_em_akshare() is deprecated, returning empty data. System now relies on NewsEventBus.")
    return None

def fetch_layer2_eastmoney_direct(*args, **kwargs):
    logger.warning("[DEPRECATED] fetch_layer2_eastmoney_direct() is deprecated, returning empty data.")
    return None

def fetch_layer3_local_cache(*args, **kwargs):
    logger.warning("[DEPRECATED] fetch_layer3_local_cache() is deprecated, returning empty data.")
    return None

def fetch_stock_news_from_all_sources(code: str, hours: int = 24, force_update: bool = False):
    """
    向后兼容的入口。
    直接查询新闻总线库中的数据。
    如果资讯总线未配置，则安全返回空列表。
    """
    try:
        from feeds.news_event_bus import get_news_bus
        bus = get_news_bus()
        events = bus.store.get_recent_events(limit=50)
        
        # 兼容旧版的返回结构，如果旧版调用者需要字典列表
        compatible_news = []
        for e in events:
            # 只返回匹配 code 的新闻
            symbols = e.get("symbols", [])
            # 旧版可能期望 600000 这样，新版可能存储了 sh600000 或 600000.SH
            if not code or any(code.replace('sh', '').replace('sz', '').replace('.SH', '').replace('.SZ', '') in s for s in symbols) or not symbols:
                 compatible_news.append({
                     "title": e.get("title", ""),
                     "content": e.get("content", ""),
                     "url": e.get("url", ""),
                     "time": e.get("event_time", "")
                 })
                 
        return compatible_news
    except Exception as e:
        import traceback
        logger.error(f"[NEWS_COMPAT] 尝试从总线获取资讯时崩溃: {e}\n{traceback.format_exc()}")
        return None
