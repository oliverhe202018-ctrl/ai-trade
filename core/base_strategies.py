"""
基础量化策略规则库 - 根据 AI 赋能排序后的池子执行具体策略买入
"""


def execute_trend_strategy(candidates, config, current_positions, max_positions, available_cash):
    """
    趋势跟踪策略 - 绝对信任 AI 的评分排序并动态配资

    Args:
        candidates: AI 打分后排序的候选股票列表
        config: 系统配置字典
        current_positions: 当前持仓数量
        max_positions: 最大持仓数
        available_cash: 可用资金

    Returns:
        list of buy trade dicts
    """
    buys = []

    for q in candidates[:3]:
        if current_positions + len(buys) >= max_positions:
            break

        budget = available_cash * 0.30
        price = q["price"]
        shares = int(budget / price / 100) * 100

        if shares >= 100:
            sector_count = sum(1 for b in buys if b.get("sector") == q.get("sector"))
            if sector_count >= config.get("strategy", {}).get("max_per_sector", 2):
                continue

            ai_reason = q.get("ai_reason", "")
            base_reason = f"趋势跟踪 - {q.get('sector', '')}板块"

            buys.append(
                {
                    "code": q["code"],
                    "name": q["name"],
                    "price": price,
                    "shares": shares,
                    "reason": f"{base_reason} | AI: {ai_reason}" if ai_reason else base_reason,
                    "sector": q.get("sector", ""),
                }
            )
            available_cash -= price * shares

    return buys


def execute_value_strategy(candidates, config, current_positions, max_positions, available_cash):
    """
    价值投资策略 - 基于 AI 评分寻找低估标的

    后续可根据 PE/PB/ROE 等价值因子独立演进。
    当前作为趋势策略的兼容路由。
    """
    # 低涨幅排序 (找低估的)
    candidates_sorted = sorted(candidates, key=lambda x: x.get("change_pct", 0))
    return execute_trend_strategy(candidates_sorted, config, current_positions, max_positions, available_cash)


def execute_momentum_strategy(candidates, config, current_positions, max_positions, available_cash):
    """
    动量驱动策略 - 追强势股

    后续可结合 RSI/成交量放大等动量因子独立演进。
    当前按涨幅降序排列后执行买入。
    """
    # 高涨幅排序 (追强势)
    candidates_sorted = sorted(candidates, key=lambda x: x.get("change_pct", 0), reverse=True)
    return execute_trend_strategy(candidates_sorted, config, current_positions, max_positions, available_cash)
