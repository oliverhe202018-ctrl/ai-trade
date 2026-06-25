"""
AI 交易系统 监控大屏
"""
import os
import sys
import json
import time
import pandas as pd
import streamlit as st
from pathlib import Path
from datetime import datetime, timedelta

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.backtester import CACHE_DIR, LOGS_DIR
from core.state_manager import load_portfolio
from core.trading_state import get_trading_state, TradingState

# ==========================================
# 页面配置
# ==========================================
st.set_page_config(
    page_title="AI 交易监控",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==========================================
# 共用工具函数
# ==========================================

def load_json(path, default=None, silent=False):
    """safe JSON loader"""
    try:
        p = Path(path)
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        if not silent:
            st.warning(f"读取 {path} 失败: {e}")
    return default


def format_pct(val):
    if val is None:
        return "N/A"
    color = "red" if val > 0 else "green" if val < 0 else "gray"
    return f"<span style='color:{color}'>{val:+.2f}%</span>"


def format_profit(val):
    if val is None:
        return "N/A"
    color = "red" if val > 0 else "green" if val < 0 else "gray"
    return f"<span style='color:{color}'>{val:,.2f}</span>"


# ==========================================
# 模块：资产监控大屏
# ==========================================

def module_asset_monitor():
    st.header("📊 资产监控大屏")

    portfolio = load_portfolio()
    if not portfolio:
        st.error("无法读取组合数据，请确认 live_trader 已启动。")
        return

    cash = portfolio.get("cash", 0)
    positions = portfolio.get("positions", {})
    total_cost = sum(p.get("avg_cost", 0) * p.get("quantity", 0) for p in positions.values())
    total_equity = cash + total_cost

    # ── KPI 行 ──
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("💰 可用现金", f"{cash:,.2f}")
    k2.metric("📈 持仓成本合计", f"{total_cost:,.2f}")
    k3.metric("🏦 总净値估算", f"{total_equity:,.2f}")
    k4.metric("📌 持仓标的数", f"{len(positions)}")

    st.markdown("---")

    # ── 持仓明细 ──
    st.subheader("📌 当前持仓")
    if positions:
        rows = []
        for code, pos in positions.items():
            qty = pos.get("quantity", 0)
            avg_cost = pos.get("avg_cost", 0)
            cur_price = pos.get("current_price", avg_cost)
            cost_val = avg_cost * qty
            cur_val = cur_price * qty
            profit = cur_val - cost_val
            pct = (profit / cost_val * 100) if cost_val > 0 else 0
            rows.append({
                "代码": code,
                "持仓数量": qty,
                "均摘成本": f"{avg_cost:.3f}",
                "当前价": f"{cur_price:.3f}",
                "市值": f"{cur_val:,.2f}",
                "浮动盈亏": f"{profit:,.2f}",
                "浮动盈亏%": f"{pct:+.2f}%"
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
    else:
        st.info("目前无持仓。")

    # ── 交易状态 ──
    st.markdown("---")
    st.subheader("🚦 系统交易状态")
    try:
        state = get_trading_state()
        color = "green" if state == TradingState.ACTIVE else "red"
        st.markdown(f"<span style='color:{color}; font-size:1.4rem; font-weight:bold;'>{state.value}</span>", unsafe_allow_html=True)
    except Exception as e:
        st.warning(f"状态获取失败: {e}")


# ==========================================
# 模块：自选股管理
# ==========================================

def module_watchlist():
    st.header("📋 自选股管理")
    watchlist_file = PROJECT_ROOT / "data_cache" / "watchlist.json"
    watchlist = load_json(watchlist_file, default=[])

    st.subheader("当前自选股")
    if watchlist:
        st.write(watchlist)
    else:
        st.info("自选股为空。")

    st.markdown("---")
    new_code = st.text_input("新增标的代码（如 sh600519）")
    if st.button("添加") and new_code:
        if new_code not in watchlist:
            watchlist.append(new_code.strip())
            watchlist_file.parent.mkdir(parents=True, exist_ok=True)
            with open(watchlist_file, "w", encoding="utf-8") as f:
                json.dump(watchlist, f, ensure_ascii=False)
            st.success(f"已添加: {new_code}")
            st.rerun()
        else:
            st.warning(f"{new_code} 已在列表中。")

    remove_code = st.selectbox("移除标的", [""] + watchlist)
    if st.button("移除") and remove_code:
        watchlist = [c for c in watchlist if c != remove_code]
        with open(watchlist_file, "w", encoding="utf-8") as f:
            json.dump(watchlist, f, ensure_ascii=False)
        st.success(f"已移除: {remove_code}")
        st.rerun()


# ==========================================
# 模块：日志
# ==========================================

def module_logs():
    st.header("📜 系统运行日志")

    log_files = []
    if LOGS_DIR.exists():
        log_files = sorted(LOGS_DIR.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
        jsonl_files = sorted(LOGS_DIR.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True)
        log_files.extend(jsonl_files)

    if not log_files:
        st.info("暂无日志文件")
        return

    log_file_names = [f.name for f in log_files]
    selected_log = st.selectbox("选择日志文件", log_file_names)

    if selected_log:
        log_path = LOGS_DIR / selected_log

        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()

            total = len(all_lines)
            tail_lines = all_lines[-200:] if total > 200 else all_lines
            st.subheader(f"最近日志 (文件共 {total} 行，展示最后 {len(tail_lines)} 行)")

            if selected_log.endswith(".jsonl"):
                formatted = []
                for line in reversed(tail_lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry.get("timestamp", "")
                        action = entry.get("action", "")
                        code = entry.get("code", "")
                        name = entry.get("name", "")
                        price = entry.get("price", 0)
                        quantity = entry.get("quantity", 0)
                        profit = entry.get("profit", 0)
                        formatted.append(
                            f"{ts}  {action:4s}  {code} {name}  "
                            f"价格:{price:.2f}  数量:{quantity}  盈亏:{profit:,.2f}"
                        )
                    except json.JSONDecodeError:
                        formatted.append(line)
                st.code("\n".join(formatted), language="text")
            else:
                log_text = "".join(reversed(tail_lines))
                st.code(log_text, language="text")

        except Exception as e:
            st.error(f"读取日志失败: {e}")


# ==========================================
# 模块： Paper Trading 监控
# ==========================================

def module_paper_trading_monitor():
    st.header("📈 Paper Trading 监控与审计大屏")
    st.markdown("监控最近 5 个交易日的实盘试运行情况，验证全链路风控与信号流转是否符合预期。")

    st.info("""
    **🎯 首日 Paper Trading 通过标准：**
    1. 全部关键进程可稳定运行到收盘，不出现主循环崩溃。
    2. Dashboard 的 Paper Trading 监控可正常统计关键审计字段。
    3. 没有 FOLLOW_RULE_ONLY 和 Unknown final action 命中。
    4. LLM、指数缓存、交易状态任一异常时，系统都表现为保守阻断。
    5. 任意一笔交易都能在日志中追溯到完整审计链路。
    """)
    st.markdown("---")

    keywords = [
        "[TRADE_TRACE]",
        "[RISK_GATE_BLOCK]",
        "[CONSERVATIVE_VETO]",
        "[STALE_EVENT_BLOCK]",
        "[TRADING_STATE_UNAVAILABLE]",
        "[INDEX_CACHE_STALE]",
        "[PROVIDER_FAILOVER]",
        "[EVENT_CARD_STALE]"
    ]

    fatal_keywords = [
        "FOLLOW_RULE_ONLY",
        "Unknown final action"
    ]

    log_files = []
    if LOGS_DIR.exists():
        log_files = sorted(LOGS_DIR.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)

    if not log_files:
        st.warning("暂无日志文件可供分析。")
        return

    st.subheader("关键词命中统计")

    results = {k: 0 for k in keywords}
    fatal_hits = {k: [] for k in fatal_keywords}

    for log_path in log_files[:5]:
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                for line_num, line in enumerate(f, 1):
                    for k in keywords:
                        if k in line:
                            results[k] += 1
                    for fk in fatal_keywords:
                        if fk in line:
                            fatal_hits[fk].append(f"{log_path.name}:{line_num} -> {line.strip()}")
        except Exception:
            continue

    col1, col2, col3, col4 = st.columns(4)
    cols = [col1, col2, col3, col4]

    explanations = {
        "[TRADE_TRACE]": "每笔成交的四段审计绑定，必须完整。",
        "[RISK_GATE_BLOCK]": "风控闸门拦截记录。",
        "[CONSERVATIVE_VETO]": "LLM 超时/异常触发阻断。",
        "[STALE_EVENT_BLOCK]": "陈旧资讯被拦截。",
        "[TRADING_STATE_UNAVAILABLE]": "状态获取失败触发 Fail-close。",
        "[INDEX_CACHE_STALE]": "大盘缓存陈旧拦截。",
        "[PROVIDER_FAILOVER]": "数据源切换。",
        "[EVENT_CARD_STALE]": "事件卡片陈旧。"
    }

    for i, (kw, cnt) in enumerate(results.items()):
        with cols[i % 4]:
            st.metric(kw, cnt, help=explanations.get(kw, ""))

    st.markdown("---")
    st.subheader("🚨 致命关键词审查")
    has_fatal = False
    for fk, hits in fatal_hits.items():
        if hits:
            has_fatal = True
            st.error(f"**{fk}** 命中 {len(hits)} 次")
            with st.expander("查看详情"):
                for h in hits[:20]:
                    st.code(h, language="text")
    if not has_fatal:
        st.success("✅ 无致命关键词命中。")


# ==========================================
# 模块：历史复盘
# ==========================================

def module_backtest_review():
    st.header("📜 历史复盘")
    st.info("历史复盘功能待完善。")


# ==========================================
# 模块：资讯情绪中枢
# ==========================================

def module_news_sentiment():
    """模块：资讯情绪中枢 — 展示 news_extractor 的实时输出"""
    st.header("📰 资讯情绪中枢")
    st.caption("数据来源：brain_node 每轮演算写入 data_cache/news_sentiment_cache.json")

    cache_file = CACHE_DIR / "news_sentiment_cache.json"

    def _load_cache():
        try:
            if not cache_file.exists():
                st.info("⏳ 等待 brain_node 完成首轮演算...")
                st.stop()
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
        return None

    data = _load_cache()

    if data is None:
        st.warning("⚠️ 尚无资讯缓存文件。请确认 brain_node 已启动，并等待首轮演算完成（直到盘中，约 60 秒）。")
        st.info("缓存路径: `data_cache/news_sentiment_cache.json`")
        if st.button("🔄 刷新"):
            st.rerun()
        return

    ts_str = data.get("_datetime", "未知")
    
    cache_age = time.time() - data.get("_ts", 0)
    if cache_age > 300:
        st.warning(f"⚠️ 数据已 {int(cache_age//60)} 分钟未更新")
        
    source = data.get("_source", "llm")
    news_count = data.get("_news_count", 0)
    macro = float(data.get("macro_sentiment", 0))

    col_ts, col_src, col_cnt, col_refresh = st.columns([3, 2, 2, 1])
    with col_ts:
        st.caption(f"🕐 最后更新：{ts_str}")
    with col_src:
        src_color = "🟢" if source in ("llm", "rule_engine") else "🟡"
        st.caption(f"{src_color} 来源：{source}")
    with col_cnt:
        st.caption(f"📄 本轮资讯条数：{news_count}")
    with col_refresh:
        if st.button("🔄", help="刷新页面"):
            st.rerun()

    st.markdown("---")

    col_gauge, col_sectors = st.columns([2, 3])

    with col_gauge:
        st.subheader("🌡️ 宏观市场情绪")
        if macro > 0.3:
            sentiment_label = "🔴 偏强（看多）"
            color = "#e74c3c"
        elif macro < -0.3:
            sentiment_label = "🟢 偏弱（看空/谨慎）"
            color = "#27ae60"
        else:
            sentiment_label = "⚪ 中性"
            color = "#95a5a6"

        st.markdown(f"""
        <div style="text-align:center; padding: 2rem; background: #1e1e2e;
             border-radius: 1rem; border: 2px solid {color};">
            <div style="font-size: 3.5rem; font-weight: 800; color: {color};">{macro:+.2f}</div>
            <div style="font-size: 1.1rem; margin-top: 0.5rem; color: #ccc;">{sentiment_label}</div>
            <div style="font-size: 0.8rem; color: gray; margin-top: 0.3rem;">区间 [-1, +1]，0 为中性</div>
        </div>
        """, unsafe_allow_html=True)

    with col_sectors:
        st.subheader("🔥 热点板块")
        hot_sectors = data.get("hot_sectors", [])
        if hot_sectors:
            n_cols = min(len(hot_sectors), 4)
            cols = st.columns(n_cols)
            for i, sector in enumerate(hot_sectors[:8]):
                with cols[i % n_cols]:
                    if isinstance(sector, dict):
                        name = sector.get("name", str(sector))
                        score = sector.get("score", "")
                        st.metric(name, f"{score}" if score else "🔥")
                    else:
                        st.metric(str(sector), "🔥")
        else:
            st.info("本轮未识别到明显热点板块。")

    st.markdown("---")

    col_risk, col_stocks = st.columns([2, 3])

    with col_risk:
        st.subheader("⚠️ 个股风险预警")
        risk_warnings = data.get("risk_warnings", [])
        if risk_warnings:
            for rw in risk_warnings:
                if isinstance(rw, dict):
                    code = rw.get("code", "")
                    reason = rw.get("reason", str(rw))
                    st.error(f"🔴 **{code}** — {reason}")
                else:
                    st.warning(f"⚠️ {rw}")
        else:
            st.success("✅ 本轮无个股风险预警。")

    with col_stocks:
        st.subheader("📋 个股情绪评分")
        stock_sentiments = data.get("stock_sentiments", [])
        if stock_sentiments:
            df_stocks = pd.DataFrame(stock_sentiments)
            st.dataframe(df_stocks, use_container_width=True)
        else:
            st.info("本轮无个股情绪评分（LLM 模式才会生成）。")

    st.markdown("---")

    with st.expander("📄 本轮采集资讯原文（展开查看）", expanded=False):
        raw_news = data.get("news_list", data.get("raw_news", []))
        if raw_news:
            for i, item in enumerate(raw_news[:30]):
                if isinstance(item, dict):
                    title = item.get("title", item.get("content", str(item))[:80])
                    pub_time = item.get("publish_time", item.get("time", ""))
                    st.markdown(f"**{i+1}.** {title}  <span style='color:gray; font-size:0.8rem'>{pub_time}</span>", unsafe_allow_html=True)
                else:
                    st.markdown(f"**{i+1}.** {str(item)[:120]}")
        else:
            st.info("原始资讯未写入缓存（规则引擎模式正常）。")

    with st.expander("🔧 完整 JSON 诊断", expanded=False):
        st.json(data)


# ==========================================
# 主函数
# ==========================================

def main():
    st.sidebar.title("🤖 AI 交易监控")
    st.sidebar.markdown("---")

    selected_module = st.sidebar.radio(
        "选择模块",
        ["📊 资产监控大屏", "📰 资讯情绪中枢", "📈 Paper Trading 监控", "📋 自选股管理", "📜 系统运行日志", "📜 历史复盘"],
        index=0
    )

    if selected_module == "📊 资产监控大屏":
        module_asset_monitor()
    elif selected_module == "📰 资讯情绪中枢":
        module_news_sentiment()
    elif selected_module == "📈 Paper Trading 监控":
        module_paper_trading_monitor()
    elif selected_module == "📋 自选股管理":
        module_watchlist()
    elif selected_module == "📜 系统运行日志":
        module_logs()
    elif selected_module == "📜 历史复盘":
        module_backtest_review()

    st.sidebar.markdown("---")
    st.sidebar.caption(f"最后刷新: {datetime.now().strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
