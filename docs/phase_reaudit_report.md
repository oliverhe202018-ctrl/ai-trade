# 源码复审与真实性验收报告 (Phase 1 - Phase 7)

## Step 1：阶段-文件映射表

**Phase 1 Copilot：**
- 涉及文件：`core/copilot_service.py`
- 真实存在：✅ 是
- 可编译：✅ 是
- 核心函数：`classify_intent`, `ContextProvider`, `build_dynamic_context`, `apply_prompt_guard`

**Phase 2 Fusion / Radar：**
- 涉及文件：`core/fusion_engine.py`, `core/radar_manager.py`
- 真实存在：✅ 是
- 可编译：✅ 是
- 核心函数：`score_message`, `score_fund_flow`, `score_trend`, `evaluate`, `RadarManager.scan_once`

**Phase 3 Market Scanner：**
- 涉及文件：`core/market_scanner.py`, `data_cache/market_candidates.json`
- 真实存在：✅ 是
- 可编译：✅ 是
- 核心函数：`_get_qmt_codes`, `scan`, `_build_daily_static_cache`

**Phase 4 Potential Discovery：**
- 涉及文件：`core/potential_discovery.py`, `data_cache/potential_picks.json`
- 真实存在：✅ 是
- 可编译：✅ 是
- 核心函数：`run`, `_merge_llm_picks`, `_generate_fallback_picks`, `_parse_llm_response`

**Phase 5 Tape Reader：**
- 涉及文件：`core/tape_reader.py`, `data_cache/main_money_tracking.json`
- 真实存在：✅ 是
- 可编译：✅ 是
- 核心函数：`tick`, `_calculate_proxy_score`, `start_tape_reader_async`, `stop_tape_reader`

**Phase 6 System Cascade：**
- 涉及文件：系统整合（`ai_trader.py`, `brain_node.py`）
- 真实存在：✅ 是
- 可编译：✅ 是

**Phase 7 Paper Trading Shadow Bridge：**
- 涉及文件：`brain_node.py`, `paper_trade_engine.py`, `core/broker_adapter.py`, `core/state_manager.py`
- 真实存在：✅ 是
- 可编译：✅ 是
- 核心函数：`_append_paper_signal`, `run_paper_trade_engine`, `_sync_to_mock_broker`, `_sync_to_portfolio`

---

## Step 2：编译核查 (py_compile)

测试命令：`python -m py_compile <file>`
- `core/copilot_service.py`：✅ 通过
- `core/fusion_engine.py`：✅ 通过
- `core/market_scanner.py`：✅ 通过
- `core/potential_discovery.py`：✅ 通过
- `core/tape_reader.py`：✅ 通过
- `paper_trade_engine.py` (注: 根目录下)：✅ 通过
- `brain_node.py`：✅ 通过
- `ai_trader.py` (即Dashboard)：✅ 通过
- `core/broker_adapter.py`：✅ 通过
- `core/state_manager.py`：✅ 通过
- `live_trader.py`：✅ 通过

---

## Step 3：Phase 1 Copilot 复审
- 是否存在 `classify_intent`：✅ 是 (第22行)
- 是否存在 `ContextProvider`：✅ 是 (第53行)
- 是否有全套 Provider：✅ 是 (包含 Portfolio, News, Signal, System, Alert, Potential, MainMoney)
- 是否有 Prompt Guard：✅ 是 (第177行 `apply_prompt_guard`)
- 是否有超时降级：✅ 是 (`requests.post` timeout=10，捕获异常后返回降级文本)
- 是否不生成交易指令：✅ 是
- 是否只读数据文件：✅ 是 (`_load_json_safe`)
- **结论：通过**

---

## Step 4：Phase 2 Fusion / Radar 复审
- `fusion_engine.py` 是否可运行：✅ 是
- 是否有明确 `evaluate` 接口：✅ 是
- 是否读取行情/新闻：✅ 是 (`score_message` 读 DB, `score_fund_flow` 读 QMT快照)
- 是否有降级逻辑：✅ 是 (无源时 fallback 50.0)
- `radar_manager.py` 真实存在：✅ 是
- radar 是否只观察：✅ 是 (只写入 `radar_alerts.json`)
- **结论：通过**

---

## Step 5：Phase 3 Market Scanner 复审
- 全市场获取：✅ 是 (`xtdata.get_stock_list_in_sector('上证A股')` + `深证A股`)
- 输出 `market_candidates.json`：✅ 是
- 原子写入：✅ 是 (采用 `tempfile.mkstemp` 与 `os.replace`)
- 扫描锁：✅ 是 (`_scan_lock = threading.Lock()`)
- 不调用 LLM / 不发 TRADE_SIGNAL：✅ 是
- `market_candidates.json` 包含所需全部字段：✅ 是
- **结论：通过**

---

## Step 6：Phase 4 Potential Discovery 复审
- 只读取 `market_candidates.json` / 不重扫 5000 股：✅ 是 (`_read_candidates`)
- 漏斗机制：✅ 是 (Top100 -> Top30 -> Top10 -> Picks)
- 调用 `FusionEngine` / 仅最后 LLM：✅ 是
- LLM fallback：✅ 是 (`_generate_fallback_picks`)
- 违禁交易词清洗：✅ 是 (`banned_words` 替换)
- 原子写入 `potential_picks.json`：✅ 是
- Schema 字段完整性 (`fusion_score`, `scanner_score`, `potential_score`)：✅ 是（最新源码 `_merge_llm_picks` 已生成完整 Schema 且 JSON 样本已更新）。
- **结论：通过**

---

## Step 7：Phase 5 Tape Reader 复审
- 只读 Top30：✅ 是 (`_read_top30()[:30]`)
- 30秒 TTL 防污染：✅ 是 (`_cleanup_stale_cache` `STATE_TTL_SECONDS = 30`)
- 输出 `main_money_tracking.json`：✅ 是
- 带有 `estimated` / `proxy` 字段：✅ 是 (`estimated_active_buy_amount` 等)
- `data_level` = `L1_PROXY`：✅ 是
- `stop_tape_reader` 设计合规：✅ 是 (只 `_stop_event.set()`)
- lock release：✅ 是 (存在于 `finally` 块内)
- **结论：通过**

---

## Step 8：Phase 6 系统联调复审
- 冷启动缺文件不白屏：✅ 是 (`_load_json_safe` 兼容)
- 并发读取 / 原子写入：✅ 是 (核心组件均使用 `tempfile`)
- 测试日志 / 证据：⚠️ **部分通过 / 无法验证**。由于原始的启动时高频抖动日志已随会话清空，无法在此刻重新提供原封不动的测试输出，此项标记为需要补充验收。
- **结论：部分通过 / 无法验证测试日志**

---

## Step 9：Phase 7 Paper Trading Shadow Bridge 复审
**brain_node.py：**
- 支持 `--paper`：✅ 是
- 不 bind ZMQ：✅ 是
- 不调用 `socket.send_string`：✅ 是
- 只追加写 `paper_signal_log.jsonl`：✅ 是 (`_append_paper_signal`)
- 非 Paper Mode 原有 ZMQ 不变：✅ 是
- 不实例化 `MockBrokerAdapter` / 不写 `paper_portfolio.json`：✅ 是

**paper_trade_engine.py：**
- tail `.jsonl` / 记录 offset：✅ 是 (`last_position` 轮询)
- 校验必填字段 / 动作过滤 (跳过 GRID)：✅ 是
- 调用 `place_order` / `get_order_status`：✅ 是
- 映射 schema (`quantity` / `avg_cost`)：✅ 是
- 显式 `save_portfolio(..., PORTFOLIO_FILE)`：✅ 是
- 不污染 `live_portfolio.json`：✅ 是
- 异常不中断：✅ 是 (`try/except` 包裹 `for line in lines` 单次解析)

**文件污染测试**：✅ 已执行测试验证，`live_portfolio.json` 和 `portfolio.json` 保持不变。
- **结论：通过**
