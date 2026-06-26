"""
strategies/autonomous_week_001/baseline_strategy.py — Autonomous Week 策略基线

⚠️ 定位说明:
  本文件是 autonomous_week_001 的策略登记/说明文件。
  当前 paper_trade_engine.py 不加载策略模块——交易信号来自 data_cache/paper_signal_log.jsonl。
  本文件记录实验期间使用的策略定义，供盘前/盘后报告引用。

  如果后续要实现真正的 autonomous paper trading，需要:
  1. 新建 signal generation bridge 将策略输出写入 paper_signal_log.jsonl
  2. 验证 signals 合法性 (code/action/quantity/price)
  3. 确保 signal generator 不绕过 risk gate
  4. 将 strategy_registry.md 与 signal source 关联

当前版本:
  - version: 1.0
  - type: baseline (no-op)
  - active: false (signal bridge not implemented)
  - description: Placeholder strategy for autonomous_week_001 framework validation
"""

STRATEGY_VERSION = "1.0"
STRATEGY_NAME = "autonomous_week_001_baseline"
STRATEGY_TYPE = "dry_run_framework"
IS_ACTIVE = False  # Paper engine does not load strategies yet

# ── 策略配置占位 ──
config = {
    "name": STRATEGY_NAME,
    "version": STRATEGY_VERSION,
    "mode": "paper",
    "max_positions": 5,
    "risk_per_trade_pct": 2.0,
    "stop_loss_pct": -5.0,
    "take_profit_pct": 8.0,
    "signals": [],  # Will be populated by future signal bridge
}


def get_strategy_status() -> dict:
    """返回当前策略状态 (供盘前/盘后报告引用)。"""
    return {
        "version": STRATEGY_VERSION,
        "name": STRATEGY_NAME,
        "type": STRATEGY_TYPE,
        "active": IS_ACTIVE,
        "signal_source": "paper_signal_log.jsonl (external)",
        "note": "Strategy module is for documentation only; signals come from JSONL file",
    }
