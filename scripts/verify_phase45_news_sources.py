"""
scripts/verify_phase45_news_sources.py — Phase 4+5 新闻源正式验收脚本
保留在 scripts/ 供后续回归使用。运行: python scripts/verify_phase45_news_sources.py
"""
import sys, os, py_compile, re, json

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

results = []
failures = []
def check(name, ok):
    results.append((name, ok))
    if not ok:
        failures.append(name)
    return ok

print("=" * 60)
print("Phase 4+5 News Sources Verification")
print("=" * 60)

# 1. py_compile
for f in ["feeds/cninfo_news_provider.py", "feeds/rss_news_provider.py",
           "feeds/sse_news_provider.py", "feeds/szse_news_provider.py",
           "feeds/news_event_bus.py"]:
    try:
        py_compile.compile(os.path.join(PROJECT_ROOT, f), doraise=True)
        check(f"py_compile {f}", True)
    except py_compile.PyCompileError as e:
        check(f"py_compile {f}", False)

# 2. Config
import yaml
with open(os.path.join(PROJECT_ROOT, "config", "config.yaml"), "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)
prov = cfg["news_data"]["providers"]
for name in ["cninfo", "sse", "szse", "rss", "eastmoney", "cls"]:
    check(f"config has {name}", name in prov)
    check(f"config {name} enabled", prov[name].get("enabled") is True)

# 3. NewsEventBus
from feeds.news_event_bus import NewsEventBus
from feeds.cninfo_news_provider import CninfoNewsProvider
from feeds.rss_news_provider import RssNewsProvider
from feeds.sse_news_provider import SseNewsProvider
from feeds.szse_news_provider import SzseNewsProvider
from feeds.eastmoney_news_provider import EastMoneyNewsProvider

bus = NewsEventBus()
bus.initialize_from_config(cfg)
registered = list(bus.providers.keys())
check("bus providers count 6", len(registered) == 6)
check("cninfo registered", "cninfo" in registered)
check("eastmoney registered", "eastmoney" in registered)
check("sse registered", "sse" in registered)
check("szse registered", "szse" in registered)
check("rss registered", "rss" in registered)
check("cls registered", "cls" in registered)

# 4. CNINFO v2
cninfo = bus.providers["cninfo"]
check("cninfo max_pages=5", cninfo.max_pages == 5)
check("cninfo recent_hours=24", cninfo.recent_hours == 24)
check("cninfo has _rate_limit_wait", hasattr(cninfo, "_rate_limit_wait"))
check("cninfo has _build_date_window", hasattr(cninfo, "_build_date_window"))
check("cninfo has _infer_event_type", hasattr(cninfo, "_infer_event_type"))
dw = cninfo._build_date_window()
check("cninfo date window format", re.match(r"\d{4}-\d{2}-\d{2}~\d{4}-\d{2}-\d{2}", dw))
n = cninfo.normalize({"announcementTitle":"2025年年度报告","secCode":"000001","secName":"A","announcementTime":1782489600000,"adjunctUrl":"f.pdf","announcementId":"123","_cninfo_column":"szse"})
check("cninfo normalize ok", n is not None and n["source"]=="cninfo" and n["symbols"]==["sz000001"] and n["event_type"]=="earnings")
check("cninfo earnings", cninfo._infer_event_type("2025年年度报告")=="earnings")
check("cninfo risk", cninfo._infer_event_type("退市风险警示")=="risk")
check("cninfo corp_action", cninfo._infer_event_type("减持公告")=="corporate_action")
check("cninfo governance", cninfo._infer_event_type("股东大会通知")=="governance")

# 5. RSS
rss = bus.providers["rss"]
check("rss 2 feeds", len(rss.feeds) == 2)
nr = rss.normalize({"title":"平安银行年报增长15%","ctime":"1782489600","url":"https://x","_rss_feed_name":"sina_finance","_rss_feed_type":"json_api"})
check("rss normalize ok", nr is not None and nr["source"]=="rss_sina_finance" and nr["event_type"]=="earnings" and nr["importance"]=="LOW")
syms = rss._extract_symbols("000001 600519 300750")
check("rss extract sz000001", "sz000001" in syms)
check("rss extract sh600519", "sh600519" in syms)
check("rss extract sz300750", "sz300750" in syms)
syms2 = rss._extract_symbols("平安银行000001涨幅居前")
check("rss extract CJK context", "sz000001" in syms2)

# 6. SSE/SZSE
sse = bus.providers["sse"]
ns = sse.normalize({"SECURITY_CODE":"600000","SECURITY_NAME":"X","TITLE":"T","ADDDATE":"2026-06-26 19:00:00","SSEDATE":"2026-06-27","SSETimeStr":"09:30:00","BULLETIN_TYPE":"","BULLETIN_HEADING":"","URL":"/a/b.pdf"})
check("sse norm ok", ns is not None and ns["source"]=="sse" and ns["symbols"]==["sh600000"])
szse = bus.providers["szse"]
nz = szse.normalize({"title":"T","secCode":["002459"],"secName":["X"],"publishTime":"2026-06-27 00:00:00","attachPath":"/a/b.pdf","content":"C"})
check("szse norm ok", nz is not None and nz["source"]=="szse" and nz["symbols"]==["sz002459"])

# 7. Trade isolation
for fn in ["cninfo_news_provider.py","rss_news_provider.py","sse_news_provider.py","szse_news_provider.py"]:
    with open(os.path.join(PROJECT_ROOT,"feeds",fn),"r",encoding="utf-8") as f:
        c = f.read()
    for fb in ["live_trader","brain_node","broker_adapter","trading_state"]:
        check(f"{fn} no {fb}", not re.search(rf'from.*{fb}|import.*{fb}', c))

# 8. Health
for name in ["cninfo","sse","szse","rss"]:
    h = bus.providers[name].health_check()
    check(f"{name} health status", "status" in h)
    check(f"{name} health source", h["source"] == name)

# Summary
passed = sum(1 for _, ok in results if ok)
total = len(results)
print(f"\nRESULTS: {passed}/{total} PASS")
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
sys.exit(0 if passed == total else 1)
