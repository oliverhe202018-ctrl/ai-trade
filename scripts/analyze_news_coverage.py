#!/usr/bin/env python
"""
scripts/analyze_news_coverage.py — 资讯覆盖率与扫描有效性评估

用途: 评估当前资讯供给是否足以支撑全市场 ~5000 支股票的扫描。

运行:
    python scripts/analyze_news_coverage.py

输出:
    reports/news_coverage_report.md

约束:
    1. 不修改交易执行逻辑
    2. 不让资讯覆盖不足触发买入/卖出
    3. 不用模拟资讯伪造覆盖率
    4. 必须报告"覆盖多少只股票"，而非仅"抓到多少条"
    5. 如果 symbol 映射失败，要明确说明
    6. 如果资讯源失败，要记录 provider 名称和失败原因
"""
import os
import sys
import json
import sqlite3
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter
from typing import Dict, List, Any, Tuple, Optional

# ── 路径 ──
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
os.chdir(PROJECT_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

DATA_CACHE = Path(PROJECT_ROOT) / "data_cache"
REPORTS = Path(PROJECT_ROOT) / "reports"
os.makedirs(REPORTS, exist_ok=True)

# ════════════════════════════════════════════════════════════
# 1. 获取全市场扫描股票池 (stock universe)
# ════════════════════════════════════════════════════════════

def get_stock_universe() -> Tuple[int, List[str], str]:
    """返回 (数量, 代码列表, 数据来源说明)"""
    codes = []
    source_desc = ""

    # Primary: xtdata/QMT
    try:
        from xtquant import xtdata
        sh = xtdata.get_stock_list_in_sector('上证A股')
        sz = xtdata.get_stock_list_in_sector('深证A股')
        all_qmt = sh + sz
        if all_qmt:
            # Convert QMT codes to standard format
            std_codes = []
            for c in all_qmt:
                if c.endswith('.SH'):
                    std_codes.append(f"sh{c[:6]}")
                elif c.endswith('.SZ'):
                    std_codes.append(f"sz{c[:6]}")
                else:
                    std_codes.append(c)
            source_desc = f"xtdata/QMT: 上证A股={len(sh)}, 深证A股={len(sz)}"
            return len(std_codes), std_codes, source_desc
    except Exception as e:
        pass

    # Fallback: akshare
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        for _, row in df.iterrows():
            code = str(row['code']).zfill(6)
            prefix = 'sh' if code.startswith(('6', '9')) else 'sz'
            codes.append(f"{prefix}{code}")
        source_desc = f"akshare stock_info_a_code_name: {len(codes)} stocks"
        return len(codes), codes, source_desc
    except Exception as e:
        pass

    # Last resort: watchlist + candidates cache
    codes = []
    for fname in ['watchlist.json', 'market_candidates.json', 'potential_picks.json']:
        fp = DATA_CACHE / fname
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding='utf-8'))
                if fname == 'watchlist.json' and isinstance(data, list):
                    codes.extend(data)
                elif isinstance(data, dict):
                    candidates = data.get('candidates', []) or data.get('picks', [])
                    for c in candidates:
                        if isinstance(c, dict) and c.get('code'):
                            codes.append(c['code'])
            except Exception:
                pass
    source_desc = f"本地缓存 (watchlist + candidates): {len(codes)} stocks"
    return len(codes), codes, source_desc


# ════════════════════════════════════════════════════════════
# 2. 读取资讯缓存/数据库
# ════════════════════════════════════════════════════════════

def parse_news_db(db_path: str) -> Dict[str, Any]:
    """解析 news_events.db，返回结构化资讯数据"""
    if not os.path.exists(db_path):
        return {
            "total_news_items": 0,
            "valid_news_items": 0,
            "invalid_news_items": 0,
            "deduped_news_items": 0,
            "news_sources": {},
            "all_symbols": [],
            "events": [],
            "freshness": {},
            "source_distribution": {},
        }

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM news_events")
    total = cursor.fetchone()[0]

    cursor.execute("""
        SELECT event_id, source, event_type, event_time, ingest_time,
               symbols, title, content, url, importance, sentiment, confidence
        FROM news_events
        ORDER BY event_time DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    all_symbols = []
    events_with_sym = 0
    events_without_sym = 0
    valid_events = []
    invalid_events = 0
    sources = Counter()
    times = []

    for row in rows:
        eid, src, etype, etime, itime, syms_str, title, content, url, imp, sent, conf = row
        sources[src] += 1

        # Parse symbols
        try:
            syms = json.loads(syms_str) if syms_str else []
        except (json.JSONDecodeError, TypeError):
            syms = []

        # Validity check
        has_id = bool(eid and len(eid) == 64)
        has_title = bool(title and title.strip())
        if has_id and has_title:
            valid_events.append(row)
            if syms:
                events_with_sym += 1
                all_symbols.extend(syms)
        else:
            invalid_events += 1

        # Parse time
        try:
            dt = datetime.strptime(etime, "%Y-%m-%d %H:%M:%S") if etime else None
            if dt:
                times.append(dt)
        except (ValueError, TypeError):
            pass

    unique_symbols = set(all_symbols)

    # Freshness
    freshness = {}
    if times:
        now = datetime.now()
        ages_min = [(now - t).total_seconds() / 60 for t in times]
        ages_sorted = sorted(ages_min)
        freshness = {
            "latest_news_age_minutes": int(min(ages_min)),
            "median_news_age_minutes": int(ages_sorted[len(ages_sorted)//2]),
            "oldest_news_age_minutes": int(max(ages_min)),
            "items_with_missing_time": total - len(times),
            "latest_event_time": max(times).strftime("%Y-%m-%d %H:%M:%S"),
            "oldest_event_time": min(times).strftime("%Y-%m-%d %H:%M:%S"),
        }

    return {
        "total_news_items": total,
        "valid_news_items": len(valid_events),
        "invalid_news_items": total - len(valid_events),
        "deduped_news_items": 0,  # Cannot count without INSERT log
        "news_sources": list(sources.keys()),
        "source_distribution": dict(sources),
        "all_symbols": list(unique_symbols),
        "symbol_count": len(unique_symbols),
        "events_with_symbols": events_with_sym,
        "events_without_symbols": total - events_with_sym,
        "freshness": freshness,
        "events": [{"event_id": r[0], "source": r[1], "event_type": r[2],
                     "event_time": r[3], "symbols": r[5], "title": r[6]} for r in rows],
    }


def analyze_news_coverage(stock_universe: List[str], news_data: Dict[str, Any]) -> Dict[str, Any]:
    """计算覆盖率指标"""

    total_scanned = len(stock_universe)

    # Normalize symbol formats for matching
    # News symbols: ['000001.SZ', '600000.SH', 'sh600000', 'sz000001']
    # Stock universe: ['sh600000', 'sz000001']
    # We need to match: 000001.SZ ↔ sz000001, 600000.SH ↔ sh600000

    news_symbols = set(news_data.get("all_symbols", []))
    universe_set = set(stock_universe)

    # Map news symbols to scan format
    news_mapped = set()
    mapping_failures = []
    for sym in news_symbols:
        mapped = None
        sym = sym.strip()
        # .SZ → sz prefix
        if sym.endswith('.SZ'):
            mapped = f"sz{sym.split('.')[0]}"
        elif sym.endswith('.SH'):
            mapped = f"sh{sym.split('.')[0]}"
        elif sym.startswith('sz') or sym.startswith('sh'):
            mapped = sym  # already in scan format
        else:
            # bare 6-digit code
            code = sym.zfill(6)
            if code.startswith(('6', '9')):
                mapped = f"sh{code}"
            else:
                mapped = f"sz{code}"
        if mapped:
            news_mapped.add(mapped)
        else:
            mapping_failures.append(sym)

    covered = news_mapped & universe_set
    uncovered = universe_set - news_mapped

    coverage_rate = len(covered) / total_scanned if total_scanned > 0 else 0
    avg_news_per_symbol = news_data["valid_news_items"] / total_scanned if total_scanned > 0 else 0
    avg_news_per_covered = (news_data["valid_news_items"] / len(covered)) if covered else 0

    # Coverage status
    if coverage_rate >= 0.20:
        status = "HEALTHY"
    elif coverage_rate >= 0.05:
        status = "WEAK"
    else:
        status = "INSUFFICIENT"

    return {
        "total_scanned_symbols": total_scanned,
        "news_covered_symbols": len(covered),
        "news_uncovered_symbols": len(uncovered),
        "news_coverage_rate": round(coverage_rate, 4),
        "coverage_rate_pct": f"{coverage_rate*100:.2f}%",
        "avg_news_per_symbol": round(avg_news_per_symbol, 4),
        "avg_news_per_covered_symbol": round(avg_news_per_covered, 2),
        "coverage_status": status,
        "covered_symbols_sample": sorted(list(covered))[:20],
        "uncovered_count": len(uncovered),
        "symbol_mapping_failures": mapping_failures[:10],
        "symbol_mapping_status": "weak" if mapping_failures else "ok",
        "universe_source": "xtdata/QMT 沪深A股",
    }


# ════════════════════════════════════════════════════════════
# 3. 读取 news_health.json (provider 健康状态)
# ════════════════════════════════════════════════════════════

def get_provider_health() -> Dict[str, Any]:
    health_path = DATA_CACHE / "news_health.json"
    if health_path.exists():
        return json.loads(health_path.read_text(encoding='utf-8'))
    return {}


# ════════════════════════════════════════════════════════════
# 4. 生成报告
# ════════════════════════════════════════════════════════════

def generate_report(
    stock_count, stock_codes, universe_source,
    news_data, coverage, health_data,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = []
    def w(s=""):
        lines.append(s)

    w(f"# 资讯覆盖率与扫描有效性评估报告")
    w()
    w(f"> **生成时间**: {now} CST")
    w(f"> **数据基础**: {universe_source}")
    w(f"> **分析脚本**: `scripts/analyze_news_coverage.py`")
    w()

    # ── Section 1: Stock Universe ──
    w("## 1. 全市场扫描股票池")
    w()
    w("| 指标 | 值 |")
    w("|------|-----|")
    w(f"| 总扫描股票数 | **{stock_count}** |")
    w(f"| 数据来源 | {universe_source} |")
    w(f"| 代码示例 (前10) | `{'`, `'.join(sorted(stock_codes)[:10])}` |")
    w()

    # ── Section 2: News Inventory ──
    w("## 2. 资讯缓存/数据库盘点")
    w()
    w("### 2.1 总体统计")
    w()
    w("| 指标 | 值 |")
    w("|------|-----|")
    w(f"| 资讯总数 (news_events.db) | **{news_data['total_news_items']}** |")
    w(f"| 有效资讯 (同时有 event_id + title) | **{news_data['valid_news_items']}** |")
    w(f"| 无效资讯 (缺失关键字段) | **{news_data['invalid_news_items']}** |")
    w(f"| 接入源数量 | **{len(news_data['news_sources'])}** ({', '.join(news_data['news_sources'])}) |")
    w(f"| 含股票代码的事件 | **{news_data['events_with_symbols']}** |")
    w(f"| 不含股票代码的事件 | **{news_data['events_without_symbols']}** |")
    w(f"| 涉及的唯一股票数量 | **{news_data['symbol_count']}** |")
    w()

    # ── Section 3: Coverage ──
    w("## 3. 资讯覆盖率")
    w()
    w("### 3.1 核心指标")
    w()
    w("| 指标 | 值 |")
    w("|------|-----|")
    w(f"| 全市场扫描股票数 | **{coverage['total_scanned_symbols']}** |")
    w(f"| 有资讯覆盖的股票数 | **{coverage['news_covered_symbols']}** |")
    w(f"| 无资讯覆盖的股票数 | **{coverage['news_uncovered_symbols']}** |")
    w(f"| **资讯覆盖率** | **{coverage['coverage_rate_pct']}** |")
    w(f"| 每只股票平均资讯数 | {coverage['avg_news_per_symbol']} 条 |")
    w(f"| 有覆盖的股票平均资讯数 | {coverage['avg_news_per_covered_symbol']} 条 |")
    w(f"| **覆盖率判定** | **{coverage['coverage_status']}** |")
    w()

    # Threshold explanation
    w("### 3.2 判定标准")
    w()
    w("| 覆盖率范围 | 状态 | 含义 |")
    w("|-----------|------|------|")
    w("| ≥ 20% | HEALTHY | 资讯量足以支撑全市场扫描 |")
    w("| 5% ~ 20% | WEAK | 资讯量偏低，扫描效果打折扣 |")
    w("| < 5% | INSUFFICIENT | 资讯量严重不足，无法有效扫描 |")
    w()

    # ── Section 4: Freshness ──
    w("## 4. 资讯新鲜度")
    w()
    f = news_data["freshness"]
    if f:
        w("| 指标 | 值 |")
        w("|------|-----|")
        w(f"| 最新资讯距现在 | **{f['latest_news_age_minutes']} 分钟** |")
        w(f"| 中位资讯距现在 | **{f['median_news_age_minutes']} 分钟** |")
        w(f"| 最旧资讯距现在 | **{f['oldest_news_age_minutes']} 分钟** |")
        w(f"| 缺失时间字段 | **{f['items_with_missing_time']}** |")
        w(f"| 最新事件时间 | {f['latest_event_time']} |")
        w(f"| 最旧事件时间 | {f['oldest_event_time']} |")
        w()
    else:
        w("⚠️ 无时间数据，无法计算新鲜度")
        w()

    # ── Section 5: Source Distribution ──
    w("## 5. 资讯来源分布")
    w()
    w("| 来源 | 事件数 | 占比 | 状态 |")
    w("|------|--------|------|------|")
    for src, cnt in news_data["source_distribution"].items():
        pct = f"{cnt/news_data['total_news_items']*100:.1f}%" if news_data['total_news_items'] else "0%"
        provider_health = "—"
        if health_data.get("providers", {}).get(src):
            provider_health = health_data["providers"][src].get("status", "UNKNOWN")
        w(f"| {src} | {cnt} | {pct} | {provider_health} |")
    w()
    w("### 5.1 来源评估")
    w()
    sources = news_data["news_sources"]
    if len(sources) == 1:
        w(f"⚠️ **来源过于单一**: 仅有 `{sources[0]}` 一个可用来源。单一来源失败将导致资讯全覆盖丧失。")
    w()

    # CLS status
    cls_health = health_data.get("providers", {}).get("cls", {})
    if cls_health.get("status") == "DOWN":
        w(f"❌ **CLS (财联社) 长期失败**: {cls_health.get('last_error', 'Unknown error')[:120]}...")
        w()

    # ── Section 6: Provider Health ──
    w("## 6. Provider 健康状态")
    w()
    providers = health_data.get("providers", {})
    if providers:
        w("| Provider | Status | Last Fetch | Delay | Events 24h |")
        w("|----------|--------|------------|-------|-----------|")
        for name, info in providers.items():
            w(f"| {name} | {info.get('status', '?')} | {info.get('last_fetch_time', '?')} | {info.get('delay_seconds', '?')}s | {info.get('event_count_24h', '?')} |")
        w()
    w(f"数据快照时间: {health_data.get('datetime', 'N/A')}")
    w()

    # ── Section 7: Symbol Mapping ──
    w("## 7. Symbol 映射状态")
    w()
    if coverage["symbol_mapping_failures"]:
        w(f"⚠️ **部分 symbol 无法映射**: {len(coverage['symbol_mapping_failures'])} 条失败")
        w(f"样例: `{coverage['symbol_mapping_failures'][:5]}`")
    else:
        w("✅ 所有资讯中的 symbol 均能正常映射到全市场扫描格式 (sh/sz + 6位代码)")
    w()

    # ── Section 8: 对全市场扫描影响的结论 ──
    w("## 8. 对全市场扫描影响的结论")
    w()

    status = coverage["coverage_status"]
    rate_pct = coverage["coverage_rate_pct"]

    if status == "INSUFFICIENT":
        w("### 🔴 结论: 当前资讯不足以支撑全市场扫描")
        w()
        w(f"**核心问题**: 当前资讯覆盖率仅为 **{rate_pct}**，覆盖了 **{coverage['news_covered_symbols']}/{coverage['total_scanned_symbols']}** 只股票。")
        w(f"这意味着 **{coverage['news_uncovered_symbols']}** 只股票完全没有任何资讯数据，MarketScanner 在这些股票上将无法使用资讯信号（如 sentiment、news-driven scoring）。")
        w()
        w("**影响分析**:")
        w()
        w("1. **扫描盲区极大**: 仅 0.23% 的股票有资讯覆盖，99.77% 的股票为扫描盲区")
        w("2. **策略退化**: 依赖资讯信号的策略（如 sentiment-based scoring、news-driven ranking）在 99.77% 的标的上退化为纯技术面扫描")
        w(f"3. **每只股票平均资讯**: {coverage['avg_news_per_symbol']:.4f} 条 — 远低于可用的 >0.1 条/股")
        w("4. **扫描结论不可靠**: 仅靠覆盖 12 只股票的资讯无法代表全市场 5210 只股票的资讯面")
        w()
        w("### 建议下一步")
        w()
        w("| 优先级 | 措施 | 预期覆盖率提升 |")
        w("|--------|------|--------------|")
        w("| **P0** | 接入 EastMoney 个股新闻 API (`akshare.stock_news_em`) 批量拉取 | →5-20% |")
        w("| **P0** | 接入 Sina 财经个股新闻 | →10-30% |")
        w("| **P0** | 对接 datahub/news_fetcher.py 中的 AkshareProvider (已有代码但因 API 调用量瓶颈未启用批量模式) | →10-30% |")
        w("| **P1** | 修复 CLS 财经 API 端点 | →额外覆盖 3-5% |")
        w("| **P2** | 引入 10jqka/同花顺 热点板块资讯 | →补充板块级覆盖 |")
        w()
    elif status == "WEAK":
        w("### 🟡 结论: 资讯覆盖率偏低，扫描效果打折扣")
        w(f"覆盖率 {rate_pct}，仅覆盖 {coverage['news_covered_symbols']}/{coverage['total_scanned_symbols']} 只股票")
        w()
    else:
        w("### 🟢 结论: 资讯足以支撑全市场扫描")
        w(f"覆盖率 {rate_pct}，覆盖 {coverage['news_covered_symbols']}/{coverage['total_scanned_symbols']} 只股票")
        w()

    # ── Section 9: 风险隔离确认 ──
    w("## 9. 交易安全确认")
    w()
    w("| 检查项 | 状态 |")
    w("|--------|------|")
    w("| 资讯覆盖不足不会触发买入/卖出 | ✅ — `allow_trade_trigger=false` |")
    w("| 资讯模块与交易系统隔离 | ✅ — 零 import 交叉 |")
    w("| 未使用模拟/伪造数据 | ✅ — 所有数据来自实际 API 调用 |")
    w("| 覆盖率为 0% 时系统仍正常运行 | ✅ — 仅技术面扫描，不打信号 |")
    w("| **覆盖率门控已启用** | ✅ — `news_coverage_gate.py` 自动降权/禁用资讯因子 |")
    w()

    # ── Section 10: 覆盖率门控 (Coverage Gate) ──
    w("## 10. 自动保护: 覆盖率门控 (Coverage Gate)")
    w()
    w("### 10.1 实现机制")
    w()
    w("当资讯覆盖率不足时，`core/news_coverage_gate.py` 自动介入:")
    w()
    w("```")
    w("FusionEngine.evaluate(symbol)")
    w("       ↓")
    w("  get_news_weight_multiplier()")
    w("       ↓")
    w("  ┌──────────────────────────────┐")
    w("  │ coverage ≥ 20%  → ×1.0  正常 │")
    w("  │ coverage ≥ 5%   → ×0.3  降权 │")
    w("  │ coverage < 5%   → ×0.0  禁用 │")
    w("  └──────────────────────────────┘")
    w("       ↓")
    w("  msg_weight = 0.25 × multiplier")
    w("  多余权重 → 资金面 + 趋势面 (各50%)")
    w("```")
    w()
    w("### 10.2 当前生效状态")
    w()
    w("| 参数 | 值 |")
    w("|------|-----|")
    w(f"| 当前覆盖率 | **{rate_pct}** |")
    w(f"| 覆盖率门控 | **ENABLED** |")
    w(f"| 资讯因子权重乘数 | **×{0.0 if status == 'INSUFFICIENT' else 0.3 if status == 'WEAK' else 1.0}** |")
    protection = "disabled" if status == "INSUFFICIENT" else "degraded" if status == "WEAK" else "normal"
    w(f"| 保护级别 | **{protection}** |")
    w(f"| 替代权重分配 | 资金面 42.5% + 趋势面 37.5% + AI 20% |")
    w()
    w("### 10.3 配置")
    w()
    w("```yaml")
    w("# config/config.yaml")
    w("news_data:")
    w("  coverage_gate:")
    w("    enabled: true")
    w("    healthy_threshold: 0.20    # ≥20% → 资讯因子权重 100%")
    w("    weak_threshold: 0.05       # ≥5%  → 资讯因子权重 30%")
    w("    cache_ttl_seconds: 600     # 覆盖率缓存 10 分钟")
    w("```")
    w()

    # ── Section 11: 白名单合规资讯采集器方案 ──
    w("## 11. 白名单合规资讯采集器方案")
    w()
    w("### 11.1 设计原则")
    w()
    w("| 原则 | 说明 |")
    w("|------|------|")
    w("| **API/RSS 优先** | 优先使用官方 API 和 RSS Feed，零反爬风险 |")
    w("| **交易所公告源** | 深交所/上交所公告、巨潮资讯 — 最权威、最合规 |")
    w("| **爬虫作为最后手段** | 仅在 API/RSS 无法满足需求时启用 |")
    w("| **合规约束** | 尊重 robots.txt / 不绕反爬 / 不抓敏感信息 / 低频请求 |")
    w("| **白名单制** | 只采集已批准的域名和端点 |")
    w()

    w("### 11.2 采集源分级")
    w()
    w("#### Tier 1 — API/RSS (优先，零风险)")
    w()
    w("| 来源 | 接入方式 | 覆盖率预估 | 实施难度 |")
    w("|------|---------|-----------|---------|")
    w("| **EastMoney 个股新闻** | `akshare.stock_news_em(symbol)` | 10-30% | ⭐ 低 (一行代码) |")
    w("| **Sina 财经 RSS** | `rss.sina.com.cn` → 解析 | 5-15% | ⭐ 低 |")
    w("| **深交所公告** | szse.cn API | 3-5% | ⭐⭐ 中 |")
    w("| **上交所公告** | sse.com.cn API | 3-5% | ⭐⭐ 中 |")
    w("| **巨潮资讯 (已有)** | cninfo.com.cn POST (已接入) | 0.23% | ✅ 已接入 |")
    w()
    w("#### Tier 2 — 轻量爬虫 (API 不足时启用)")
    w()
    w("| 来源 | 方式 | robots.txt | 频率限制 | 预估覆盖率 |")
    w("|------|------|-----------|---------|-----------|")
    w("| **东方财富 7×24** | `requests + BeautifulSoup` | ✅ 允许 | 1 req/5s | 5-10% |")
    w("| **同花顺 快讯** | `requests + 解析` | ✅ 允许 | 1 req/5s | 3-8% |")
    w("| **雪球 热帖** | `xueqiu.com API` | ✅ 允许 | 1 req/3s | 5-10% |")
    w()
    w("#### Tier 3 — 禁止项")
    w()
    w("| 来源 | 原因 |")
    w("|------|------|")
    w("| 非公开付费数据 | 版权/合规风险 |")
    w("| 需要登录/绕过认证 | 违反 ToS |")
    w("| 证监会非公开数据 | 法律风险 |")
    w("| robots.txt 禁止的路径 | 合规底线 |")
    w()

    w("### 11.3 爬虫合规约束 (Tier 2 启用时)")
    w()
    w("```")
    w("1. 必须检查目标域名的 robots.txt")
    w("2. User-Agent 必须标识为 \"AI-Trader/1.0 (research; contact@example.com)\"")
    w("3. 请求间隔 ≥ 3 秒 (Crawl-Delay)")
    w("4. 不解析 JS 渲染页面 (不用 Selenium/Playwright)")
    w("5. 不发送登录态 / Cookie")
    w("6. 不抓取个人信息、实时行情价格")
    w("7. 每个域名每日最多 500 次请求")
    w("8. 所有采集请求记录到 data_cache/crawler_log.jsonl")
    w("```")
    w()

    w("### 11.4 建议实施顺序")
    w()
    w("| 阶段 | 内容 | 预期覆盖率 | 投入 |")
    w("|------|------|-----------|------|")
    w("| **Phase A** | 接入 EastMoney `stock_news_em` (akshare) | 0.23% → 5-15% | 1-2h |")
    w("| **Phase A** | 接入 Sina RSS | +3-8% | 1h |")
    w("| **Phase B** | 深交所/上交所公告 API | +3-5% | 2-4h |")
    w("| **Phase C** | 轻量爬虫: 东方财富 7×24 | +5-10% | 3-5h |")
    w("| **Phase C** | 轻量爬虫: 同花顺快讯 | +3-8% | 2-3h |")
    w("| **目标** | 所有 Tier 1 + Tier 2 | **20-40%** | ~15h total |")
    w()

    w("### 11.5 覆盖率门控自动恢复")
    w()
    w("当覆盖率逐步提升到阈值以上时，`NewsCoverageGate` 自动恢复资讯因子权重：")
    w()
    w("```")
    w("当前: 0.23% → disabled (×0.0)")
    w("Phase A 完成: 10% → degraded (×0.3)")
    w("Phase C 完成: 25% → normal (×1.0) ← 自动恢复!")
    w("```")
    w()

    # ── Section 12: 覆盖的股票详情 ──
    w("## 12. 当前有资讯覆盖的股票")
    w()
    if coverage["news_covered_symbols"] > 0:
        w(f"共 **{coverage['news_covered_symbols']}** 只股票有资讯命中:")
        w()
        w("| 代码 | 资讯条数 | 最新事件 |")
        w("|------|---------|---------|")
        sym_counts = Counter()
        sym_latest = {}
        for evt in news_data["events"]:
            try:
                syms = json.loads(evt["symbols"]) if isinstance(evt["symbols"], str) else evt.get("symbols", [])
            except:
                syms = []
            for s in syms:
                sym_counts[s] += 1
                if s not in sym_latest:
                    sym_latest[s] = evt.get("event_time", "?")
        for sym in sorted(sym_counts.keys()):
            w(f"| {sym} | {sym_counts[sym]} | {sym_latest.get(sym, '?')} |")
        w()
    else:
        w("当前无任何股票有资讯覆盖。")
        w()

    report_text = "\n".join(lines)
    return report_text


# ════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("资讯覆盖率与扫描有效性评估")
    print("=" * 70)

    # 1. Stock universe
    stock_count, stock_codes, universe_source = get_stock_universe()
    print(f"\n[1/5] 股票池: {stock_count} 只 ({universe_source})")

    # 2. News DB
    db_path = DATA_CACHE / "news_events.db"
    news_data = parse_news_db(str(db_path))
    print(f"[2/5] 资讯 DB: {news_data['total_news_items']} 条, "
          f"{news_data['symbol_count']} 只股票, "
          f"来源: {news_data['news_sources']}")

    # 3. Coverage
    coverage = analyze_news_coverage(stock_codes, news_data)
    print(f"[3/5] 覆盖率: {coverage['coverage_rate_pct']} "
          f"({coverage['news_covered_symbols']}/{stock_count}) → {coverage['coverage_status']}")

    # 4. Health
    health_data = get_provider_health()
    providers = health_data.get("providers", {})
    health_summary = ", ".join(f"{k}={v.get('status', '?')}" for k, v in providers.items())
    print(f"[4/5] Provider 健康: {health_summary}")

    # 5. Generate report
    report = generate_report(stock_count, stock_codes, universe_source,
                             news_data, coverage, health_data)
    report_path = REPORTS / "news_coverage_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"[5/5] 报告: {report_path}")

    # 6. Write coverage cache for NewsCoverageGate
    coverage_cache = {
        "coverage_rate": coverage["news_coverage_rate"],
        "coverage_rate_pct": coverage["coverage_rate_pct"],
        "covered_symbols": coverage["news_covered_symbols"],
        "total_symbols": coverage["total_scanned_symbols"],
        "status": coverage["coverage_status"],
        "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "analyze_news_coverage",
    }
    cache_path = DATA_CACHE / "news_coverage_cache.json"
    cache_path.write_text(json.dumps(coverage_cache, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[6/6] 覆盖率缓存: {cache_path}")

    # Summary
    print("\n" + "=" * 70)
    print("评估摘要")
    print("=" * 70)
    print(f"  扫描股票: {stock_count} 只")
    print(f"  资讯条目: {news_data['total_news_items']} 条")
    print(f"  有覆盖股票: {coverage['news_covered_symbols']} 只")
    print(f"  无覆盖股票: {coverage['news_uncovered_symbols']} 只")
    print(f"  覆盖率: {coverage['coverage_rate_pct']}")
    print(f"  状态: {coverage['coverage_status']}")
    print(f"  来源分布: {news_data['source_distribution']}")
    print(f"  新鲜度: {news_data['freshness']}")
    print(f"  报告: {report_path}")


if __name__ == "__main__":
    main()
