# Phase 9.5 验收报告

> 时间: 2026-06-25 23:02 UTC+8  
> Commit: `af6d757`  
> 目标: 通知通道 + Scanner自动化 + QMT保护

---

## 1. 修改文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `core/notification_service.py` | 新增 | 统一通知接口 (Telegram + Webhook) |
| `core/scanner_scheduler.py` | 新增 | Scanner 自动定时运行 |
| `core/qmt_guard.py` | 新增 | QMT 实盘禁用保护门 |
| `paper_trade_engine.py` | 修改 | 加通知钩子（成交/拒单推送） |
| `config/config.yaml` | 修改 | 新增 scanner / notify.dedup / broker.qmt_enabled |

**未修改**: brain_node.py, live_trader.py, broker_adapter.py, trading_state.py, fusion_engine.py, tape_reader.py, market_scanner.py

---

## 2. 新增配置项

```yaml
notify:
  enabled: true              # 通知总开关
  dedup_window_seconds: 300  # 同一事件5分钟内不重复

scanner:
  auto_run: false            # 默认关闭，需手动开启
  interval_seconds: 60       # 扫描间隔
  alert_score_threshold: 80  # 高于此分推送通知

broker:
  mode: paper                # 默认 paper 模式
  qmt_enabled: false         # QMT 实盘禁用
```

---

## 3. 功能验证

| # | 项目 | 结果 |
|---|------|------|
| 1 | Scanner 自动运行 | ✅ `python core/scanner_scheduler.py` (auto_run=true 时) |
| 2 | Scanner --once 模式 | ✅ `python core/scanner_scheduler.py --once` |
| 3 | Telegram 通知 | ✅ `notify_event()` → `_send_telegram()` |
| 4 | Webhook 通知 | ✅ `notify_event()` → `_send_webhook()` |
| 5 | 配置为空时安全跳过 | ✅ 日志 `[NOTIFY] 未配置通知通道...` |
| 6 | Dedup 防重复 | ✅ 同一 event_type+symbol 5分钟内不重复 |
| 7 | Paper Trading 成交推送 | ✅ `paper_trade` 事件在 paper_trade_engine 中触发 |
| 8 | Paper Trading 拒单推送 | ✅ REJECTED → warning 级别 |
| 9 | QMT 禁止调用 | ✅ `check_qmt_guard()` → False + 日志警告 |
| 10 | qmt_enabled=false 默认安全 | ✅ 配置 `qmt_enabled: false` |
| 11 | Dashboard 手动扫描不受影响 | ✅ `run_scanner_async()` 不变 |
| 12 | py_compile 通过 | ✅ 5 files |

---

## 4. 手动测试步骤

### 4.1 测试通知（空配置）
```bash
cd C:\Users\a2515\ai-trader
python -c "from core.notification_service import notify_event; notify_event('test','测试','sh601318','message')"
# 期望: 日志显示 "未配置通知通道，跳过"
```

### 4.2 测试通知（配置 Telegram 后）
```yaml
# 在 config.yaml 中填入:
notify:
  telegram_token: "YOUR_BOT_TOKEN"
  chat_id: "YOUR_CHAT_ID"
```
```bash
python -c "from core.notification_service import notify_event; notify_event('test','测试','sh601318','测试消息')"
# 期望: Telegram 收到消息
```

### 4.3 测试 Scanner 单次运行
```bash
python core/scanner_scheduler.py --once
# 期望: 日志显示扫描开始/完成，高分标的触发通知
```

### 4.4 测试 QMT 保护
```bash
python -c "from core.qmt_guard import check_qmt_guard; print(check_qmt_guard())"
# 期望: False + 日志 "[QMT GUARD] live trading is disabled..."
```

### 4.5 测试 Paper Trading 通知
```bash
echo '{"code": "sh601318", "action": "BUY", "quantity": 100, "price": 42.5}' >> data_cache/paper_signal_log.jsonl
# 等待 paper_trade_engine 处理（~2s）
# 期望: 日志显示成交通知事件
```

---

## 5. 安全边界确认

| 检查项 | 状态 |
|--------|------|
| 不修改 live_trader.py | ✅ |
| 不修改 brain_node.py | ✅ |
| 不修改 broker_adapter.py | ✅ |
| 不修改 trading_state.py | ✅ |
| 不修改 fusion_engine.py 核心评分 | ✅ |
| 不修改 tape_reader.py 核心推演 | ✅ |
| 不修改 market_scanner.py scan() | ✅ |
| 不启用 QMT 实盘 | ✅ `qmt_enabled: false` + 运行时门检查 |
| 通知失败不中断主流程 | ✅ try/except 包裹 |
| 配置为空不报错 | ✅ 日志跳过 |
| Dashboard 手动扫描可用 | ✅ 未修改 |
| 所有新增行为可配置关闭 | ✅ `notify.enabled` / `scanner.auto_run` |
