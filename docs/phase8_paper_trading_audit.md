# Phase 8: Paper Trading 连续观测 — 审计报告

> 审计日期: 2026-06-25  
> 审计范围: Paper Trading 数据源、观察脚本、回测引擎可复用性  
> 原则: 只审计，不修改交易逻辑

---

## 1. Paper Trading 数据源清单

### 1.1 `data_cache/paper_signal_log.jsonl`

| 属性 | 值 |
|------|-----|
| **用途** | Brain 节点产出的交易信号（brain_node.py --paper 模式写入） |
| **当前内容** | 3 条测试信号（sz000002 BUY 500@10, sh600519 BUY 1000@1500, sh600036 BUY 100@38） |
| **字段** | `code`, `action`, `quantity`, `price` |
| **格式** | JSONL (每行一条独立 JSON) |
| **是否稳定** | ⚠️ 当前是 Phase 1 测试数据；缺少 `timestamp`, `strategy`, `signal_source` 字段 |
| **可用于绩效统计** | ⚠️ 作为信号来源可用，但缺少时间戳无法做时间维度分析 |
| **缺失字段** | `timestamp`, `signal_source` (brain/manual), `strategy`, `confidence` |

### 1.2 `data_cache/paper_trade_fills.jsonl`

| 属性 | 值 |
|------|-----|
| **用途** | paper_trade_engine.py 输出的撮合成交流水 |
| **当前内容** | 9 条记录（来自 Phase 7 验证测试），混合了旧格式和非规范化数据 |
| **字段** | `timestamp`, `code`, `action`, `quantity`, `price`, `order_id`, `status`, `filled_qty`, `avg_price`, `reason` (10 字段完整) |
| **格式** | JSONL |
| **是否稳定** | ✅ Phase 7 归一化后**新产生**的记录稳定；⚠️ 文件内有 1 条旧 REJECTED 记录仍带 `filled_qty=100, avg_price=144.855`（line 7） |
| **可用于绩效统计** | ✅ 可直接统计 total/placed/filled/rejected/skipped/failed |
| **缺失字段** | `strategy`, `sector`, `realized_pnl`, `commission` |

> ⚠️ **数据污染警告**：该文件当前包含 Phase 7 验证测试产生的混合数据。建议在 Phase 8 正式启动前清空或归档。

### 1.3 `data_cache/paper_portfolio.json`

| 属性 | 值 |
|------|-----|
| **用途** | 模拟盘当前持仓与现金快照 |
| **当前内容** | cash=93,764.04, positions: {sh601318: 100@42.542, sh600900: 100@22.022} |
| **字段** | `cash`, `positions[code].quantity`, `positions[code].avg_cost` |
| **格式** | JSON |
| **是否稳定** | ✅ Phase 7 原子写入保证断电安全 |
| **可用于绩效统计** | ✅ 可读取持仓数量与成本基准 |
| **缺失字段** | `initial_cash`, `realized_pnl_total`, `unrealized_pnl`, `current_prices`（无行情来源无法填充） |

### 1.4 `data_cache/paper_signal_log.offset`

| 属性 | 值 |
|------|-----|
| **用途** | paper_trade_engine.py 的信号文件读取断点 |
| **当前内容** | 644（来自上一轮测试） |
| **字段** | 纯数字 |
| **是否稳定** | ✅ |
| **可用于绩效统计** | ⚠️ 辅助性，可验证是否丢信号 |

---

## 2. 已有观察脚本审计

### 2.1 `scripts/run_72h_observation.py`

| 功能 | 状态 | 说明 |
|------|------|------|
| Grep 交易隔离审计 | ✅ | 检查 feeds/*news*.py 是否包含禁止的 trade 关键字 |
| 行情健康度读取 | ✅ | 读取 `data_cache/market_health.json` |
| 资讯健康度读取 | ✅ | 读取 `data_cache/news_health.json` / SQLite |
| 72h 状态报告生成 | ✅ | 输出 `reports/observation_72h_status.md` |
| Paper Trading 报告 | ❌ **硬编码空壳** | `generate_paper_trading_report()` 写死静态文本，不读取 paper_* 文件 |
| 资讯只读报告 | ❌ **硬编码空壳** | `generate_news_readonly_report()` 同样写死静态文本 |
| Paper Trading 绩效分析 | ❌ **完全缺失** | 不读取 paper_trade_fills.jsonl / paper_portfolio.json |
| 定时执行机制 | ⚠️ 仅指南 | `observation_scheduler_guide.md` 描述了 Windows 任务计划程序，但未配置 |

**结论**: `run_72h_observation.py` 可以作为 Phase 8 的执行框架扩展，但 `generate_paper_trading_report()` 需要从硬编码空壳升级为实际读取 `paper_trade_fills.jsonl` + `paper_portfolio.json` 的统计引擎。

### 2.2 观察报告文件状态

| 文件 | 当前内容 | 是否可用 |
|------|---------|---------|
| `reports/observation_72h_status.md` | 系统级状态（行情/资讯/隔离），2026-06-25 21:53 | ✅ 框架可用 |
| `reports/paper_trading_observation.md` | 全静态占位文本，数据全为 0 | ❌ 需替换为真实数据 |
| `reports/news_readonly_observation.md` | 全静态占位文本 | ❌ 需替换为真实数据 |

---

## 3. `core/backtester.py` 可复用性审计

| 指标/函数 | backtester 中是否存在 | 可复用性 | 说明 |
|-----------|---------------------|---------|------|
| `total_return` (总收益率) | ✅ Line 754 | ⚠️ 内联计算 | 依赖 equity_curve，paper 没有日频净值曲线 |
| `max_drawdown` (最大回撤) | ✅ Line 757-764 | ⚠️ 可复用算法 | 同样需要日频净值序列 |
| `win_rate` (胜率) | ❌ 无 | — | `risk_manager.py` 中使用硬编码 0.45 |
| `profit_factor` (盈亏比) | ❌ 无 | — | 不存在 |
| `sharpe_ratio` (夏普比率) | ❌ 无 | — | 不存在 |
| 交易统计 (trade_log) | ✅ Line 771 | ⚠️ 格式不同 | backtester 有内部 trade_log，格式与 paper fills 不兼容 |
| 历史行情加载 | ✅ | ⚠️ 用腾讯 API | paper 场景需要实时行情（QMT xtdata 或 Tencent API） |
| 摩擦成本计算 | ✅ 常量 STAMP_DUTY/COMMISSION | ⚠️ | paper_trade_engine 已在 broker 层处理 |

**结论**: backtester 的算法思想可参考，但无法直接复用。因为：
1. backtester 使用日频历史数据 + equity_curve，paper 需要实时价格
2. backtester 的 trade_log 格式与 paper fills 不兼容
3. core 算法（回撤、收益率）可提取为工具函数，但不能直接套用

---

## 4. Phase 8 指标体系：可算 vs 不可算

### 4.1 ✅ 当前可直接计算（仅依赖 fills + portfolio）

以下明确列出可计算的指标：

| 指标 | 数据源 | 说明 |
|------|--------|------|
| `total_signals` | fills (全部记录) | 所有信号总数 |
| `filled_orders` | fills (status=FILLED) | 成功成交数 |
| `rejected_orders` | fills (status=REJECTED) | 拒单数 |
| `skipped_orders` | fills (status=SKIPPED) | 校验失败数 |
| `failed_orders` | fills (status=FAILED) | 执行异常数 |
| `buy_count` | fills (action=BUY, status=FILLED) | 买入成交数 |
| `sell_count` | fills (action=SELL, status=FILLED) | 卖出成交数 |
| `open_positions` | portfolio.positions | 当前持仓数量 |
| `initial_cash` | 固定 100,000 | 初始资金 |
| `current_cash` | portfolio.cash | 当前现金 |

### 4.2 ⚠️ 部分可算（有条件）

| 指标 | 所需条件 | 阻塞 |
|------|---------|------|
| `estimated_market_value` | 每只持仓当前价格 | **无行情数据源** |
| `total_equity` | cash + market_value | 同上 |
| `unrealized_pnl` | market_value - cost_basis | 同上 |

### 4.3 ❌ 当前不可算（标记为 unavailable）

以下明确列出不可计算的指标，并附带不可计算原因：

| 指标 | 不可计算原因 |
|------|-------------|
| `total_return` | 需要净值序列 (cash + market_value over time)；当前 paper_portfolio 无价格快照历史 |
| `win_rate` | fills 缺少 `realized_pnl` 字段，无法从成交流水直接算出每笔盈亏 |
| `avg_win / avg_loss` | 同上 — 无逐笔 realized_pnl |
| `profit_factor` | 同上 — 总盈利/总亏损需要每笔盈亏 |
| `max_drawdown` | 需要日频净值曲线；paper 当前不记录历史净值 |
| `sharpe_ratio` | 需要日频收益率序列；同 max_drawdown |
| `market_value` | 需要每只持仓的实时价格；当前 paper 链路未接入行情源 |
| `total_equity` | = cash + market_value，依赖 market_value |
| `unrealized_pnl` | = market_value - cost_basis，依赖 market_value |

> 核心阻塞原因：**paper_portfolio.json 不含 current_price，paper 链路未接入 QMT xtdata / Tencent API 行情推送**。这些都是不可计算的根本原因，第一版如实标记为 unavailable，不做伪造。

### 4.4 稳定性指标

| 指标 | 当前可算 | 说明 |
|------|---------|------|
| `live_file_pollution_check` | ⚠️ | 需检查 live_portfolio.json / portfolio.json 是否被意外写入 |
| `json_decode_errors` | ✅ | 解析 fails 时自动 log |
| `duplicate_signal_count` | ⚠️ | offset 机制不防重，需额外去重 |
| `offset_consistency` | ✅ | offset 文件存在且可读 |

---

## 5. 最小实现方案

### 5.1 新增模块：`paper_performance_analyzer.py`

```
职责：
  1. 读取 paper_trade_fills.jsonl
  2. 读取 paper_portfolio.json
  3. 按日期窗口统计交易
  4. 输出 data_cache/paper_performance.json
  5. 输出 reports/paper_trading_performance.md
```

第一版产出：

```
基础统计（从 fills 直接得出）:
  - 总信号数 / 成交数 / 拒单数 / 跳过数 / 失败数
  - BUY成交 / SELL成交
  - 最近 N 笔成交流水

组合统计（从 portfolio 直接得出）:
  - 当前现金
  - 当前持仓（代码/数量/成本）
  - 持仓成本总计

异常统计:
  - JSON 解析失败次数
  - REJECTED 原因汇总
  - 数据污染检测（fills 中存在多种格式混写时告警）
```

明确不做的：
- ❌ 不计算总收益率（无行情数据）
- ❌ 不计算胜率（无 realized_pnl）
- ❌ 不计算夏普/回撤（无净值序列）
- ❌ 不预测/建议/下单

### 5.2 升级 `run_72h_observation.py`

将 `generate_paper_trading_report()` 从硬编码替换为：

```python
def generate_paper_trading_report():
    analyzer = PaperPerformanceAnalyzer()
    stats = analyzer.analyze(window_hours=72)
    analyzer.write_markdown_report(stats, output_path)
```

### 5.3 Dashboard 接入

在 `core/dashboard.py` 的 `module_observation_reports()` 区域新增"📈 模拟盘绩效"面板：

```
展示:
  - 当前现金 / 持仓数量
  - 成交数 / 拒单数 / 跳过数
  - 最近 5 笔成交流水
  - 数据健康度（是否有污染）
```

### 5.4 Copilot 接入

新增 `core/paper_trading_provider.py`，实现 Copilot 可查询的上下文：

```
问题 → 对应查询:
  "模拟盘表现怎么样？" → 读取 paper_performance.json 摘要
  "最近有哪些成交？" → 读取 fills 最后 5 条
  "为什么有拒单？" → 读取 fills REJECTED 的 reason 字段
  "当前模拟盘持仓？" → 读取 paper_portfolio.json
```

---

## 6. 实施顺序

```
Phase 8a: 数据清理
  □ 归档/清空当前 paper_trade_fills.jsonl（含污染数据）
  □ 归档/清空当前 paper_portfolio.json（测试组合）
  □ 重置 paper_signal_log.offset 为 0

Phase 8b: 绩效分析器
  □ 新增 paper_performance_analyzer.py
  □ 输出 paper_performance.json + paper_trading_performance.md

Phase 8c: 观察脚本升级
  □ 修改 run_72h_observation.py 的 generate_paper_trading_report()
  □ 接入 Windows 任务计划程序或 cron 定时执行

Phase 8d: 展示层
  □ Dashboard 新增"模拟盘绩效"面板
  □ Copilot 新增 PaperTradingProvider
```

---

## 7. 验收标准

```
✅ 完成 4 个数据源审计
✅ 完成 run_72h_observation.py 可复用性审计
✅ 完成 backtester.py 可复用性审计
✅ 明确 10 个可直接计算的指标
✅ 明确 9 个当前不可算的指标及不可计算原因
✅ 输出的 paper_trading_performance.md 数据来自 fills/portfolio（非硬编码）
✅ 不修改 live_trader.py / brain_node.py / broker_adapter.py
✅ 不修改真实交易链路
✅ 不发送 TRADE_SIGNAL
✅ 不连接 QMT 下单
✅ 不写 live_portfolio.json / portfolio.json
✅ Paper Trading 结果只用于观察与复盘
✅ 不新增实盘能力
```
