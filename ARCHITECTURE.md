



\### Relevant Code Snippets

1\. c:/Users/a2515/ai-trader/README.md:L1-L50

&#x20; — README文件的前50行，介绍了项目的主要功能和使用说明。

2\. c:/Users/a2515/ai-trader/config.yaml:L1-L30

&#x20; — 配置文件内容，包含项目的设置选项和参数。

3\. c:/Users/a2515/ai-trader/ai\_trader.py:L1-L50

&#x20; — 主程序入口文件，展示了系统的整体架构和核心逻辑。

4\. c:/Users/a2515/ai-trader/broker.py:L1-L50

&#x20; — 交易模块文件，包含了与证券公司交互的接口实现。

5\. c:/Users/a2515/ai-trader/market\_data.py:L1-L50

&#x20; — 市场数据模块，负责获取和处理金融数据。

6\. c:/Users/a2515/ai-trader/trade\_engine.py:L1-L50

&#x20; — 交易引擎模块，负责执行买卖指令并管理交易流程。

7\. c:/Users/a2515/ai-trader/strategy\_engine.py:L1-L50

&#x20; — 策略引擎模块，实现了交易策略的设计与执行逻辑。

8\. c:/Users/a2515/ai-trader/stock\_picker.py:L1-L50

&#x20; — 股票选择器模块，用于筛选候选股票池。

9\. c:/Users/a2515/ai-trader/llama\_manager.py:L1-L50

&#x20; — Llama模型管理模块，负责大语言模型的加载与调用。

10\. c:/Users/a2515/ai-trader/llama\_service\_monitor.py:L1-L50

&#x20; — Llama服务监控模块，用于监控大语言模型服务的状态。

11\. c:/Users/a2515/ai-trader/check\_easytrader.py:L1-L50

&#x20; — Easytrader校验脚本，用于验证交易接口的可用性。

12\. c:/Users/a2515/ai-trader/test\_llama\_manager.py:L1-L50

&#x20; — 对Llama管理器的测试脚本，包含单元测试用例。

13\. c:/Users/a2515/ai-trader/requirements.txt:L1-L30

&#x20; — 项目依赖库列表，显示了所需Python包。

14\. c:/Users/a2515/ai-trader/INSTRUCTIONS.md:L1-L50

&#x20; — 指令说明文档，提供系统使用指南。

15\. c:/Users/a2515/ai-trader/LLAMA\_SERVER\_OPTIMIZATION\_SUMMARY.md:L1-L50

&#x20; — Llama服务器优化摘要，解释了模型服务的性能优化方法。



Let me read all the core source files in parallel.





Now let me read the key files to understand their responsibilities in detail.



Now I have a comprehensive understanding of the entire codebase. Let me compile the architecture snapshot report.

\# 项目当前架构快照



\## 一、核心目录结构树



```

c:\\Users\\a2515\\ai-trader\\

├── ai\_trader.py              # 主程序入口

├── broker.py                 # 券商适配器层

├── strategy\_engine.py        # 策略引擎

├── trade\_engine.py           # 交易执行引擎

├── market\_data.py            # 实时行情数据

├── stock\_picker.py           # 选股器 (简易封装)

├── llama\_manager.py          # LLaMA 服务管理器

├── llama\_service\_monitor.py  # LLaMA 服务监控器

├── check\_easytrader.py       # 券商客户端检测脚本

├── test\_llama\_manager.py     # LLaMA 管理器测试

├── config.yaml               # 系统配置

├── requirements.txt          # 依赖清单

└── portfolio.json            # 运行时状态文件 (运行时生成)

```



\## 二、核心模块职责定义



| 模块 | 功能总结 |

|---|---|

| \[ai\_trader.py](file:///c:/Users/a2515/ai-trader/ai\_trader.py) | 主调度入口：串联"连接券商→获取持仓→拉取行情→策略决策→执行买卖→输出报告"全流程 |

| \[broker.py](file:///c:/Users/a2515/ai-trader/broker.py) | 券商适配器层：统一接口抽象，支持 `mock / easytruser(同花顺) / tdx(通达信) / qmt(迅投)` 四种模式无缝切换，含 YAML 简易解析器 |

| \[strategy\_engine.py](file:///c:/Users/a2515/ai-trader/strategy\_engine.py) | 多策略选股引擎：实现 trend(趋势跟踪)、value(价值投资)、momentum(动量) 三种策略，以及止损/止盈卖出信号 |

| \[trade\_engine.py](file:///c:/Users/a2515/ai-trader/trade\_engine.py) | 交易执行与状态管理：原子化持久化(文件锁+os.replace)、结构化 JSONL 日志、收盘结算、风控熔断 |

| \[market\_data.py](file:///c:/c/Users/a2515/ai-trader/market\_data.py) | 实时行情数据源：腾讯行情接口为主，失败降级为模拟数据，内置 20 只标的 watchlist |

| \[stock\_picker.py](file:///c:/Users/a2515/ai-trader/stock\_picker.py) | 选股器：仅 15 行封装，直接调用 market\_data 输出 JSON |

| \[llama\_manager.py](file:///c:/Users/a2515/ai-trader/llama\_manager.py) | LLaMA 服务管理器：异步管理 llama-server 进程，含请求排队、并发控制、自动重启、指标记录 |

| \[llama\_service\_monitor.py](file:///c:/Users/a2515/ai-trader/llama\_service\_monitor.py) | LLaMA 服务监控器：与 llama\_manager 功能重叠，额外支持 Windows 服务模式 |

| \[config.yaml](file:///c:/Users/a2515/ai-trader/config.yaml) | 全局配置中心：broker 模式、策略参数、风控阈值、模拟盘费率、实盘参数、通知 webhook 等 |



\## 三、数据流向闭环



```

行情源 (腾讯/新浪 API)

&#x20;   │

&#x20;   ▼

\[market\_data.py] get\_realtime\_quotes()

&#x20;   │

&#x20;   ├─ 成功 → 返回实时行情 list\[dict] (含 price, change\_pct, sector, limit\_up/down)

&#x20;   └─ 失败 → 降级 get\_mock\_quotes() 模拟数据

&#x20;   │

&#x20;   ▼

\[ai\_trader.py] ai\_decide()

&#x20;   │

&#x20;   ├── 获取持仓 → broker.get\_accounts() / load\_state()

&#x20;   │

&#x20;   ▼

\[strategy\_engine.py] select\_stocks()

&#x20;   │   根据 config.strategy.type 分支:

&#x20;   │   trend → 按涨幅排序取中间段

&#x20;   │   value → 按涨幅从低到高取低估标的

&#x20;   │   momentum → 按涨幅从高到低追强势股

&#x20;   │   输出: {"buys": \[...], "sells": \[...]}

&#x20;   │

&#x20;   ▼

\[strategy\_engine.py] generate\_sell\_signals()

&#x20;   │   止损/止盈判断 (config.risk.stop\_loss\_pct / take\_profit\_pct)

&#x20;   │

&#x20;   ▼

\[ai\_trader.py] broker.buy() / broker.sell()

&#x20;   │

&#x20;   ├── MockBroker → 更新 portfolio.json 内存状态 → 原子写入磁盘

&#x20;   │

&#x20;   ├── EasyTraderBroker → easytruser GUI 自动化下单

&#x20;   │

&#x20;   └── QMTBroker → xtquant API 下单

&#x20;   │

&#x20;   ▼

\[trade\_engine.py] log\_trade() → logs/json\_YYYYMMDD.jsonl (结构化日志)

```



\## 四、当前已完成进度与缺失短板



\### ✅ 已完成



\- \*\*券商适配器架构\*\*：BrokerBase 抽象类 + 4 种实现 (mock/easytruser/tdx/qmt)，工厂模式切换

\- \*\*三套策略引擎\*\*：trend / value / momentum，含行业分散、仓位上限、单板块限制

\- \*\*风控系统\*\*：止损/止盈、日回撤熔断、持仓天数上限

\- \*\*状态持久化\*\*：文件锁保护 + 原子写入 (os.replace)，脏数据容错

\- \*\*结构化日志\*\*：JSONL 格式记录每笔交易 (action/code/price/quantity/strategy/reason)

\- \*\*收盘结算\*\*：daily\_settlement() 计算实盈/浮盈/总资产

\- \*\*实时行情\*\*：腾讯接口 + 模拟降级，涨跌停检测

\- \*\*配置系统\*\*：config.yaml 覆盖策略/风控/费率/通知



\### ⚠️ 缺失短板



1\. \*\*无回测模块\*\*：无法对策略在历史数据上验证，直接实盘/模拟盘跑

2\. \*\*LLaMA 未真正接入交易决策\*\*：llama\_manager.py 和 llama\_service\_monitor.py 是独立服务，仅做模型推理管理，未与 ai\_trader.py 策略链路打通，当前策略是纯规则驱动

3\. \*\*行情数据源单一\*\*：仅腾讯/新浪接口，无 K 线/分钟线历史数据，无 ATR/均线等技术指标计算

4\. \*\*策略逻辑较简单\*\*：当前策略仅基于涨幅过滤排序，无技术指标(如 MACD/RSI/布林带)，无基本面因子

5\. \*\*无通知渠道实现\*\*：config.yaml 中 webhook/telegram 配置为空，无实际推送逻辑

6\. \*\*llama\_manager.py 有语法错误\*\*：第 111 行 `subprocess.CREATE\_NO要经常` 是无效标识符

7\. \*\*llama\_service\_monitor.py 有语法错误\*\*：第 116 行 `subprocess.CREATE\_NO\_DUPLICATE` 是无效标识符，且两个模块功能大量重叠

8\. \*\*缺少异常处理深度\*\*：market\_data.py 中行情失败静默降级为模拟数据，可能误导实盘决策

9\. \*\*无数据库\*\*：所有状态存储在 portfolio.json，无查询/聚合/分析能力

10\. \*\*无调度器\*\*：当前无 cron/timer 自动轮询执行，需手动运行 `python ai\_trader.py`

