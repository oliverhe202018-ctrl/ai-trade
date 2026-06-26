"""
tests/test_autonomous_week_safety.py — Autonomous Week safety tests

Run: python -m pytest tests/test_autonomous_week_safety.py -v
"""
import os, sys, re, subprocess, pytest, yaml

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture
def aw_cfg():
    with open(os.path.join(PROJECT_ROOT, "config", "autonomous_week_001.yaml"), "r") as f:
        return yaml.safe_load(f)


@pytest.fixture
def main_cfg():
    with open(os.path.join(PROJECT_ROOT, "config", "config.yaml"), "r") as f:
        return yaml.safe_load(f)


class TestAutonomousWeekSafety:
    """Safety checks for autonomous_week_001."""

    def test_paper_mode_default(self, aw_cfg):
        assert aw_cfg["paper_trading_only"] is True

    def test_qmt_disabled(self, main_cfg):
        assert main_cfg["broker"]["qmt_enabled"] is False

    def test_dry_run_default(self, aw_cfg):
        assert aw_cfg["dry_run"]["default"] is True

    def test_intraday_default_does_not_start_engine(self):
        script = os.path.join(PROJECT_ROOT, "scripts", "run_autonomous_intraday.py")
        with open(script, "r") as f:
            content = f.read()
        # Default path should never call paper_trade_engine
        assert "subprocess.Popen" not in content or "paper_trade_engine" not in content
        assert "DRY-RUN" in content

    def test_no_live_trader_import(self):
        for script in ["run_autonomous_premarket.py", "run_autonomous_intraday.py",
                       "run_autonomous_postmarket.py"]:
            spath = os.path.join(PROJECT_ROOT, "scripts", script)
            if not os.path.exists(spath):
                continue
            with open(spath, "r") as f:
                content = f.read()
            assert "live_trader" not in content, f"{script} imports live_trader"

    def test_no_broker_adapter_import(self):
        for script in ["run_autonomous_premarket.py", "run_autonomous_intraday.py",
                       "run_autonomous_postmarket.py"]:
            spath = os.path.join(PROJECT_ROOT, "scripts", script)
            if not os.path.exists(spath):
                continue
            with open(spath, "r") as f:
                content = f.read()
            assert "broker_adapter" not in content, f"{script} imports broker_adapter"

    def test_dotenv_not_in_allowed_paths(self, aw_cfg):
        allowed = aw_cfg.get("permissions", {}).get("allowed_write_paths", [])
        assert ".env" not in str(allowed)
        assert ".env.example" not in str(allowed)

    def test_logs_path_restricted_to_autonomous(self, aw_cfg):
        lp = aw_cfg["paths"]["logs"]
        assert "autonomous_week_001" in lp
        assert "core" not in lp

    def test_reports_path_restricted_to_autonomous(self, aw_cfg):
        rp = aw_cfg["paths"]["reports"]
        assert "autonomous_week_001" in rp

    def test_strategies_path_restricted_to_autonomous(self, aw_cfg):
        sp = aw_cfg["paths"]["strategies"]
        assert "autonomous_week_001" in sp

    def test_postmarket_handles_no_trades(self):
        script = os.path.join(PROJECT_ROOT, "scripts", "run_autonomous_postmarket.py")
        with open(script, "r") as f:
            content = f.read()
        assert "No trades found" in content or "not os.path.exists" in content

    def test_validate_script_passes_with_valid_config(self):
        script = os.path.join(PROJECT_ROOT, "scripts", "validate_autonomous_week.py")
        r = subprocess.run([sys.executable, script], capture_output=True, text=True, cwd=PROJECT_ROOT)
        assert r.returncode == 0, f"Validation failed:\n{r.stdout}"

    def test_news_not_triggering_trade(self, aw_cfg):
        assert aw_cfg["news"]["allow_news_to_trigger_trade"] is False
        assert aw_cfg["news"]["allow_news_to_mutate_state"] is False

    def test_phase1_5_tag_exists(self):
        r = subprocess.run(["git", "tag", "-l", "phase1-5-*"], capture_output=True, text=True, cwd=PROJECT_ROOT)
        assert "phase1-5-news-sources-accepted" in r.stdout

    def test_premarket_script_runs(self):
        script = os.path.join(PROJECT_ROOT, "scripts", "run_autonomous_premarket.py")
        r = subprocess.run([sys.executable, script, "--day", "0", "--dry-run"],
                           capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=30)
        assert r.returncode == 0, f"Premarket failed:\n{r.stdout}"

    def test_intraday_script_runs(self):
        script = os.path.join(PROJECT_ROOT, "scripts", "run_autonomous_intraday.py")
        r = subprocess.run([sys.executable, script, "--day", "0", "--dry-run"],
                           capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=30)
        assert r.returncode == 0, f"Intraday failed:\n{r.stdout}"

    def test_postmarket_script_runs(self):
        script = os.path.join(PROJECT_ROOT, "scripts", "run_autonomous_postmarket.py")
        r = subprocess.run([sys.executable, script, "--day", "0", "--dry-run"],
                           capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=30)
        assert r.returncode == 0, f"Postmarket failed:\n{r.stdout}"
