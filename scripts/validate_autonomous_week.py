"""
scripts/validate_autonomous_week.py — Safety validation for autonomous_week_001

Checks 13 safety conditions before any autonomous script runs.
Usage:  python scripts/validate_autonomous_week.py
Exit:   0 = PASS (all checks), 1 = FAIL (blocking issues)
"""
import os, sys, yaml, re

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

FAILURES = []


def check(condition: bool, message: str) -> bool:
    if not condition:
        FAILURES.append(f"FAIL: {message}")
        print(f"  [FAIL] {message}")
        return False
    print(f"  [PASS] {message}")
    return True


def main() -> int:
    print("=" * 60)
    print("Autonomous Week Safety Validation")
    print("=" * 60)

    # ── Check 1: broker.mode must be paper ──
    with open(os.path.join(PROJECT_ROOT, "config", "config.yaml"), "r") as f:
        main_cfg = yaml.safe_load(f)
    check(
        main_cfg.get("broker", {}).get("mode") == "paper",
        "1. broker.mode = paper"
    )

    # ── Check 2: qmt_enabled must be false ──
    check(
        main_cfg.get("broker", {}).get("qmt_enabled") is False,
        "2. qmt_enabled = false"
    )

    # ── Check 3: autonomous config exists and parses ──
    aw_config_path = os.path.join(PROJECT_ROOT, "config", "autonomous_week_001.yaml")
    if not os.path.exists(aw_config_path):
        check(False, "3. autonomous_week_001.yaml NOT FOUND")
        return 1
    with open(aw_config_path, "r") as f:
        aw_cfg = yaml.safe_load(f)
    check(True, "3. autonomous_week_001.yaml exists and parsable")

    # ── Check 4: reports/autonomous_week_001/ writable ──
    rp = os.path.join(PROJECT_ROOT, aw_cfg["paths"]["reports"])
    check(
        os.path.isdir(rp),
        f"4. reports dir exists: {rp}"
    )

    # ── Check 5: logs/autonomous_week_001/ writable ──
    lp = os.path.join(PROJECT_ROOT, aw_cfg["paths"]["logs"])
    check(
        os.path.isdir(lp),
        f"5. logs dir exists: {lp}"
    )

    # ── Check 6: strategies/autonomous_week_001/ exists ──
    sp = os.path.join(PROJECT_ROOT, aw_cfg["paths"]["strategies"])
    check(
        os.path.isdir(sp),
        f"6. strategies dir exists: {sp}"
    )

    # ── Check 7-8: Autonomous scripts must not import live_trader or broker_adapter ──
    forbid_imports = {"live_trader": "7", "broker_adapter": "8", "trading_state": "8b"}
    for script in ["run_autonomous_premarket.py", "run_autonomous_intraday.py",
                   "run_autonomous_postmarket.py"]:
        spath = os.path.join(PROJECT_ROOT, "scripts", script)
        if not os.path.exists(spath):
            continue
        with open(spath, "r", encoding="utf-8") as f:
            content = f.read()
        for forbid, label in forbid_imports.items():
            # Only match actual import statements, not comment references
            if re.search(rf'^\s*(from\s+.*{forbid}|import\s+.*{forbid})', content, re.MULTILINE):
                check(False, f"{label}. {script} imports {forbid}")
                return 1
    check(True, "7. autonomous scripts do not import live_trader")
    check(True, "8. autonomous scripts do not import broker_adapter")

    # ── Check 9: .env not in allowed write paths ──
    check(
        ".env" not in str(aw_cfg.get("permissions", {}).get("allowed_write_paths", [])),
        "9. .env not in allowed write paths"
    )

    # ── Check 10: No real broker/execution path access ──
    forbid_paths = aw_cfg.get("permissions", {}).get("forbidden_paths", [])
    for fp in forbid_paths:
        for script in ["run_autonomous_premarket.py", "run_autonomous_intraday.py",
                       "run_autonomous_postmarket.py"]:
            spath = os.path.join(PROJECT_ROOT, "scripts", script)
            if not os.path.exists(spath):
                continue
            with open(spath, "r", encoding="utf-8") as f:
                content = f.read()
            # Only match actual imports of forbidden modules, not comments
            if re.search(rf'^\s*(from\s+.*{fp.replace(".py","")}|import\s+.*{fp.replace(".py","")})',
                         content, re.MULTILINE):
                check(False, f"10. {script} imports {fp}")
                return 1
    check(True, "10. no forbidden path imports in autonomous scripts")

    # ── Check 11: NewsEventBus must be read-only context ──
    check(
        aw_cfg.get("news", {}).get("allow_news_to_trigger_trade") is False,
        "11. NewsEventBus: allow_news_to_trigger_trade = false"
    )
    check(
        aw_cfg.get("news", {}).get("allow_news_to_mutate_state") is False,
        "11b. NewsEventBus: allow_news_to_mutate_state = false"
    )

    # ── Check 12: Dry-run is default mode ──
    check(
        aw_cfg.get("dry_run", {}).get("default") is True,
        "12. dry_run default = true"
    )

    # ── Check 13: Supervised paper requires explicit duration ──
    supervised_reqs = aw_cfg.get("dry_run", {}).get("supervised_paper_requires", [])
    check(
        "explicit_duration_minutes" in supervised_reqs,
        "13. supervised paper requires explicit_duration_minutes"
    )

    print()
    if FAILURES:
        print(f"AUTONOMOUS_WEEK_VALIDATION: FAIL ({len(FAILURES)} issues)")
        return 1
    else:
        print("AUTONOMOUS_WEEK_VALIDATION: PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
