"""
AI 自主交易主程序 v3 - 使用 Broker 适配器层 + 多策略引擎
支持模拟盘/真实盘无缝切换，支持 trend/value/momentum 三种策略

使用方法:
  python ai_trader.py --mode mock           # 模拟盘
  python ai_trader.py --mode mock --strategy trend  # 指定策略
  python ai_trader.py --mode easytrader     # 同花顺真实盘
  python ai_trader.py --mode tdx            # 通达信真实盘
  python ai_trader.py --mode qmt            # QMT 真实盘
"""
import json
import subprocess
import sys
import os
import argparse
from datetime import datetime

# 将项目根目录加入 sys.path，确保 feeds 等模块可被引用
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from core.logger_config import logger
from core.broker import get_broker, load_config
from feeds.market_data import get_realtime_quotes
from core.strategy_engine import select_stocks, generate_sell_signals


def run_cmd(cmd, timeout=60):
    """运行命令并返回输出"""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, encoding='utf-8')
    return result.stdout, result.stderr, result.returncode


def ai_decide(mode="mock", strategy_override=None):
    """AI 自主决策并执行交易"""
    config = load_config()
    
    logger.info("=" * 70)
    logger.info("🤖 AI 交易 Agent v3 - 启动")
    logger.info(f"   交易模式: {mode}")
    logger.info(f"   启动资金: ¥{config.get('initial_capital', 50000):,.0f}")
    logger.info(f"   日目标利润: ¥{config.get('daily_target', 50)}")
    logger.info(f"   策略: {strategy_override or config.get('strategy', {}).get('type', 'trend')}")
    logger.info("=" * 70)

    # 连接券商
    logger.info("\n[步骤0] 连接券商...")
    try:
        # 优先用参数，否则从 config.yaml 读取
        effective_mode = mode if mode != "mock" else config.get("broker_mode", "mock")
        broker = get_broker(effective_mode)
        connect_result = broker.connect()
        if connect_result.get("status") == "error":
            logger.error(f"  ❌ 券商连接失败: {connect_result.get('msg')}")
            logger.info(f"  → 回退到模拟盘")
            broker = get_broker("mock")
            broker.connect()
        else:
            logger.info(f"  ✅ 已连接: {connect_result.get('broker')}")
            if connect_result.get("path"):
                logger.info(f"  📁 路径: {connect_result['path']}")
    except Exception as e:
        logger.exception(f"  ⚠️ 券商初始化失败: {e}，回退到模拟盘")
        broker = get_broker("mock")
        broker.connect()

    # 获取持仓
    logger.info("\n[步骤1] 获取当前持仓...")
    accounts = broker.get_accounts()
    positions = {}
    
    if isinstance(accounts, list) and len(accounts) > 0:
        if isinstance(accounts[0], dict) and 'account_id' in accounts[0]:
            # Mock broker - 用 trade_engine 加载
            pass
        else:
            # Real broker
            for acct in accounts:
                if isinstance(acct, dict) and '证券代码' in acct:
                    positions[acct.get('证券代码', '')] = acct
    
    if not positions:
        try:
            from trade_engine import load_state
            state = load_state()
            for code, pos in state.get("position", {}).items():
                positions[code] = pos
        except:
            pass
    
    logger.info(f"  当前持仓: {len(positions)} 只")
    if positions:
        for code in positions:
            pos_info = broker.get_position(code)
            if pos_info:
                logger.info(f"    {code}: {pos_info.get('shares', '?')}股")
            else:
                logger.info(f"    {code}: ?")

    # 获取实时行情
    logger.info("\n[步骤2] 获取实时行情...")
    try:
        quotes = get_realtime_quotes()
        logger.info(f"  ✅ 获取 {len(quotes)} 只股票行情")
    except Exception as e:
        logger.exception(f"  ❌ 行情获取失败: {e}")
        quotes = []

    # 展示行情概览
    if quotes:
        logger.info(f"\n📊 市场概览:")
        sorted_quotes = sorted(quotes, key=lambda x: x.get('change_pct', 0), reverse=True)
        for i, q in enumerate(sorted_quotes[:10], 1):
            sign = "+" if q.get('change_pct', 0) > 0 else ""
            flag = ""
            if q.get('limit_up'):
                flag = "🔴涨停"
            elif q.get('limit_down'):
                flag = "🟢跌停"
            logger.info(f"  {i:2d}. {q['name']:10s} ¥{q['price']:>8.2f}  {sign}{q.get('change_pct', 0):.2f}%  [{q.get('sector', '')}] {flag}")
        logger.info(f"\n  ... (共 {len(quotes)} 只)")

    # AI 决策
    logger.info("\n[步骤3] AI 分析决策中...")
    
    # 获取策略类型
    strategy_type = strategy_override or config.get("strategy", {}).get("type", "trend")
    logger.info(f"  使用策略: {strategy_type}")
    
    # 使用策略引擎
    stock_decisions = select_stocks(quotes, positions, config, mode)
    
    # 生成卖出信号
    sell_signals = generate_sell_signals(positions, quotes, config)
    
    # 合并交易
    trades = stock_decisions.get("buys", [])
    sell_trades = sell_signals
    
    # 展示买入
    if trades:
        logger.info(f"\n🛒 AI 决定买入 {len(trades)} 只股票:")
        for t in trades:
            logger.info(f"   📥 {t['name']}({t['code']}) ¥{t['price']:.2f} x {t['shares']}股")
            logger.info(f"      理由: {t['reason']}")
    else:
        logger.info("\n🤷 AI 认为当前市场没有合适的买入机会")
    
    # 展示卖出
    if sell_trades:
        logger.info(f"\n💰 AI 决定卖出 {len(sell_trades)} 只股票:")
        for t in sell_trades:
            logger.info(f"   📤 {t['name']}({t['code']}) ¥{t['price']:.2f} x {t['shares']}股")
            logger.info(f"      理由: {t['reason']}")
    
    # 执行交易
    logger.info(f"\n[步骤4] 执行交易...")
    
    for t in sell_trades:
        result = broker.sell(t['code'], t['price'], t['shares'])
        logger.info(f"  卖出: {json.dumps(result, ensure_ascii=False, indent=2)}")
    
    for t in trades:
        result = broker.buy(t['code'], t['price'], t['shares'])
        logger.info(f"  买入: {json.dumps(result, ensure_ascii=False, indent=2)}")

    # 最终报告
    logger.info(f"\n[步骤5] 最终账户状态:")
    final_accounts = broker.get_accounts()
    if final_accounts:
        logger.info(json.dumps(final_accounts, ensure_ascii=False, indent=2))

    # 交易日志
    logger.info("\n" + "=" * 70)
    logger.info("📋 交易日志")
    logger.info("=" * 70)
    if trades:
        for t in trades:
            logger.info(f"  ✅ 买入 {t['name']}({t['code']}) @ ¥{t['price']:.2f} x {t['shares']}股")
    if sell_trades:
        for t in sell_trades:
            logger.info(f"  💸 卖出 {t['name']}({t['code']}) @ ¥{t['price']:.2f} x {t['shares']}股")
    if not trades and not sell_trades:
        logger.info("  ⏸️ 今日无交易操作")
    logger.info("=" * 70)

    return {
        "buy_trades": trades,
        "sell_trades": sell_trades,
        "accounts": final_accounts,
        "strategy": strategy_type,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI 自主交易 v3 - 多策略")
    parser.add_argument("--mode", default="mock", choices=["mock", "easytrader", "tdx", "qmt"],
                       help="交易模式 (default: mock)")
    parser.add_argument("--strategy", choices=["trend", "value", "momentum"],
                       help="覆盖配置中的策略类型")
    args = parser.parse_args()

    result = ai_decide(args.mode, args.strategy)
    logger.info(f"\n✅ 交易完成，模式: {args.mode}")
