# Startup / Autorun Audit Report

> **Audit Time**: 2026-06-27 01:30
> **System**: Windows 10 (MINGW64_NT-10.0-26200)
> **User**: HEXIANGFEI\a2515
> **Repository**: C:\Users\a2515\ai-trader
> **Commit**: 85caa97

## 1. Startup Folder Audit

### 1.1 Current User Startup

| File | Target | Risk | Action |
|------|--------|------|--------|
| `Comet.lnk` | Perplexity Comet | NONE | KEEP |
| `Ollama.lnk` | Ollama LLM | NONE | KEEP |
| `start_system.bat - 快捷方式.lnk` | `ai-trader\scripts\start_system.bat` | **HIGH** | **MOVED to backup** |
| `start_matrix.bat - 快捷方式.lnk` | `ai-trader\start_matrix.bat` | **HIGH** | **MOVED to backup** |
| `wsl-bg.vbs` | WSL background | NONE | KEEP |

### 1.2 Common Startup

| File | Risk | Action |
|------|------|--------|
| `desktop.ini` | NONE | KEEP |

## 2. HIGH-Risk Items Detail

### 2.1 `start_system.bat` Startup Shortcut

| Field | Value |
|-------|-------|
| Target | `C:\Users\a2515\ai-trader\scripts\start_system.bat` |
| Risk | **HIGH** |
| Why | Launches `llama_monitor.py`, `agent_daemon.py`, `dashboard.py` on boot. While not directly calling `live_trader.py`, it starts long-running system processes that could interact with trading path. |
| Script contents | `start "LLM_Monitor" cmd /k "python scripts\llama_monitor.py"`<br>`start "Agent Daemon" cmd /k "python core/agent_daemon.py"`<br>`start "Web Dashboard" cmd /k "streamlit run core/dashboard.py"` |
| Action | **MOVED to `backups/startup_disabled/`** |
| Current status | Disabled (shortcut removed from Startup) |

### 2.2 `start_matrix.bat` Startup Shortcut

| Field | Value |
|-------|-------|
| Target | `C:\Users\a2515\ai-trader\start_matrix.bat` |
| Risk | **HIGH (CRITICAL)** |
| Why | **Directly launches `live_trader.py`** on boot:<br>`start "Fast Hand (Live Trader)" cmd /k "python live_trader.py"`<br>Also launches `brain_node.py`. This bypasses ALL autonomous_week safety gates. |
| Script contents | Line 13: `start "Fast Hand (Live Trader) - 监听 TCP:5555" cmd /k "cd /d C:\Users\a2515\ai-trader && python live_trader.py"`<br>Line 20: `start "Slow Brain (AI Commander)" cmd /k "python brain_node.py"` |
| Action | **MOVED to `backups/startup_disabled/`** |
| Current status | Disabled (shortcut removed from Startup) |

## 3. Registry Run Keys Audit

All three registry Run keys checked:
- `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- `HKLM\Software\Microsoft\Windows\CurrentVersion\Run`
- `HKLM\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Run`

| Key | Value | Related to ai-trader? |
|-----|-------|----------------------|
| OneDrive, Weixin, Edge, etc. | Various system/user apps | NO |

**No ai-trader related registry Run entries found.**

## 4. Scheduled Tasks Audit

| Task Name | State | Action | Risk |
|-----------|-------|--------|------|
| `AITrader_72H_Observer` | Ready | `python scripts/run_72h_observation.py` | MEDIUM — generates observation reports but does not trade |
| `autonomous_week_001_day1_premarket_once` | Ready | `scheduled_day1_premarket.bat` | **LOW — one-shot, validation only, no intraday** |
| `Hermes_Gateway` | Ready | `Hermes_Gateway.cmd` | NONE — Hermes infrastructure |
| `登录后启动gateway` | Ready | `hermes gateway start` | NONE — Hermes infrastructure |
| Various `Microsoft\Windows\*` | Various | System tasks | NONE |

**No `live_trader`, `daily_settlement`, `start_system`, or auto-trading tasks found in Task Scheduler.**

## 5. Summary of Findings

| Finding | Status |
|---------|--------|
| `start_system.bat` in Startup folder | ✅ DISABLED (moved to backup) |
| `start_matrix.bat` in Startup folder (launches live_trader.py!) | ✅ DISABLED (moved to backup) |
| `live_trader.py` scheduled task | ❌ NOT FOUND (user reference was from start_matrix.bat, not Task Scheduler) |
| `daily_settlement.py` scheduled task | ❌ NOT FOUND |
| `paper_trade_engine.py` auto-start | ❌ NOT FOUND |
| Registry Run keys with ai-trader | ❌ NONE FOUND |
| Autonomous week premarket (safe) | ✅ KEPT |

## 6. Risk Classification & Actions

| Item | Risk | Action Taken |
|------|------|-------------|
| `start_matrix.bat` startup shortcut | HIGH — launches `live_trader.py` | MOVED to `backups/startup_disabled/` |
| `start_system.bat` startup shortcut | HIGH — launches agent_daemon/dashboard | MOVED to `backups/startup_disabled/` |
| `AITrader_72H_Observer` task | MEDIUM — observation only | KEPT (no trade execution) |
| `autonomous_week_001_day1_premarket_once` | LOW — validation only | KEPT |

## 7. Backup Location

```
C:\Users\a2515\ai-trader\backups\startup_disabled\
  ├── start_system.bat - 快捷方式.lnk
  └── start_matrix.bat - 快捷方式.lnk
```

## 8. Remaining HIGH-Risk Startup Paths

**NONE.** Both `live_trader.py` auto-start paths have been disabled:
- `start_matrix.bat` shortcut removed from Startup folder
- No `live_trader` Task Scheduler tasks found
- No `live_trader` registry Run entries found

## 9. Recommended Post-Reboot Check

After next reboot, verify:
1. Start Menu > Startup folder is empty of ai-trader items
2. No `python live_trader.py` processes running
3. `autonomous_week_001_day1_premarket_once` task still exists in Task Scheduler
4. Run `python scripts/validate_autonomous_week.py` — should still PASS
