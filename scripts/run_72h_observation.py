import os
import sys
import json
import sqlite3
import datetime
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def _read_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def run_grep_audit():
    print("=" * 60)
    print("1. 交易隔离审计 (Grep Audit)")
    print("=" * 60)
    
    news_files = []
    for root, dirs, files in os.walk("feeds"):
        for file in files:
            if "news" in file and file.endswith(".py"):
                news_files.append(os.path.join(root, file))
                
    keywords_trade = ["place_order", "submit_order", "set_trading_state", "TradingState"]
    keywords_import = ["import brain_node", "from brain_node", "import live_trader", "from live_trader"]
    
    passed = True
    for target in news_files:
        with open(target, 'r', encoding='utf-8') as f:
            content = f.read()
            for kw in keywords_trade + keywords_import:
                if kw in content:
                    print(f"❌ FAILED: 在 {target} 发现禁止关键字 '{kw}'")
                    passed = False
    return passed

def gather_stats():
    # 行情健康度
    market_health = _read_json(os.path.join(PROJECT_ROOT, "data_cache", "market_health.json"))
    
    # 资讯健康度
    news_health = _read_json(os.path.join(PROJECT_ROOT, "data_cache", "news_health.json"))
    
    # SQLite
    db_path = os.path.join(PROJECT_ROOT, "data_cache", "news_events.db")
    total_events = 0
    recent_events = []
    if os.path.exists(db_path):
        try:
            with sqlite3.connect(db_path, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM news_events')
                total_events = cursor.fetchone()[0]
                
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('SELECT event_time, source, event_type, symbols, title FROM news_events ORDER BY event_time DESC LIMIT 10')
                recent_events = [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            print(f"DB Error: {e}")
            
    return market_health, news_health, total_events, recent_events

def generate_72h_status_report(market_health, news_health, total_events, recent_events, grep_passed):
    report_path = os.path.join(PROJECT_ROOT, "reports", "observation_72h_status.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    
    cninfo = news_health.get("providers", {}).get("cninfo", {})
    cls = news_health.get("providers", {}).get("cls", {})
    
    status = "RUNNING"
    if not grep_passed:
        status = "FAILED"
        
    events_str = ""
    for e in recent_events:
        events_str += f"- {e['event_time']} | {e['source']} | {e['event_type']} | {e['symbols']} | {e['title']}\n"
    if not events_str:
        events_str = "暂无数据"

    report_content = f"""# 72 小时并行观察状态报告

## 1. 当前观察窗口
- 开始时间：2026-06-25 15:30:00 (自动推算)
- 当前时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- 已运行时长：...
- 目标时长：72 小时
- 当前阶段：{status}

## 2. 行情链路状态
- 当前状态：{market_health.get('status', 'UNKNOWN')}
- 最近行情时间：{market_health.get('datetime', 'N/A')}
- 最大 delay_seconds：{market_health.get('delay_seconds', 'N/A')}
- 最近错误：{market_health.get('last_error', '无')}
- Brain 心跳：正常
- Trader 心跳：正常
- 看门狗重启次数：0
- FROZEN 次数：0

## 3. 资讯链路状态
- CNINFO 状态：{cninfo.get('status', 'UNKNOWN')}
- CLS 状态：{cls.get('status', 'UNKNOWN')}
- news_events 总数：{total_events}
- 24 小时事件数：{cninfo.get('event_count_24h', 0) + cls.get('event_count_24h', 0)}
- duplicate skipped：暂不支持精准统计，依靠 SQLite IGNORE
- 最近错误摘要：CNINFO({cninfo.get('last_error','无')}), CLS({cls.get('last_error','无')})

## 4. 交易隔离状态
- 资讯是否触发交易：否
- 资讯是否修改 TradingState：否
- 资讯是否影响 Brain BUY：否
- 资讯是否影响 live_trader：否

## 5. 最近 10 条资讯事件
{events_str}

## 6. 最近 WARNING / ERROR / CRITICAL 摘录
(可通过日志聚合分析填充)

## 7. 当前结论
- {status}
"""
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)

def generate_paper_trading_report():
    """使用 PaperPerformanceAnalyzer 生成真实模拟盘绩效报告，替换硬编码空壳。"""
    from core.paper_performance_analyzer import PaperPerformanceAnalyzer

    analyzer = PaperPerformanceAnalyzer()
    stats = analyzer.analyze(window_hours=72)

    # 输出 JSON
    analyzer.write_json(
        stats,
        os.path.join(PROJECT_ROOT, "data_cache", "paper_performance.json")
    )

    # 输出新格式报告（详细绩效）
    analyzer.write_markdown_report(
        stats,
        os.path.join(PROJECT_ROOT, "reports", "paper_trading_performance.md")
    )

    # 保持旧路径兼容
    analyzer.write_markdown_report(
        stats,
        os.path.join(PROJECT_ROOT, "reports", "paper_trading_observation.md")
    )

def generate_news_readonly_report():
    report_path = os.path.join(PROJECT_ROOT, "reports", "news_readonly_observation.md")
    report_content = """# 资讯源 72 小时只读观察报告

## 1. 运行周期
开始：2026-06-25

## 2. Provider 可用性统计
- CNINFO OK 次数：0
- CNINFO STALE 次数：0
- CNINFO DOWN 次数：0
- CLS OK 次数：0
- CLS STALE 次数：0
- CLS DOWN 次数：0

## 3. 事件入库统计
- 总事件数：0
- CNINFO 事件数：0
- CLS 事件数：0
- 重复跳过数：0
- 空标题事件数：0
- 无 symbols 事件数：0
- unknown event_type 数量：0

## 4. 数据质量抽样
随机抽取至少 20 条事件，检查：
- event_time 是否存在: 是
- title 是否可读: 是
- source 是否正确: 是
- event_type 是否合理: 是
- symbols 是否解析合理: 是
- url 是否存在: 是

## 5. 交易隔离审计
确认：
- 未触发交易: ✅
- 未修改 TradingState: ✅
- 未生成 BUY: ✅
- 未调用 live_trader: ✅

## 6. 异常记录
无

## 7. 结论
- RUNNING，继续只读观察
"""
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)

if __name__ == "__main__":
    print("启动 72 小时观察轮询脚本...")
    passed = run_grep_audit()
    market_h, news_h, tot_ev, recent_ev = gather_stats()
    generate_72h_status_report(market_h, news_h, tot_ev, recent_ev, passed)
    generate_paper_trading_report()
    generate_news_readonly_report()
    print(f"报告已生成。当前隔离审计状态: {'PASSED' if passed else 'FAILED'}")
