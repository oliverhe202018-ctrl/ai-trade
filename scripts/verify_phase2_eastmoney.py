"""
scripts/verify_phase2_eastmoney.py — EastMoney Phase 2 Recovery 验证脚本
默认 mock 验证, 不访问网络。
python scripts/verify_phase2_eastmoney.py          # mock only
python scripts/verify_phase2_eastmoney.py --live   # + live smoke test (少量请求)
"""
import sys, os, py_compile, json, argparse

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Enable live API smoke test")
    args = parser.parse_args()

    results = []
    def chk(name, ok):
        results.append((name, ok))
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")

    print("=" * 60)
    print("Phase 2 EastMoney Recovery Verification")
    print("=" * 60)

    # 1. py_compile
    print("\n--- py_compile ---")
    for f in ["feeds/eastmoney_news_provider.py", "feeds/news_event_bus.py"]:
        try:
            py_compile.compile(os.path.join(PROJECT_ROOT, f), doraise=True)
            chk(f"py_compile {f}", True)
        except py_compile.PyCompileError as e:
            chk(f"py_compile {f}", False)

    # 2. Imports
    print("\n--- Imports ---")
    try:
        from feeds.eastmoney_news_provider import EastMoneyNewsProvider, _resolve_symbol, _SEARCH_TYPES
        chk("import EastMoneyNewsProvider", True)
    except Exception as e:
        chk(f"import EastMoneyNewsProvider", False)
        print(f"  Error: {e}")
        return

    # 3. Init
    p = EastMoneyNewsProvider(max_pages=2)
    chk("provider init", p.max_pages == 2 and p.categories == ["stock", "announcement", "report"])
    chk("has _rate_limit_wait", hasattr(p, "_rate_limit_wait"))
    chk("has _infer_event_type", hasattr(p, "_infer_event_type"))
    chk("has _calculate_importance", hasattr(p, "_calculate_importance"))
    chk("has _fetch_paginated", hasattr(p, "_fetch_paginated"))
    chk("has _fetch_flash_news", hasattr(p, "_fetch_flash_news"))
    chk("has _fetch_sector_articles", hasattr(p, "_fetch_sector_articles"))

    # 4. Symbol resolution
    print("\n--- Symbol Resolution ---")
    for code, exp in [("600519.SH", "sh600519"), ("000001", "sz000001"),
                       ("159001", None), ("510300", None), ("204001", None), ("", None)]:
        result = _resolve_symbol(code)
        chk(f"  {code} -> {result}", result == exp)

    # 5. Search types
    print("\n--- Search Types ---")
    for t in ["stock", "announcement", "report", "news"]:
        chk(f"  _SEARCH_TYPES[{t}]", t in _SEARCH_TYPES)

    # 6. Event types
    print("\n--- Event Types ---")
    for title, exp in [
        ("2025年年度报告", "earnings"), ("退市风险警示", "risk"),
        ("回购公告", "corporate_action"), ("中标公告", "contract"),
        ("战略合作", "partnership"),
    ]:
        actual = p._infer_event_type(title, "stock")
        chk(f"  {title} -> {actual}", actual == exp)

    # 7. Importance
    print("\n--- Importance ---")
    detail = p._calculate_importance("重大合同突破", "x" * 600, "contract", "announcement")
    for field in ["base_score", "keyword_score", "category_bonus", "content_bonus",
                   "type_bonus", "final_score", "importance", "matched_keywords"]:
        chk(f"  importance.{field} exists", field in detail)
    chk("  importance HIGH", detail["importance"] == "HIGH")

    # 8. Normalize
    print("\n--- Normalize ---")
    n = p.normalize({
        "title": "测试公告", "content": "内容", "article_url": "https://x",
        "publish_time_raw": "2026-01-01 10:00:00", "category": "stock",
        "symbol_code": "sh600519", "_extracted_symbols": ["sh600519"],
    })
    chk("  normalize not None", n is not None)
    chk("  source=eastmoney", n["source"] == "eastmoney")
    chk("  has category", "category" in n)
    chk("  has importance_detail", "importance_detail" in n)
    chk("  symbols correct", n["symbols"] == ["sh600519"])
    chk("  event_id len 64", len(n.get("event_id", "")) == 64)

    # 9. NewsEventBus integration
    print("\n--- NewsEventBus ---")
    import yaml
    with open(os.path.join(PROJECT_ROOT, "config", "config.yaml"), "r") as f:
        cfg = yaml.safe_load(f)
    from feeds.news_event_bus import NewsEventBus
    bus = NewsEventBus()
    bus.initialize_from_config(cfg)
    chk("  eastmoney registered", "eastmoney" in bus.providers)
    em = bus.providers["eastmoney"]
    chk("  max_pages from config", em.max_pages == 5)
    chk("  categories from config", len(em.categories) >= 5)
    chk("  request_delay from config", em.request_delay_min == 1.0)

    # 10. Live smoke test (only with --live)
    if args.live:
        print("\n--- Live Smoke Test ---")
        print("  [WARN] Running real API requests (limited scope)")
        live_provider = EastMoneyNewsProvider(
            max_pages=1, categories=["stock"], sector_max_keywords=1,
            request_delay_min=0.5, request_delay_max=1.0,
        )
        for code in ["600519", "000001", "INVALID"]:
            try:
                items = live_provider.fetch_latest(limit=3, symbols=[code])
                chk(f"  live {code}: {len(items)} items", True)
            except Exception as e:
                chk(f"  live {code}: error", False)
                print(f"    Exception: {e}")
        # Flash
        try:
            flash_p = EastMoneyNewsProvider(max_pages=1, categories=["flash"],
                                            request_delay_min=0.5, request_delay_max=1.0)
            flash_items = flash_p.fetch_latest(limit=5)
            chk(f"  live flash: {len(flash_items)} items", True)
        except Exception as e:
            chk(f"  live flash: error", False)
            print(f"    Exception: {e}")
    else:
        print("\n--- Live Smoke Test ---")
        print("  [SKIP] Use --live flag to enable real API requests")

    # Summary
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\nRESULTS: {passed}/{total} PASS")
    if passed != total:
        for name, ok in results:
            if not ok:
                print(f"  FAIL: {name}")

if __name__ == "__main__":
    main()
