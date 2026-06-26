"""
scripts/run_autonomous_postmarket.py — Day N Postmarket Analysis

Usage:
  python scripts/run_autonomous_postmarket.py --day 1 --dry-run

Reads: logs/autonomous_week_001/dayN_trades.csv
Generates:
  reports/autonomous_week_001/dayN_postmarket.md
  reports/autonomous_week_001/dayN_strategy_decision.md
"""
import os, sys, yaml, argparse, csv
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)


def load_aw_config():
    with open(os.path.join(PROJECT_ROOT, "config", "autonomous_week_001.yaml"), "r") as f:
        return yaml.safe_load(f)


def parse_trades_csv(day: int, aw_cfg: dict) -> list:
    """Read dayN_trades.csv. Returns list of trade dicts (excluding header/comment)."""
    logs_dir = os.path.join(PROJECT_ROOT, aw_cfg["paths"]["logs"])
    csv_path = os.path.join(logs_dir, f"day{day}_trades.csv")

    trades = []
    if not os.path.exists(csv_path):
        return trades

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip dry-run comment rows
            if row.get("reason", "").startswith("DRY-RUN"):
                continue
            if row.get("symbol") == "N/A":
                continue
            trades.append(row)
    return trades


def read_news_coverage():
    """Read-only news context."""
    try:
        from core.news_coverage_gate import get_coverage_gate
        gate = get_coverage_gate()
        return gate.get_status_report()
    except Exception:
        return {"status": "unknown"}


def generate_postmarket_report(day: int, aw_cfg: dict, trades: list, news_status: dict):
    """Generate dayN_postmarket.md."""
    reports_dir = os.path.join(PROJECT_ROOT, aw_cfg["paths"]["reports"])
    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(reports_dir, f"day{day}_postmarket.md")

    dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# Autonomous Week — Day {day} Postmarket Report\n\n")
        f.write(f"**Generated**: {dt}\n\n")

        f.write("## 1. Trade Summary\n\n")
        if not trades:
            f.write("**No trades found.**\n\n")
            f.write("- Paper engine was not started (dry-run mode).\n")
            f.write("- No fills were generated.\n")
            f.write("- PnL stats unavailable.\n")
            f.write("- This is a framework validation run only.\n\n")
        else:
            f.write(f"- Total trades: {len(trades)}\n")
            f.write(f"- Win rate: N/A (dry-run)\n")
            f.write(f"- Total PnL: N/A (dry-run)\n")
            f.write(f"- Max drawdown: N/A\n\n")

        f.write("## 2. Risk Check\n\n")
        for k, v in aw_cfg.get("risk_limits", {}).items():
            f.write(f"- **{k}**: {v}\n")
        f.write("\n")

        f.write("## 3. News Context (Read-Only)\n\n")
        f.write(f"- Coverage status: {news_status.get('status', 'unknown')}\n")
        f.write("- Note: News was used for context only. No trade triggering.\n\n")

        f.write("## 4. Strategy Status\n\n")
        f.write("- Strategy version: 1.0 (baseline)\n")
        f.write("- Active: false (signal bridge not implemented)\n")
        f.write("- Rollback target: N/A\n\n")

    print(f"  Postmarket report: {report_path}")
    return report_path


def generate_strategy_decision(day: int, aw_cfg: dict, trades: list):
    """Generate dayN_strategy_decision.md."""
    reports_dir = os.path.join(PROJECT_ROOT, aw_cfg["paths"]["reports"])
    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(reports_dir, f"day{day}_strategy_decision.md")

    dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# Autonomous Week — Day {day} Strategy Decision\n\n")
        f.write(f"**Generated**: {dt}\n\n")
        f.write(f"## Decision\n\n")
        f.write(f"- **Keep current strategy**: Yes (baseline, no changes)\n")
        f.write(f"- **Rollback**: No\n")
        f.write(f"- **New candidate**: None\n\n")
        f.write(f"## Rationale\n\n")
        if not trades:
            f.write("No trades executed (dry-run). No performance data to evaluate.\n")
        f.write("Strategy changes are not permitted during intraday.\n")
        f.write("Maximum one new strategy candidate per day.\n\n")

    print(f"  Strategy decision: {report_path}")
    return report_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true", default=True)
    args = parser.parse_args()

    print("=" * 60)
    print(f"Autonomous Week Postmarket — Day {args.day}")
    print("=" * 60)

    aw_cfg = load_aw_config()

    # 1. Parse trades
    print("\n--- Trades ---")
    trades = parse_trades_csv(args.day, aw_cfg)
    print(f"  Trades found: {len(trades)}")

    # 2. News context (read-only)
    print("\n--- News Context ---")
    news_status = read_news_coverage()
    print(f"  Coverage: {news_status.get('status', 'unknown')}")

    # 3. Generate reports
    print("\n--- Reports ---")
    generate_postmarket_report(args.day, aw_cfg, trades, news_status)
    generate_strategy_decision(args.day, aw_cfg, trades)

    print()
    print(f"Postmarket Day {args.day} complete.")


if __name__ == "__main__":
    main()
