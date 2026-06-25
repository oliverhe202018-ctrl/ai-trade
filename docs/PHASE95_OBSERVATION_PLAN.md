# Phase 9.5 Observation Plan

## Duration
24h - 72h

## Observe

| Item | Method |
|------|--------|
| Scanner auto-run stability | Check logs for scanner completion/errors |
| Notification delivery success rate | Check logs for sent/skipped/failed counts |
| Duplicate alert suppression | Verify dedup logs suppress same event |
| High score alert frequency | Track how many alerts per scan cycle |
| Paper Trading event notifications | Verify fills/rejects trigger notify_event |
| System errors | Monitor for exceptions or crashes |
| Paper trade engine liveness | Verify engine uptime and signal processing |

## Metrics To Collect

```
scanner_runs_count        → from scanner_scheduler logs
scanner_failed_count      → from scanner_scheduler logs
alerts_generated_count    → from notification_service logs
notifications_sent_count  → Telegram/webhook send success logs
notifications_skipped_count → empty config or dedup skip logs
notifications_failed_count  → Telegram/webhook send failures
dedup_suppressed_count    → "[NOTIFY] 重复通知，跳过" log count
paper_trade_event_count   → fills/rejects notification count
system_error_count        → CRITICAL level notification count
```

## Decision After Observation

### If stable:
- Proceed to Phase 10 short-term strategy design

### If unstable:
- Fix notification / scheduler / dedup issues first

### Do NOT proceed to QMT until:
- [ ] Auto scanner is stable (no crashes for 24h+)
- [ ] Notification is stable (delivery rate > 90% when configured)
- [ ] Paper Trading signals are observable in reports
- [ ] Strategy rules are explicit
