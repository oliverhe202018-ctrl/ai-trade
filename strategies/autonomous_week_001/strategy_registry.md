# Strategy Registry — autonomous_week_001

| Version | Name | Created | Reason | Enabled Day | Backtest Required | Rollback Target | Status |
|---------|------|---------|--------|-------------|-------------------|-----------------|--------|
| 1.0 | baseline_strategy | 2026-06-27 | Dry-run framework validation | Day 0 | No (no-op) | N/A | INACTIVE |

## Notes

- **Current state**: Paper engine reads signals from `data_cache/paper_signal_log.jsonl`.
  Strategy modules are NOT loaded by the engine.
- **Future**: When signal generation bridge is implemented, strategies will be the source
  of signals written to `paper_signal_log.jsonl`.
- **Rollback policy**: Each new strategy must specify a rollback target (previous version).
  Maximum one new strategy candidate per day.
