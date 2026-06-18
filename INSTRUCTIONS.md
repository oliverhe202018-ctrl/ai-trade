# AI 炒股系统 - 使用说明

## 快速启动

### 模拟盘（默认）
```bash
cd ~/ai-trader
python ai_trader.py --mode mock
```

### 同花顺真实盘
```bash
python ai_trader.py --mode easytrader
# 需先安装: pip install easytrader
# 需先运行同花顺客户端
```

### 通达信真实盘
```bash
python ai_trader.py --mode tdx
# 需先安装: pip install easytrader
# 需先运行通达信客户端
```

### 迅投 QMT 真实盘
```bash
python ai_trader.py --mode qmt
# 需先安装: pip install xtquant
# 需先运行 QMT 客户端
```

## 文件说明

```
~/ai-trader/
├── ai_trader.py           # AI 自主交易主程序 (v2)
├── trade_engine.py        # 交易执行引擎
├── stock_picker.py        # 选股器 (实时行情)
├── market_data.py         # 实时行情数据源 (腾讯接口)
├── broker.py              # 券商适配器层 (模拟/真实)
├── config.yaml            # 配置文件
├── portfolio.json         # 账户状态 (自动更新)
└── INSTRUCTIONS.md        # 本文件
```

## 配置说明 (config.yaml)

| 参数 | 说明 | 默认值 |
|------|------|--------|
| broker_mode | 交易模式 | mock |
| initial_capital | 启动资金 | 50000 |
| daily_target | 日目标利润 | 50 |
| max_position_pct | 单笔最大仓位 | 0.15 |
| strategy.min_gain_pct | 最低涨幅阈值 | 0% |
| strategy.max_gain_pct | 最高涨幅阈值 | 5% |
| strategy.exclude_sectors | 排除行业 | ["白酒"] |
| sell.stop_loss_pct | 止损线 | -5% |
| sell.take_profit_pct | 止盈线 | +10% |

## 切换真实券商步骤

### 方式1: 同花顺 (推荐新手)
1. `pip install easytrader`
2. 安装同花顺客户端
3. 登录交易账户
4. `python ai_trader.py --mode easytrader`

### 方式2: 通达信
1. `pip install easytrader`
2. 安装通达信客户端
3. 登录交易账户
4. `python ai_trader.py --mode tdx`

### 方式3: 迅投 QMT (推荐量化)
1. 安装 QMT 客户端 (推荐国金/华泰等支持 MiniQMT 的券商)
2. `pip install xtquant`
3. 配置 config.yaml:
   ```yaml
   broker_mode: qmt
   ```
4. `python ai_trader.py --mode qmt`

## 查看状态
```bash
cd ~/ai-trader
python trade_engine.py status
```

## 手动交易
```bash
# 买入
python trade_engine.py buy sh600036 招商银行 39.37 100 "银行板块"

# 卖出
python trade_engine.py sell sh600036 招商银行 40.00 100 "止盈"

# 查看状态
python trade_engine.py status
```

## 重置账户
```bash
echo '{"cash":50000,"position":{},"history":[],"start_date":"'"$(date +%Y-%m-%d)"'","trading_count":0}' > portfolio.json
```
