"""
scripts/scheduled_day1_premarket.py — One-shot scheduled premarket for Day 1

Triggered by Windows Task Scheduler (or cron/at on Linux).
Only runs validation + premarket. Does NOT start intraday.

Usage:
  python scripts/scheduled_day1_premarket.py
"""
import os, sys, subprocess, time
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

LOGS_DIR = os.path.join(PROJECT_ROOT, "logs", "autonomous_week_001")
os.makedirs(LOGS_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOGS_DIR, "day1_scheduled_premarket.log")

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

log("=== scheduled_day1_premarket started ===")
log(f"cwd: {os.getcwd()}")
log(f"python: {sys.executable}")

# Step 1: Run validation
log("Step 1: Running validate_autonomous_week.py...")
r = subprocess.run(
    [sys.executable, "scripts/validate_autonomous_week.py"],
    capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=30
)
log(f"validate exit code: {r.returncode}")

if r.returncode != 0:
    log("VALIDATION FAILED — aborting premarket")
    log(f"stderr: {r.stderr[-500:]}")
    log(f"stdout: {r.stdout[-500:]}")

    # Write failure report
    report_dir = os.path.join(PROJECT_ROOT, "reports", "autonomous_week_001")
    os.makedirs(report_dir, exist_ok=True)
    fail_report = os.path.join(report_dir, "day1_scheduled_premarket_failed.md")
    with open(fail_report, "w", encoding="utf-8") as f:
        f.write(f"# Day 1 Scheduled Premarket — FAILED\n\n")
        f.write(f"**Time**: {datetime.now()}\n\n")
        f.write("## Validation Output\n\n```\n")
        f.write(r.stdout[-3000:])
        f.write("\n```\n\n")
        f.write("## Action Required\n\n")
        f.write("Validation failed. Premarket was NOT executed. Intraday is BLOCKED.\n")
    log(f"Failure report: {fail_report}")
    sys.exit(1)

log("Validation PASS")

# Step 2: Run premarket
log("Step 2: Running run_autonomous_premarket.py --day 1...")
r2 = subprocess.run(
    [sys.executable, "scripts/run_autonomous_premarket.py", "--day", "1"],
    capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=30
)
log(f"premarket exit code: {r2.returncode}")
log(f"premarket stdout (last 300): {r2.stdout[-300:]}")
if r2.stderr:
    log(f"premarket stderr: {r2.stderr[-300:]}")

# Step 3: Verify report was generated
report_path = os.path.join(PROJECT_ROOT, "reports", "autonomous_week_001", "day1_premarket.md")
if os.path.exists(report_path):
    log(f"Report generated: {report_path}")
else:
    log("WARNING: day1_premarket.md not found!")

log("=== scheduled_day1_premarket complete ===")
log("Intraday is NOT started. Awaiting manual confirmation.")
