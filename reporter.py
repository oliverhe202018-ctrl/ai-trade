"""
日报生成器 (Reporter)
聚合资产、交易、AI打分数据，生成 Markdown 格式日报
"""
import os
import json
import re
from datetime import datetime
from collections import defaultdict

# 路径常量
CACHE_DIR = "data_cache"
LOGS_DIR = "logs"


def generate_daily_report():
    """
    生成当日日报：
    1. 读取 live_portfolio.json（总资产、现金、持仓）
    2. 读取 logs/trades_YYYYMMDD.log（买卖记录、路由决策、退出原因）
    3. 读取 ai_scores_cache.json（打分记录）- 仅提取当日数据
    4. 组装 Markdown 并保存至 data_cache/daily_report_YYYYMMDD.md
    """
    # 1. 日期强校验
    today_str = datetime.now().strftime("%Y%m%d")
    today_date = datetime.now().strftime("%Y-%m-%d")
    
    # 2. 读取资产快照
    portfolio_path = os.path.join(CACHE_DIR, "live_portfolio.json")
    portfolio_data = _load_json(portfolio_path)
    
    # 3. 读取当日交易日志（文件不存在时输出提示）
    trade_log_path = os.path.join(LOGS_DIR, f"trades_{today_str}.log")
    if not os.path.exists(trade_log_path):
        print(f"[日报] 当日无交易记录: {trade_log_path}")
        trades = {"buys": [], "sells": [], "routes": {}, "exit_reasons": [], "no_trades": True}
    else:
        trades = _parse_trade_log(trade_log_path)
    
    # 4. 读取 AI 打分缓存 - 仅提取当日数据
    ai_scores_path = os.path.join(CACHE_DIR, "ai_scores_cache.json")
    ai_scores_raw = _load_json(ai_scores_path)
    ai_scores = _filter_today_ai_scores(ai_scores_raw, today_date)
    
    # 5. 组装 Markdown
    report = _build_markdown(today_date, portfolio_data, trades, ai_scores)
    
    # 6. 保存日报
    report_path = os.path.join(CACHE_DIR, f"daily_report_{today_str}.md")
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    
    print(f"[日报] 已生成: {report_path}")
    return report_path


def _filter_today_ai_scores(ai_scores_raw, today_date):
    """
    过滤 AI 打分缓存，仅保留当日数据。
    键名格式: YYYY-MM-DD_code 或 YYYY-MM-DD
    """
    if not ai_scores_raw:
        return {}
    
    filtered = {}
    prefix = today_date  # YYYY-MM-DD
    
    if isinstance(ai_scores_raw, dict):
        for key, value in ai_scores_raw.items():
            # 键名以当日日期开头
            if key.startswith(prefix):
                filtered[key] = value
    elif isinstance(ai_scores_raw, list):
        for item in ai_scores_raw:
            # 检查 update_time 或 timestamp 字段
            update_time = item.get("update_time", item.get("timestamp", ""))
            if update_time.startswith(prefix):
                code = item.get("code", "unknown")
                filtered[code] = item
    
    return filtered


def _load_json(filepath):
    """安全加载 JSON 文件"""
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _parse_trade_log(filepath):
    """
    解析交易日志，提取：
    - 买卖记录
    - 路由决策（网格/定投/趋势）
    - 退出原因
    """
    if not os.path.exists(filepath):
        return {"buys": [], "sells": [], "routes": defaultdict(int), "exit_reasons": []}
    
    buys = []
    sells = []
    routes = defaultdict(int)
    exit_reasons = []
    
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            # 提取买卖记录
            if "[买入]" in line or "[BUY]" in line:
                buys.append(line)
                # 提取路由策略
                if "网格买入" in line:
                    routes["grid"] += 1
                elif "DCA" in line or "定投" in line:
                    routes["smart_dca"] += 1
                elif "趋势" in line or "信号" in line:
                    routes["trend"] += 1
            
            elif "[卖出]" in line or "[SELL]" in line:
                sells.append(line)
                # 提取退出原因
                reason_match = re.search(r"reason[=:]\s*([^|\n]+)", line, re.IGNORECASE)
                if reason_match:
                    exit_reasons.append(reason_match.group(1).strip())
    
    return {
        "buys": buys,
        "sells": sells,
        "routes": dict(routes),
        "exit_reasons": exit_reasons,
    }


def _build_markdown(today_date, portfolio, trades, ai_scores):
    """组装 Markdown 格式日报"""
    lines = []
    
    # 标题
    lines.append(f"# 实盘日报 - {today_date}")
    lines.append("")
    
    # 资产概览
    lines.append("## 资产概览")
    lines.append("")
    
    cash = portfolio.get("cash", 0)
    positions = portfolio.get("positions", {})
    
    # 计算总市值
    market_value = 0
    for code, pos in positions.items():
        shares = pos.get("shares", 0)
        avg_price = pos.get("avg_price", 0)
        market_value += shares * avg_price
    
    total_equity = cash + market_value
    
    lines.append(f"- **总资产**: ¥{total_equity:,.2f}")
    lines.append(f"- **现金**: ¥{cash:,.2f}")
    lines.append(f"- **持仓市值**: ¥{market_value:,.2f}")
    lines.append(f"- **持仓数量**: {len(positions)} 只")
    lines.append("")
    
    # 持仓明细
    if positions:
        lines.append("### 持仓明细")
        lines.append("")
        lines.append("| 代码 | 名称 | 股数 | 成本价 | 持仓天数 |")
        lines.append("|------|------|------|--------|----------|")
        for code, pos in positions.items():
            name = pos.get("name", code)
            shares = pos.get("shares", 0)
            avg_price = pos.get("avg_price", 0)
            holding_days = pos.get("holding_days", 0)
            lines.append(f"| {code} | {name} | {shares} | ¥{avg_price:.2f} | {holding_days} |")
        lines.append("")
    
    # 交易记录
    lines.append("## 交易记录")
    lines.append("")
    
    buys = trades.get("buys", [])
    sells = trades.get("sells", [])
    
    lines.append(f"- **买入笔数**: {len(buys)}")
    lines.append(f"- **卖出笔数**: {len(sells)}")
    lines.append("")
    
    if buys:
        lines.append("### 买入记录")
        lines.append("```")
        for buy in buys:
            lines.append(buy)
        lines.append("```")
        lines.append("")
    
    if sells:
        lines.append("### 卖出记录")
        lines.append("```")
        for sell in sells:
            lines.append(sell)
        lines.append("```")
        lines.append("")
    
    # 路由决策
    routes = trades.get("routes", {})
    if routes:
        lines.append("### 策略路由分布")
        lines.append("")
        lines.append("| 策略 | 笔数 |")
        lines.append("|------|------|")
        for strategy, count in routes.items():
            strategy_name = {"grid": "网格", "smart_dca": "智能定投", "trend": "趋势"}.get(strategy, strategy)
            lines.append(f"| {strategy_name} | {count} |")
        lines.append("")
    
    # 退出原因
    exit_reasons = trades.get("exit_reasons", [])
    if exit_reasons:
        lines.append("### 退出原因统计")
        lines.append("```")
        for reason in set(exit_reasons):
            count = exit_reasons.count(reason)
            lines.append(f"{reason}: {count} 次")
        lines.append("```")
        lines.append("")
    
    # AI 打分记录
    if ai_scores:
        lines.append("## AI 打分记录")
        lines.append("")
        lines.append("| 代码 | 名称 | 得分 | 更新时间 |")
        lines.append("|------|------|------|----------|")
        
        # ai_scores 可能是 dict 或 list
        if isinstance(ai_scores, dict):
            for code, data in ai_scores.items():
                if isinstance(data, dict):
                    name = data.get("name", code)
                    score = data.get("score", "N/A")
                    update_time = data.get("update_time", "N/A")
                    lines.append(f"| {code} | {name} | {score} | {update_time} |")
                else:
                    lines.append(f"| {code} | - | {data} | - |")
        elif isinstance(ai_scores, list):
            for item in ai_scores:
                code = item.get("code", "N/A")
                name = item.get("name", code)
                score = item.get("score", "N/A")
                update_time = item.get("update_time", "N/A")
                lines.append(f"| {code} | {name} | {score} | {update_time} |")
        
        lines.append("")
    
    # 页脚
    lines.append("---")
    lines.append(f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    
    return "\n".join(lines)


if __name__ == "__main__":
    generate_daily_report()
