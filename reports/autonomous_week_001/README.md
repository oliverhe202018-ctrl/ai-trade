# Autonomous Week 001

## How to Run

### Safety Validation (always run first)
```bash
python scripts/validate_autonomous_week.py
```

### Day 1 Premarket
```bash
python scripts/run_autonomous_premarket.py --day 1 --dry-run
```

### Day 1 Intraday
```bash
python scripts/run_autonomous_intraday.py --day 1 --dry-run
```

### Day 1 Postmarket
```bash
python scripts/run_autonomous_postmarket.py --day 1 --dry-run
```

## How to Abort
- Set `allow_live_trading: false` in `config/autonomous_week_001.yaml`
- Delete or archive `reports/autonomous_week_001/`
- Run `scripts/validate_autonomous_week.py` to confirm safety

## How to Rollback Strategy
- Edit `strategies/autonomous_week_001/strategy_registry.md`
- Set rollback target to previous version
- Never modify strategy files during intraday

## Why News Is Read-Only Context
- `news_data.allow_trade_trigger: false` in `config/config.yaml`
- `allow_news_to_trigger_trade: false` in autonomous config
- News provides context for premarket/postmarket analysis only
- News NEVER generates, modifies, or cancels trade orders

## Why Live Trading Is Forbidden
- `broker.mode: paper` in `config/config.yaml`
- `broker.qmt_enabled: false`
- `allow_live_trading: false` in autonomous config
- Dry-run is the default mode for intraday
- Supervised paper mode requires explicit --supervised-paper flag + duration

## Directory Structure
```
reports/autonomous_week_001/
  day1_premarket.md
  day1_postmarket.md
  day1_strategy_decision.md
  final_report.md

logs/autonomous_week_001/
  day1_events.log
  day1_trades.csv

strategies/autonomous_week_001/
  baseline_strategy.py
  strategy_registry.md
```
