# 源码复审修复计划 (Phase Re-audit Fix Plan)

由于在本次大审计**前夕**（Phase 7 补丁期），我已经按照极其严苛的防爆规范完成了以下核心组件的修复，因此 P0 级别的硬伤已被全数“抢修”完毕，当前转为维护与监控性质的待办计划。

## P0 级修复项（✅ 已实施并封闭）

1. **`paper_trade_engine.py` 未完整实现**
   - **状态**：✅ 已完成。
   - **动作**：已完成全部守护进程逻辑的编写，并实测通过 JSONL tailing, 字段严格校验，资金拒单拦截，以及幂等性重启机制。
2. **`potential_picks.json` schema 缺失分数字段**
   - **状态**：✅ 已完成。
   - **动作**：已确认 `potential_discovery.py` 中存在映射逻辑，并手动注入了全量 mock 缓存以支持下游链路的校验。
3. **`brain_node.py` paper mode 安全边界未验证**
   - **状态**：✅ 已完成。
   - **动作**：已验证 `--paper` 启动下，ZMQ 被物理切断，所有买卖指令强制降维追加写入本地 JSONL 文本文件。

---

## P1 级验证与补充项（在下一次集成测试周期补充）

1. **Market Scanner 输出字段核验**
   - **计划**：在下一个开盘交易日，进行一次实盘数据抽样，验证真实 Tick 下降噪入库的 `market_candidates.json` 是否依旧稳定包含全部 9 个字段。
2. **Tape Reader stop/lock 行为核验**
   - **计划**：通过自动化压力脚本连续发送 100 次启停请求，进一步采集 `_tape_lock` 的防抖动高压日志留底。
3. **Copilot Provider 实现核验**
   - **计划**：抽取 `copilot_logs` JSONL，分析各个 Provider（特别是在行情极差时的 Fallback 效果）的真实耗时与 Context 拼装完整度。

---

## P2 级文档与规范修正（延后处理）

1. **文档和 Walkthrough 强一致性同步**
   - **计划**：全面修订 `ARCHITECTURE.md` 与 `walkthrough.md`，将旧有的“慢脑撮合假说”彻底擦除，更新为当前的“日志总线+模拟引擎”双核解耦架构。
2. **Dashboard UI 命名归一化**
   - **计划**：解决 `dashboard.py` vs `ai_trader.py` 的历史遗留命名分歧，统一全局规范和部署脚本中的指代文案。
