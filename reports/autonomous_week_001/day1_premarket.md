# Autonomous Week — Day 1 Premarket Report

**Generated**: 2026-06-27 01:13:08

## 1. Safety Validation

Status: ✅ PASS

```
============================================================
Autonomous Week Safety Validation
============================================================
  [PASS] 1. broker.mode = paper
  [PASS] 2. qmt_enabled = false
  [PASS] 3. autonomous_week_001.yaml exists and parsable
  [PASS] 4. reports dir exists: C:\Users\a2515\ai-trader\reports/autonomous_week_001
  [PASS] 5. logs dir exists: C:\Users\a2515\ai-trader\logs/autonomous_week_001
  [PASS] 6. strategies dir exists: C:\Users\a2515\ai-trader\strategies/autonomous_week_001
  [PASS] 7. autonomous scripts do not import live_trader
  [PASS] 8. autonomous scripts do not import broker_adapter
  [PASS] 9. .env not in allowed write paths
  [PASS] 10. no forbidden path imports in autonomous scripts
  [PASS] 11. NewsEventBus: allow_news_to_trigger_trade = false
  [PASS] 11b. NewsEventBus: allow_news_to_mutate_state = false
  [PASS] 12. dry_run default = true
  [PASS] 13. supervised paper requires explicit_duration_minutes

AUTONOMOUS_WEEK_VALIDATION: PASS

```

## 2. Strategy Version

- **Version**: 1.0
- **Name**: autonomous_week_001_baseline
- **Active**: False
- **Signal Source**: paper_signal_log.jsonl (external)

## 3. News Coverage Status

- Coverage: WEAK
- Note: News is READ-ONLY context. No trade triggering.

## 4. Risk Limits

- **max_daily_loss_pct**: 2.0
- **max_trades_per_day**: 10
- **max_consecutive_losses**: 3
- **stop_on_system_error**: True

## 5. Entry Decision

✅ **Allowed to proceed to intraday** (dry-run only).

Note: This is a dry-run framework validation. No real trades will be executed.
