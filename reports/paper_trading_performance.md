# 模拟盘绩效报告 (Paper Trading Performance)

> 生成时间: 2026-06-25 22:12:57
> 观察窗口: 72 小时
> 数据条目: 5 条

> ⚠️ **免责声明**: 当前报告只基于模拟盘成交/持仓文件统计，不包含真实行情估值，不构成投资建议。

---

## 1. 基础统计

| 指标 | 数值 |
|------|------|
| 信号总数 | 5 |
| 成交 (FILLED) | 3 |
| 拒单 (REJECTED) | 1 |
| 跳过 (SKIPPED) | 1 |
| 失败 (FAILED) | 0 |
| 其他状态 | 0 |

## 2. 交易方向

| 指标 | 数值 |
|------|------|
| BUY 成交 | 2 |
| SELL 成交 | 1 |

## 3. 组合快照

| 指标 | 数值 |
|------|------|
| 初始资金 | ¥100,000.00 |
| 当前现金 | ¥93,764.04 |
| 持仓标的数 | 2 |
| 持仓总成本 | ¥6,456.40 |

### 持仓明细

| 代码 | 数量 | 均价 | 成本 |
|------|------|------|------|
| sh601318 | 100 | ¥42.542 | ¥4,254.20 |
| sh600900 | 100 | ¥22.022 | ¥2,202.20 |

## 4. 异常统计

- 数据污染: ✅ 无 (0 条)
- 重复订单: ✅ 无 (0 条)

### 拒单原因汇总

- REJECTED by MockBrokerAdapter: 1 次

## 5. 不可用指标 (unavailable)

以下指标因缺少实时行情数据或 fills 字段不完整，当前无法准确计算：

| 指标 | 原因 |
|------|------|
| `avg_loss` | unavailable: 同上 |
| `avg_win` | unavailable: 同上 |
| `estimated_market_value` | unavailable: 缺少实时行情, paper_portfolio 无 current_price |
| `max_drawdown` | unavailable: 需要日频净值曲线 |
| `profit_factor` | unavailable: 同上 |
| `realized_pnl` | unavailable: fills 缺少 realized_pnl 字段 |
| `sharpe_ratio` | unavailable: 需要日频收益率序列 |
| `total_equity` | unavailable: 需要 market_value + cash |
| `total_return` | unavailable: 需要净值序列 (cash + market_value over time) |
| `unrealized_pnl` | unavailable: 需要 market_value - cost_basis |
| `win_rate` | unavailable: fills 缺少逐笔 realized_pnl |

## 6. 最近成交流水

| 时间 | 操作 | 代码 | 数量 | 状态 |
|------|------|------|------|------|
| 2026-06-27 10:15:00 | ✅ SELL | sh601318 | 100 | FILLED |
| 2026-06-27 10:00:00 | ✅ BUY | sh601318 | 200 | FILLED |
| 2026-06-27 09:45:00 | ❌ SELL | sz000858 | 100 | REJECTED |
| 2026-06-27 09:36:00 | ⏭️ BUY | badcode | 100 | SKIPPED |
| 2026-06-27 09:35:00 | ✅ BUY | sh600900 | 100 | FILLED |

---

*报告由 paper_performance_analyzer.py 自动生成于 2026-06-25 22:12:57*
