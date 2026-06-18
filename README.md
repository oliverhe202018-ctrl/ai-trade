# AI 自主交易系统 v3

## 架构概览

```
ai_trader.py        ← 主入口：串联行情→决策→交易
broker.py           ← 券商适配器层 (Mock/EasyTrader/QMT)
market_data.py      ← 行情数据源 (腾讯/东方财富)
strategy_engine.py  ← 策略引擎 (trend/value/momentum)
trade_engine.py     ← 模拟盘交易核心
config.yaml         ← 统一配置中心
```

## 快速开始

### 模拟盘
```bash
python ai_trader.py --mode mock
python ai_trader.py --mode mock --strategy value
python ai_trader.py --mode mock --strategy momentum
```

### 真实盘
```bash
# 同花顺 EasyTrader
python ai_trader.py --mode easytrader

# 通达信
python ai_trader.py --mode tdx

# QMT
python ai_trader.py --mode qmt
```

## 配置说明 (config.yaml)

### broker_mode - 默认券商模式
- `mock`: 模拟盘（默认）
- `easytrader`: 同花顺/通达信
- `tdx`: 通达信专用
- `qmt`: 国信 QMT

### strategy - 策略配置
```yaml
strategy:
  type: trend          # 可选: trend | value | momentum
  safe_gain_range: [-3, 3]  # 涨幅过滤范围
  exclude_sectors: [白酒]   # 排除行业
  max_per_sector: 2    # 单行业最多持仓
```

### sell - 卖出规则
```yaml
sell:
  stop_loss_pct: -5    # 止损线 -5%
  take_profit_pct: 10  # 止盈线 +10%
  max_holding_days: 20 # 最大持仓天数
```

### live - 实盘交易参数
```yaml
live:
  pre_market: "09:15-09:25"
  morning: "09:30-11:30"
  afternoon: "13:00-15:00"
  post_market: "15:00-15:30"
  price_offset_pct: 0.05  # 委托价格偏移
```

## 策略说明

### trend (趋势跟踪) - 默认
- 筛选涨幅中位数附近的股票
- 避免追高，选择稳健上涨标的
- 适合震荡市和缓涨行情

### value (价值投资)
- 优先选择涨幅最小的股票
- 寻找低估机会
- 适合价值型和保守投资者

### momentum (动量策略)
- 选择涨幅最大的股票
- 追强势股
- 适合趋势行情和激进投资者

## 券商接入

### 自动检测
EasyTrader 和通达信会自动检测安装路径：
- EasyTrader: 默认 `~/.easytrader/user_config.json`
- 通达信: 默认 `C:\通达信`
- 同花顺: 默认 `C:\同花顺客户端`

### 手动指定
修改 config.yaml:
```yaml
easytrader_path: "C:\MyBroker\同花顺"
tdx_path: "C:\MyBroker\通达信"
```

## 运行日志
交易日志自动保存到 `logs/` 目录，保留 30 天。
