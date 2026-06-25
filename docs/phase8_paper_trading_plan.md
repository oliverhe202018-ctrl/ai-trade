# Phase 8: Paper Trading 连续观测 — 实施方案

## 目标

在 Phase 7（模拟撮合闭环）基础上，建立可持续运行的模拟盘绩效评估体系。  
不做新策略，不接触实盘，纯粹观察与统计。

---

## 1. Phase 8a: 数据清理（前置条件）

当前生产数据文件被 Phase 7 验证测试污染：

| 文件 | 问题 |
|------|------|
| `paper_trade_fills.jsonl` | 9 条混合记录（含旧格式 REJECTED with filled_qty>0） |
| `paper_portfolio.json` | 测试组合 (sh601318 + sh600900) |
| `paper_signal_log.jsonl` | 3 条测试信号 |
| `paper_signal_log.offset` | 644（指向测试信号末尾） |

**操作**:
1. 备份现有文件到 `data_cache/archive/phase7_test_20260625/`
2. 清空 `paper_trade_fills.jsonl`
3. 重置 `paper_portfolio.json` 为初始状态 `{"cash": 100000.0, "positions": {}}`
4. 清空 `paper_signal_log.jsonl`
5. 写入 `paper_signal_log.offset` = `0`

---

## 2. Phase 8b: `paper_performance_analyzer.py`

### 2.1 设计原则

- 只读 fills + portfolio，不写任何交易数据
- 无法计算真值的指标返回 `"unavailable"` 而非伪造数字
- 按时间窗口聚合（72h / 当日 / 全量）
- 输出 JSON + Markdown 双格式

### 2.2 文件路径

```
ai-trader/
├── core/
│   └── paper_performance_analyzer.py   ← 新增
├── data_cache/
│   └── paper_performance.json          ← 新增（输出）
└── reports/
    └── paper_trading_performance.md    ← 新增（输出，替换旧硬编码版）
```

### 2.3 指标清单

```
第一版实现（无行情依赖）:

基础统计:
  window_start        → 观察窗口起始时间
  window_end          → 当前时间
  total_signals       → 信号总数 (fills 记录数)
  filled_orders       → FILLED 订单数
  rejected_orders     → REJECTED 订单数
  skipped_orders      → SKIPPED 订单数
  failed_orders       → FAILED 订单数

交易方向统计:
  buy_count           → BUY 成交数
  sell_count          → SELL 成交数

组合统计:
  current_cash        → 当前现金
  open_positions      → 持仓标的数
  positions_detail    → [{code, quantity, avg_cost}]
  total_cost_basis    → 持仓总成本

异常统计:
  json_decode_errors  → JSON 解析失败次数
  reject_reasons      → {reason: count} 汇总
  data_pollution      → 检测 fills 中是否有 REJECTED 仍带 filled_qty>0

标记为 unavailable 的指标:
  estimated_market_value  → "unavailable: 缺少实时行情"
  total_equity            → "unavailable: 缺少实时行情"
  unrealized_pnl          → "unavailable: 缺少实时行情"
  total_return            → "unavailable: 需净值序列"
  win_rate                → "unavailable: fills 缺少 realized_pnl"
  max_drawdown            → "unavailable: 需净值序列"
```

### 2.4 伪代码结构

```python
class PaperPerformanceAnalyzer:
    def __init__(self, fills_path, portfolio_path):
        ...

    def analyze(self, window_hours=72) -> dict:
        fills = self._load_fills(window_hours)
        portfolio = self._load_portfolio()

        return {
            "generated_at": now,
            "window": {...},
            "basic_stats": self._compute_basic(fills),
            "direction_stats": self._compute_direction(fills),
            "portfolio_stats": self._compute_portfolio(portfolio),
            "anomaly_stats": self._compute_anomalies(fills),
            "unavailable": ["market_value", "total_return", "win_rate", ...],
        }

    def write_json(self, stats, path):
        ...

    def write_markdown_report(self, stats, path):
        ...
```

---

## 3. Phase 8c: 升级 `run_72h_observation.py`

### 变更范围

只修改 `generate_paper_trading_report()` 函数（当前为硬编码空壳）。

### 变更前（Line 133-173）

```python
def generate_paper_trading_report():
    report_content = """# 行情模拟盘 72 小时观察报告
    ...全部硬编码 0..."""
```

### 变更后

```python
def generate_paper_trading_report():
    from core.paper_performance_analyzer import PaperPerformanceAnalyzer
    analyzer = PaperPerformanceAnalyzer(
        fills_path="data_cache/paper_trade_fills.jsonl",
        portfolio_path="data_cache/paper_portfolio.json"
    )
    stats = analyzer.analyze(window_hours=72)
    analyzer.write_markdown_report(
        stats,
        "reports/paper_trading_performance.md"
    )
```

### 定时执行

复用 `observation_scheduler_guide.md` 中的 Windows 任务计划程序方案：
- 触发器: 每 10 分钟
- 操作: `python scripts/run_72h_observation.py`
- 起始于: `C:\Users\a2515\ai-trader`

---

## 4. Phase 8d: Dashboard 接入

### 在 `core/dashboard.py` 中新增

`module_observation_reports()` 区域内增加：

```python
def module_paper_trading_performance():
    """📈 模拟盘绩效"""
    import json
    perf_path = os.path.join(PROJECT_ROOT, "data_cache", "paper_performance.json")
    if not os.path.exists(perf_path):
        st.info("暂无模拟盘绩效数据，请先运行 paper_trade_engine.py")
        return

    with open(perf_path, "r", encoding="utf-8") as f:
        stats = json.load(f)

    basic = stats.get("basic_stats", {})
    portfolio = stats.get("portfolio_stats", {})

    col1, col2, col3 = st.columns(3)
    col1.metric("当前现金", f"¥{portfolio.get('current_cash', 0):,.2f}")
    col2.metric("持仓标的", portfolio.get("open_positions", 0))
    col3.metric("成交/拒单", f"{basic.get('filled_orders',0)}/{basic.get('rejected_orders',0)}")

    st.divider()
    st.caption("最近成交流水（最近 5 笔）")
    for fill in stats.get("recent_fills", [])[:5]:
        icon = "✅" if fill["status"] == "FILLED" else "❌" if fill["status"] == "REJECTED" else "⏭️"
        st.text(f"{icon} {fill['timestamp']} {fill['action']} {fill['code']} x{fill['quantity']}")
```

---

## 5. Phase 8d (续): Copilot 接入

### 新增 `core/paper_trading_provider.py`

```python
class PaperTradingProvider:
    """为 Copilot 提供模拟盘查询上下文"""
    
    def get_summary(self) -> str:
        ...

    def get_recent_fills(self, n=5) -> str:
        ...

    def get_positions(self) -> str:
        ...

    def get_rejections(self) -> str:
        ...
```

在 `core/copilot_service.py` 的 Context Provider 注册表中增加：

```python
from core.paper_trading_provider import PaperTradingProvider

context_providers["paper_trading"] = PaperTradingProvider()
```

---

## 6. 依赖关系（不做）

```
Phase 8 严格不依赖以下模块:
  ✗ brain_node.py       — 不修改
  ✗ live_trader.py      — 不修改
  ✗ broker_adapter.py   — 不修改
  ✗ trading_state.py    — 不修改
  ✗ QMT / xtquant       — 不连接
  ✗ TRADE_SIGNAL        — 不发送
  ✗ live_portfolio.json — 不读写
  ✗ portfolio.json      — 不读写
```

---

## 7. 验收标准

| # | 标准 | 验证方式 |
|---|------|---------|
| 1 | `paper_trade_fills.jsonl` 从干净起点开始 | 文件行数 = 0 |
| 2 | `paper_performance.json` 自动生成 | `python -c "from core.paper_performance_analyzer import ..."` |
| 3 | `paper_trading_performance.md` 来自真实数据 | 读取 md 文件确认数值非 0 |
| 4 | `run_72h_observation.py` 调用 analyzer | grep `PaperPerformanceAnalyzer` |
| 5 | Dashboard 展示真实持仓/成交 | 启动 Streamlit 查看 |
| 6 | 不修改任何交易文件 | `git diff --name-only` 不包含 brain/live/broker_adapter |
