# Phase 10 Strategy Spec

## LimitUpRelayStrategy

**定位**: 打板/涨停接力观察策略

**触发条件**:
- `fusion_score >= 85`
- `momentum_score >= 80`
- `main_money_score >= 70`
- `price_change_pct >= 7%`
- `volume_ratio >= 1.5`
- 无明显主力出货信号

**输出**:
- `WATCH` — confidence < 0.80，仅观察通知
- `PAPER_BUY_CANDIDATE` — confidence >= 0.80，可入 paper signal queue

**风险标记**: "涨停接力风险: 追高可能回落"

---

## IntradayTStrategy

**定位**: 做T/日内波段辅助策略

**T-BUY 触发条件**:
- 已有 Paper Trading 持仓
- 回落 >= 2%（或 低于 VWAP 2%）
- Tape Reader 显示资金回流（proxy_score > 50, imbalance > 0.1）
- fusion_score >= 60

**T-SELL 触发条件**:
- 已有 Paper Trading 持仓
- 涨幅 >= 2%
- 资金流出信号（proxy_score < 30 或 imbalance < -0.1）

**VWAP 降级**: VWAP 字段不存在时自动降级为 score-based

---

## 统一输出 Schema

```python
{
    "strategy_name": "LimitUpRelayStrategy" | "IntradayTStrategy",
    "symbol": "000001",
    "name": "xxx",
    "action": "WATCH" | "PAPER_BUY_CANDIDATE" | "PAPER_T_BUY_CANDIDATE" | "PAPER_T_SELL_CANDIDATE",
    "confidence": 0.82,
    "reason": "...",
    "features": {...},
    "risk_flags": [...],
    "mode": "paper",
    "timestamp": "..."
}
```

## Paper Trading 接入

```
WATCH → 不下单，仅通知
PAPER_BUY_CANDIDATE → 可入 paper signal queue
PAPER_T_BUY_CANDIDATE → 可入 paper signal queue
PAPER_T_SELL_CANDIDATE → 可入 paper signal queue
```

成交由 Paper Trading 撮合引擎决定。

## 通知格式

```
【AI-Trader 短线策略信号】
策略: {strategy_name}
股票: {symbol} {name}
动作: {action}
置信度: {confidence}
原因: {reason}
风险标记: {risk_flags}
当前模式: Paper Trading
```
