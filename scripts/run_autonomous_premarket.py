"""
scripts/run_autonomous_premarket.py — Day N Premarket Report Generator

Usage:
  python scripts/run_autonomous_premarket.py --day 1 --dry-run

Generates: reports/autonomous_week_001/dayN_premarket.md
"""
import os, sys, yaml, argparse
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

import subprocess


def load_aw_config():
    path = os.path.join(PROJECT_ROOT, "config", "autonomous_week_001.yaml")
    with open(path, "r") as f:
        return yaml.safe_load(f)


def run_safety_validation():
    """Run validate_autonomous_week.py as subprocess; return (passed, output)."""
    script = os.path.join(PROJECT_ROOT, "scripts", "validate_autonomous_week.py")
    r = subprocess.run([sys.executable, script], capture_output=True, text=True, cwd=PROJECT_ROOT)
    return r.returncode == 0, r.stdout


def read_news_coverage():
    """Read-only: get coverage status from CoverageGate."""
    try:
        from core.news_coverage_gate import get_coverage_gate
        gate = get_coverage_gate()
        report = gate.get_status_report()
        return report
    except Exception:
        return {"status": "unknown", "note": "CoverageGate unavailable (no DB?)"}


def generate_report(day: int, safety_passed: bool, safety_output: str,
                    news_status: dict, strategy_info: dict, aw_cfg: dict):
    """Generate dayN_premarket.md report."""
    reports_dir = os.path.join(PROJECT_ROOT, aw_cfg["paths"]["reports"])
    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(reports_dir, f"day{day}_premarket.md")

    dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# Autonomous Week — Day {day} Premarket Report\n\n")
        f.write(f"**Generated**: {dt}\n\n")
        f.write("## 1. Safety Validation\n\n")
        f.write(f"Status: {'✅ PASS' if safety_passed else '❌ FAIL'}\n\n")
        f.write("```\n")
        f.write(safety_output[-2000:])
        f.write("\n```\n\n")

        f.write("## 2. Strategy Version\n\n")
        f.write(f"- **Version**: {strategy_info.get('version', 'unknown')}\n")
        f.write(f"- **Name**: {strategy_info.get('name', 'unknown')}\n")
        f.write(f"- **Active**: {strategy_info.get('active', False)}\n")
        f.write(f"- **Signal Source**: {strategy_info.get('signal_source', 'unknown')}\n\n")

        f.write("## 3. News Coverage Status\n\n")
        f.write(f"- Coverage: {news_status.get('status', 'unknown')}\n")
        f.write(f"- Note: News is READ-ONLY context. No trade triggering.\n\n")

        f.write("## 4. Risk Limits\n\n")
        for k, v in aw_cfg.get("risk_limits", {}).items():
            f.write(f"- **{k}**: {v}\n")
        f.write("\n")

        f.write("## 5. Entry Decision\n\n")
        if safety_passed:
            f.write("✅ **Allowed to proceed to intraday** (dry-run only).\n\n")
            f.write("Note: This is a dry-run framework validation. No real trades will be executed.\n")
        else:
            f.write("❌ **NOT ALLOWED** — Safety validation failed.\n\n")
            f.write("Fix the issues above before proceeding to intraday.\n")

    print(f"  Report: {report_path}")
    return report_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", type=int, default=1, help="Day number (1-5)")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Dry-run mode (default)")
    args = parser.parse_args()

    print("=" * 60)
    print(f"Autonomous Week Premarket — Day {args.day}")
    print("=" * 60)

    # 1. Load config
    aw_cfg = load_aw_config()
    print(f"  Experiment: {aw_cfg['experiment_name']}")
    print(f"  Paper trading only: {aw_cfg['paper_trading_only']}")
    print(f"  Mode: {'dry-run' if args.dry_run else 'live (BLOCKED)'}")

    # 2. Safety validation
    print("\n--- Safety Validation ---")
    safety_passed, safety_output = run_safety_validation()
    print(safety_output)

    # 3. Strategy status
    print("--- Strategy ---")
    try:
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "strategies", "autonomous_week_001"))
        from baseline_strategy import get_strategy_status
        strategy_info = get_strategy_status()
    except Exception:
        strategy_info = {"version": "1.0", "name": "unavailable", "active": False,
                         "signal_source": "paper_signal_log.jsonl"}
    print(f"  Version: {strategy_info['version']}, Active: {strategy_info['active']}")

    # 4. News coverage (read-only)
    print("--- News Coverage ---")
    news_status = read_news_coverage()
    print(f"  Status: {news_status.get('status', 'unknown')}")

    # 5. Generate report
    print("--- Report ---")
    generate_report(args.day, safety_passed, safety_output, news_status,
                    strategy_info, aw_cfg)

    print()
    print(f"Premarket Day {args.day} complete.")


if __name__ == "__main__":
    main()
