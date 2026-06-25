# AI Trader 技术债务与后续优化池 (Tech Debt & Optimization)

本系统在历时六个阶段的重构中，成功确立了由 Market Scanner、Potential Discovery、Tape Reader 与 AI Copilot 组成的坚实基座。为了保证上线节点的快速交付，我们在某些局部设计上采取了“降维”与“折中”方案。

以下是遗留的**技术债务与未来优化项清单**，供后续版本迭代参考：

## 1. 资金追踪指标精细化
- [ ] **主力资金阈值动态化**:
  目前 `LARGE_ORDER_AMOUNT_THRESHOLD` 固定为硬编码的 1,000,000。后续应升级为动态阈值算法，可采取以下路线：
  - 基于该标的过去 N 天的成交额分位数（如 90th percentile）计算大单门槛。
  - 针对大盘权重股和小盘微缩股，实施流通市值与价格区间的区分挂钩。
- [ ] **引入 Tracking Confidence 指标**:
  增加盘口追踪的置信度评分机制，引入如 `tracking_duration_seconds`（追踪总时长）、`valid_sample_ratio`（有效快照占比）、`last_gap_seconds`（最近断层秒数）。以此来科学区分出“跟踪了1小时的可靠数据”和“刚刚跟踪3分钟的短样本”。
- [ ] **L2 行情适配预留**:
  虽然我们现阶段基于 L1 快照完美实现了 `main_money_proxy` 差分算法，但架构应预留 Level 2 接口（`get_transaction_data`, `get_order_queue_data`）钩子。一旦未来获取 L2 订阅授权，可瞬间插拔替换，将 `estimated` 升格为 `precise`。

## 2. 量化模型与漏斗策略迭代
- [ ] **Fusion Score 权重回测**:
  `FusionEngine` 中的各因子（资金面30%、趋势面25%、消息面25%、异动20%）的默认权重组合缺乏中长期大样本数据的验证。需建立离线回测环境对这组参数进行寻优调参。
- [ ] **Potential Discovery Top30 策略优化**:
  在 Top 100 削减到 Top 30 的降噪过程中，目前采用了粗暴的按分数线性截断（Slice）。后续应当加入“基本面过滤器”或“异常波动筛除器”（如剔除当日振幅超 15% 的高危票）以提升喂给 LLM 的“样本水准”。

## 3. 工程化与系统运维升级
- [ ] **日志归档与清理策略**:
  系统在每次触发 `Market Scanner` 和 `Potential Discovery` 时，均会持久化大量的 json 日志包至 `data_cache/..._runs/`。为了防止长期运行造成的磁盘撑爆，需要增加异步守护进程执行自动化压缩或定期清理（如仅保留最近 7 天的记录）。
- [ ] **Dashboard 模块拆分**:
  `core/dashboard.py` 单文件已经膨胀到了惊人的行数（2000+）。不仅维护起来具有较大的认知负荷，组件复用率也较低。应当引入按业务解耦的文件夹（如 `pages/scanner.py`, `pages/tape.py`），通过 Streamlit 原生多页面支持或组件化提取来进行工程重构。
