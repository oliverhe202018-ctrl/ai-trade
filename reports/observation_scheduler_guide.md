# 72 小时并行观察：Windows 任务计划程序指南

在接下来的 72 小时观察期内，我们需要每 5 分钟自动化执行一次审查统计。你可以使用 Windows 自带的“任务计划程序 (Task Scheduler)”来实现无人值守的高频执行。

## 1. 明确禁止的系统级操作
在整个 72 小时的观察周期内，**严格禁止任何突破以下防线的行为**：
- ❌ 修改 `brain_node.py` 以引入新的行情计算或信号。
- ❌ 修改 `live_trader.py` 的开平仓逻辑。
- ❌ 篡改或影响 `TradingState` 的交易状态机流转。
- ❌ 接入 AI 模型对资讯源进行情绪打分（Sentiment/Confidence 评估必须在以后才做）。
- ❌ 接入真实资金环境执行自动下单指令。

## 2. 如何设置每 5 分钟自动巡检

1. **打开任务计划程序**
   按键盘 `Win` 键，输入“任务计划程序” (或 Task Scheduler)，按回车打开。

2. **创建基本任务**
   - 点击右侧操作栏中的 **“创建任务...”** (注意：不是创建基本任务，以获取更高级设置)。
   - **【常规】选项卡**：
     - 名称：`AITrader_72H_Observer`
     - 勾选“不管用户是否登录都要运行”。
     - 勾选“使用最高权限运行”。
   
3. **设置触发器**
   - 切换到 **【触发器】选项卡**，点击“新建...”。
   - 选择 **“按计划”**，开始时间设为当前时间。
   - 勾选 **“重复任务间隔”**：选择或手动输入 `5 分钟`。
   - 持续时间选择 **“无限期”**。
   - 点击“确定”。

4. **设置操作**
   - 切换到 **【操作】选项卡**，点击“新建...”。
   - 操作：选择 **“启动程序”**。
   - 程序或脚本：填写您的 Python 解释器绝对路径（例如：`C:\Users\a2515\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe` 或简单填写 `python` 若已配置好全系统环境变量）。
   - 添加参数：填写 `scripts/run_72h_observation.py`。
   - 起始于：必须填写项目根目录的绝对路径，即 `C:\Users\a2515\ai-trader`。
   - 点击“确定”。

5. **高级设置（可选但推荐）**
   - 切换到 **【设置】选项卡**，勾选“如果任务失败，按以下频率重新启动”，设置为 1 分钟，最多重试 3 次。
   - 点击“确定”，输入 Windows 管理员密码以保存计划。

## 3. 如何追踪观察报告

在上述任务自动执行期间，你可以通过 IDE 或 Markdown 阅读器定期打开并查看以下三大报告文件（它们会自动被脚本刷新）：

- 📖 **[observation_72h_status.md](file:///C:/Users/a2515/ai-trader/reports/observation_72h_status.md)**
  主要看 `7. 当前结论` 是否为 `RUNNING`。一旦脚本探测到违规关键字注入，或抛出未隔离的致命错误，将会被标记为 `FAILED`。

- 📖 **[paper_trading_observation.md](file:///C:/Users/a2515/ai-trader/reports/paper_trading_observation.md)**
  这是行情引擎监控。关注是否有因数据异常 (STALE/DOWN) 导致 Trader 被迫拒单或者进入 FROZEN 冰冻期。

- 📖 **[news_readonly_observation.md](file:///C:/Users/a2515/ai-trader/reports/news_readonly_observation.md)**
  这是旁路资讯监控。关注 SQLite `news_events.db` 内事件的增长趋势以及重名的阻断率，观察抓取行为是否触发了反爬限流。
