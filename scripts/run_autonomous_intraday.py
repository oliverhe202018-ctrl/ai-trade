"""
scripts/run_autonomous_intraday.py — Day N Intraday Execution (Dry-Run Framework)

⚠️  IMPORTANT: Default mode is DRY-RUN.
    Paper engine is NOT started automatically.
    To enable supervised paper trading (future), use --supervised-paper --duration-minutes N.

Usage:
  python scripts/run_autonomous_intraday.py --day 1 --dry-run

Modes:
  - dry-run (default): Validate safety, check paths, generate event log skeleton.
                        NO paper engine started. NO trades executed.
  - supervised-paper (future): Start paper_trade_engine.py with timebox + log capture.
                        Requires --duration-minutes and --supervised-paper flags.

Outputs:
  logs/autonomous_week_001/dayN_events.log
  logs/autonomous_week_001/dayN_trades.csv
"""
import os, sys, yaml, argparse, subprocess, csv
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)


def load_aw_config():
    with open(os.path.join(PROJECT_ROOT, "config", "autonomous_week_001.yaml"), "r") as f:
        return yaml.safe_load(f)


def run_safety_validation():
    script = os.path.join(PROJECT_ROOT, "scripts", "validate_autonomous_week.py")
    r = subprocess.run([sys.executable, script], capture_output=True, text=True, cwd=PROJECT_ROOT)
    return r.returncode == 0, r.stdout


def write_event_log(day: int, aw_cfg: dict, mode: str, safety_passed: bool):
    """Write dayN_events.log."""
    logs_dir = os.path.join(PROJECT_ROOT, aw_cfg["paths"]["logs"])
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, f"day{day}_events.log")

    dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"[{dt}] autonomous_week_001 intraday started\n")
        f.write(f"[{dt}] day={day} mode={mode}\n")
        f.write(f"[{dt}] safety_passed={safety_passed}\n")
        if mode == "dry-run":
            f.write(f"[{dt}] DRY-RUN: paper engine NOT started\n")
            f.write(f"[{dt}] DRY-RUN: no trades executed\n")
            f.write(f"[{dt}] DRY-RUN: framework validation only\n")
        f.write(f"[{dt}] autonomous_week_001 intraday complete\n")

    print(f"  Events log: {log_path}")
    return log_path


def write_trade_log(day: int, aw_cfg: dict, mode: str):
    """Write dayN_trades.csv (empty in dry-run)."""
    logs_dir = os.path.join(PROJECT_ROOT, aw_cfg["paths"]["logs"])
    os.makedirs(logs_dir, exist_ok=True)
    csv_path = os.path.join(logs_dir, f"day{day}_trades.csv")

    columns = ["timestamp", "symbol", "side", "quantity", "price",
               "reason", "strategy_version", "risk_check", "paper_only", "pnl"]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        if mode == "dry-run":
            # Write a comment row explaining why empty
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "N/A", "N/A", "0", "0.0",
                "DRY-RUN: no paper engine started, no fills generated",
                "1.0", "PASS", "true", "0.0"
            ])

    print(f"  Trades log: {csv_path}")
    return csv_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", type=int, default=1, help="Day number (1-5)")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Dry-run mode (default)")
    parser.add_argument("--supervised-paper", action="store_true", default=False,
                        help="Enable supervised paper trading (future; blocked)")
    parser.add_argument("--duration-minutes", type=int, default=0,
                        help="Duration for supervised paper (required)")
    args = parser.parse_args()

    # ── Determine mode ──
    if args.supervised_paper:
        print("=" * 60)
        print("❌ SUPERVISED PAPER MODE — NOT YET AVAILABLE")
        print("=" * 60)
        print("  Supervised paper trading requires signal generation bridge.")
        print("  See: reports/AUTONOMOUS_WEEK_001_IMPLEMENTATION_REPORT.md")
        print("  Fall back to dry-run.")
        mode = "dry-run"
    else:
        mode = "dry-run"

    print("=" * 60)
    print(f"Autonomous Week Intraday — Day {args.day} ({mode})")
    print("=" * 60)

    # 1. Load config
    aw_cfg = load_aw_config()

    # 2. Safety validation
    print("\n--- Safety Validation ---")
    safety_passed, safety_output = run_safety_validation()
    print(safety_output)

    if not safety_passed:
        print("\n❌ SAFETY CHECK FAILED — ABORTING INTRADAY")
        return

    # 3. Write logs
    print("\n--- Logging ---")
    write_event_log(args.day, aw_cfg, mode, safety_passed)
    write_trade_log(args.day, aw_cfg, mode)

    # 4. Summary
    print()
    print(f"Intraday Day {args.day} complete.")
    print(f"  Mode: {mode}")
    print(f"  Paper engine started: NO")
    print(f"  Trades executed: 0")
    print(f"  Real fills generated: NO")


if __name__ == "__main__":
    main()
