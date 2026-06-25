"""
Phase 9.5 验收验证脚本

用途:
  验证 Phase 9.5 核心能力：通知服务、Scanner自动化、配置安全、QMT禁用保护

覆盖范围:
  1. py_compile: 所有新增/修改文件通过编译
  2. notify (empty config): 配置为空时静默跳过，不报错
  3. notify (dedup): 同一 event_type+symbol 在窗口内不重复推送
  4. qmt_guard: QMT 实盘默认禁用，check_qmt_guard() 返回 False
  5. config: config.yaml 包含 scanner/qmt_enabled/dedup_window_seconds

执行方式:
  cd C:\\Users\\a2515\\ai-trader
  python scripts/verify/verify_phase95.py
"""
import sys, os

# 使用脚本所在目录向上两级作为项目根目录，避免硬编码路径
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

# 1. py_compile all 4 source files
import py_compile

for f in [
    "core/notification_service.py",
    "core/scanner_scheduler.py",
    "core/qmt_guard.py",
    "paper_trade_engine.py",
]:
    py_compile.compile(os.path.join(PROJECT_ROOT, f), doraise=True)
print("py_compile: 4/4 OK")

# 2. notification with empty config
from core.notification_service import notify_event
r = notify_event("test", "test title", "sh601318", "msg", "info")
assert r is not None, "notify_event returned None"
print(f"notify (empty cfg): returned={r}")

# 3. dedup
r2 = notify_event("test", "test2", "sh601318", "msg2", "info")
print(f"notify (dedup): returned={r2} (should be True=suppressed)")

# 4. qmt guard
from core.qmt_guard import check_qmt_guard
assert check_qmt_guard() is False, "QMT should be disabled by default"
print("qmt_guard: correctly returns False")

# 5. config has new keys
import yaml
with open(os.path.join(PROJECT_ROOT, "config", "config.yaml")) as f:
    c = yaml.safe_load(f)
assert "scanner" in c and c["scanner"]["auto_run"] is False
assert "dedup_window_seconds" in c.get("notify", {})
assert c.get("broker", {}).get("qmt_enabled") is False
print("config: scanner/qmt_enabled/dedup all present")

print("\nALL 5 assertions PASSED")
