#!/usr/bin/env python
"""
scripts/bulk_news_collector.py — 批量资讯采集器

用途: 驱动 EastMoneyNewsProvider 批量拉取资讯，写入 NewsEventStore
运行: python scripts/bulk_news_collector.py [--symbol-count N] [--news-per-symbol M]

流程:
  1. 从 xtdata 获取全市场 5210 只股票
  2. 随机采样 N 只股票
  3. 对每只股票调用 EastMoney API (akshare)
  4. 标准化 → 去重 → 写入 SQLite
  5. 输出覆盖率变化统计
"""
import os
import sys
import json
import time
import argparse
import random
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
os.chdir(PROJECT_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from feeds.eastmoney_news_provider import EastMoneyNewsProvider
from feeds.cninfo_news_provider import CninfoNewsProvider
from feeds.news_event_store import NewsEventStore
from core.logger_config import logger


def get_full_universe() -> list:
    """获取全市场股票代码"""
    try:
        from xtquant import xtdata
        sh = xtdata.get_stock_list_in_sector('上证A股')
        sz = xtdata.get_stock_list_in_sector('深证A股')
        # Convert to standard format
        codes = []
        for c in sh + sz:
            if c.endswith('.SH'):
                codes.append(c.replace('.SH', ''))
            elif c.endswith('.SZ'):
                codes.append(c.replace('.SZ', ''))
        return codes
    except Exception:
        # Fallback: top 100 stocks
        return [
            "000001", "000002", "000333", "000651", "000858",
            "002415", "002475", "002594", "300059", "300750",
            "600000", "600036", "600276", "600519", "600900",
            "601012", "601166", "601318", "601398", "601899",
        ]


def collect_and_store(symbols: list, news_per_symbol: int) -> dict:
    """批量采集并写入"""

    provider = EastMoneyNewsProvider()
    store = NewsEventStore()

    stats = {
        "total_requested": len(symbols),
        "total_raw_items": 0,
        "normalized_count": 0,
        "stored_count": 0,
        "deduped_count": 0,
        "failed_symbols": 0,
        "new_covered_symbols": 0,
        "start_time": time.time(),
    }

    # 记录采集前的资讯覆盖股票数
    before_symbols = set()
    try:
        import sqlite3
        conn = sqlite3.connect(store.db_path, timeout=5.0)
        cur = conn.cursor()
        cur.execute("SELECT symbols FROM news_events")
        for (syms_str,) in cur.fetchall():
            try:
                for s in json.loads(syms_str or "[]"):
                    before_symbols.add(s)
            except: pass
        conn.close()
    except: pass

    raw_items = provider.fetch_latest(limit=news_per_symbol, symbols=symbols)
    stats["total_raw_items"] = len(raw_items)

    for item in raw_items:
        normalized = provider.normalize(item)
        if normalized:
            stats["normalized_count"] += 1
            if store.save_event(normalized):
                stats["stored_count"] += 1
            else:
                stats["deduped_count"] += 1

    # 记录采集后的资讯覆盖股票数
    after_symbols = set()
    try:
        import sqlite3
        conn = sqlite3.connect(store.db_path, timeout=5.0)
        cur = conn.cursor()
        cur.execute("SELECT symbols FROM news_events")
        for (syms_str,) in cur.fetchall():
            try:
                for s in json.loads(syms_str or "[]"):
                    after_symbols.add(s)
            except: pass
        conn.close()
    except: pass

    stats["new_covered_symbols"] = len(after_symbols - before_symbols)
    stats["before_covered"] = len(before_symbols)
    stats["after_covered"] = len(after_symbols)
    stats["elapsed_seconds"] = time.time() - stats["start_time"]

    return stats


def main():
    parser = argparse.ArgumentParser(description="批量资讯采集器")
    parser.add_argument("--symbol-count", type=int, default=25,
                       help="每轮拉取的股票数 (default: 25, ~25s)")
    parser.add_argument("--news-per-symbol", type=int, default=20,
                       help="每只股票最多拉取新闻数 (default: 20)")
    parser.add_argument("--cninfo", action="store_true",
                       help="同时运行 CNINFO 采集")
    args = parser.parse_args()

    print("=" * 70)
    print("批量资讯采集器")
    print(f"采集股票数: {args.symbol_count} | 每只最多: {args.news_per_symbol} 条")
    print("=" * 70)

    # Get universe and sample
    universe = get_full_universe()
    print(f"\n全市场股票池: {len(universe)} 只")
    sampled = random.sample(universe, min(args.symbol_count, len(universe)))
    print(f"采样: {len(sampled)} 只")
    print(f"样例: {sampled[:10]}")

    # CNINFO first (fast, batch)
    if args.cninfo:
        print("\n[Phase A] CNINFO 公告采集...")
        from feeds.cninfo_news_provider import CninfoNewsProvider
        cninfo = CninfoNewsProvider()
        store = NewsEventStore()
        raw = cninfo.fetch_latest(limit=30)
        cn_stored = 0
        for item in raw:
            norm = cninfo.normalize(item)
            if norm and store.save_event(norm):
                cn_stored += 1
        print(f"  CNINFO: {len(raw)} 条原始 → {cn_stored} 条入库")

    # EastMoney bulk
    print(f"\n[Phase B] EastMoney 批量采集 ({len(sampled)} 只股票)...")
    stats = collect_and_store(sampled, args.news_per_symbol)

    print("\n" + "=" * 70)
    print("采集统计")
    print("=" * 70)
    print(f"  请求股票数: {stats['total_requested']}")
    print(f"  原始新闻数: {stats['total_raw_items']}")
    print(f"  标准化成功: {stats['normalized_count']}")
    print(f"  入库成功:   {stats['stored_count']}")
    print(f"  去重跳过:   {stats['deduped_count']}")
    print(f"  失败股票数: {stats['failed_symbols']}")
    print(f"  新覆盖股票: {stats['new_covered_symbols']}")
    print(f"  采集前覆盖: {stats['before_covered']} → 采集后覆盖: {stats['after_covered']}")
    print(f"  耗时:       {stats['elapsed_seconds']:.1f}s")

    # Update coverage cache
    cache_path = Path(PROJECT_ROOT) / "data_cache" / "news_coverage_cache.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached["covered_symbols"] = stats["after_covered"]
            cached["coverage_rate"] = round(stats["after_covered"] / 5210, 4)
            cached["coverage_rate_pct"] = f"{stats['after_covered']/5210*100:.2f}%"
            cached["computed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cached["source"] = "bulk_collector"
            cache_path.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8")
        except: pass


if __name__ == "__main__":
    main()
