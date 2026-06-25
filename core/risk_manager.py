"""
核心风控模块 (Risk Manager)
包含：动态 ATR 止损映射、凯利公式利润垫加仓、持仓熔断逻辑
"""
import os
import json
from core.logger_config import logger

def _load_hyperparams():
    """加载固化的静态超参数配置"""
    # 路径适配新的工程目录结构
    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'hyperparams.json')
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[风控层] hyperparams.json 解析失败: {e}")
    return {}

def calculate_atr(data, period=20):
    """计算真实波动幅度 (ATR)"""
    try:
        highs, lows, closes = data["high"].tolist(), data["low"].tolist(), data["close"].tolist()
    except Exception:
        highs, lows, closes = data.get("highs", []), data.get("lows", []), data.get("closes", [])
    if len(closes) < period + 1:
        return 0
    tr_list = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        tr_list.append(tr)
    return sum(tr_list[-period:]) / period

def get_dynamic_atr_multiplier(base_multiplier: float = 2.0, volume_ratio: float = 1.0) -> float:
    """动态 ATR 乘数：缩量收紧，放量回归"""
    if volume_ratio <= 0.2:
        return 1.2
    elif volume_ratio <= 0.5:
        return base_multiplier * 0.75
    return base_multiplier

def calculate_kelly_fraction(win_rate=0.45, win_loss_ratio=2.0):
    """
    计算凯利公式仓位比例 (f*)
    f* = (p * b - q) / b
    默认设定：45% 胜率，2.0 盈亏比
    """
    q = 1.0 - win_rate
    kelly_f = (win_rate * win_loss_ratio - q) / win_loss_ratio
    # 极客安全阀：只采用半凯利 (Half-Kelly) 以防止肥尾风险导致的破产
    return max(0.0, kelly_f * 0.5) 

def calculate_position_size(total_capital, current_price, atr, risk_per_trade=None, max_position_pct=0.15, base_atr_multiplier=2.0, volume_ratio=1.0, floating_profit_pct=0.0):
    """
    结合利润垫与半凯利公式的非对称仓位计算

    Args:
        floating_profit_pct (float): 当前标的已有持仓的浮盈比例 (例如 0.05 代表 5% 浮盈)
    """
    params = _load_hyperparams()
    if risk_per_trade is None:
        risk_per_trade = params.get('risk_per_trade', 0.008)
    
    max_position_pct = params.get('max_single_pct', max_position_pct)
        
    if atr <= 0 or current_price <= 0:
        return 0
        
    # 获取动态止损距离
    adjusted_multiplier = get_dynamic_atr_multiplier(base_atr_multiplier, volume_ratio)
    real_stop_distance = atr * adjusted_multiplier

    # ================= 核心：利润垫加仓逻辑 =================
    dynamic_risk = risk_per_trade
    
    # 浮盈超过 3% 且存在胜率期望时，开启凯利加速
    if floating_profit_pct > 0.03:
        kelly_scaler = calculate_kelly_fraction()
        # 新增风险敞口 = 基础风险 + (浮盈比例 * 半凯利比例)
        # 例如：5%浮盈 * 0.175(半凯利) = 增加 0.875% 的额外风控总额度
        extra_risk = floating_profit_pct * kelly_scaler
        dynamic_risk += extra_risk
        logger.info(f"  [风控系统] 触发利润垫加仓！浮盈 {floating_profit_pct*100:.2f}%，风控敞口从 {risk_per_trade*100:.2f}% 提升至 {dynamic_risk*100:.2f}%")
    # 处于亏损状态，无情维持最低硬性底仓风险
    elif floating_profit_pct < 0:
        logger.info(f"  [风控系统] 当前处于浮亏 {floating_profit_pct*100:.2f}%，锁死基础风控敞口。")
    # =======================================================
    
    # 修复后的物理敞口除法（使用真实止损距离计算买入股数）
    target_shares = (total_capital * dynamic_risk) / real_stop_distance
    
    # 资金上限锁机制
    max_shares_by_capital = (total_capital * max_position_pct) / current_price
    target_shares = min(target_shares, max_shares_by_capital)
    
    # 向下取整到 100 股的倍数
    target_shares = int(target_shares / 100) * 100
    if target_shares < 100:
        target_shares = 100
        
    return target_shares

def check_sell_signals(positions, quotes, config):
    """
    检查日内卖出及熔断信号（支持动态 ATR 与利润回撤防御）
    """
    sells = []
    params = _load_hyperparams()
    global_stop_loss = params.get("stop_loss_pct", -0.08)
    
    quote_map = {q["code"]: q for q in quotes}
    
    for code, pos in positions.items():
        if code not in quote_map:
            continue
            
        current_price = quote_map[code]["price"]
        avg_price = pos["avg_price"]
        shares = pos["shares"]
        
        # 1. 计算实时浮盈亏
        roi = (current_price - avg_price) / avg_price
        
        # 2. 硬性止损判定
        if roi <= global_stop_loss:
            sells.append({
                "code": code,
                "shares": shares,
                "price": current_price,
                "reason": f"触及全局硬止损 ({roi*100:.2f}%)"
            })
            continue
            
        # 3. 动态回撤保护 (如果曾有利润垫)
        highest_price = pos.get("highest_price", avg_price)
        if current_price > highest_price:
            pos["highest_price"] = current_price # 更新最高价
            
        # 如果历史最高浮盈超过 8%，回撤超过历史最高价的 3%，直接锁定利润
        if (highest_price - avg_price) / avg_price > 0.08:
            drawdown_from_high = (highest_price - current_price) / highest_price
            if drawdown_from_high > 0.03:
                sells.append({
                    "code": code,
                    "shares": shares,
                    "price": current_price,
                    "reason": f"利润回撤保护触发 (最高涨幅>8%，现回撤>3%)"
                })
                
    return sells

class LogicExitManager:
    # 兼容之前 strategy_engine.py 中依赖的空类
    pass

def check_building_cooldown(*args, **kwargs):
    """
    向下兼容补丁：用于桥接老版本 backtester.py 的幽灵依赖。
    直接返回 True (允许建仓)，将风控拦截权全部交接给新的利润垫和 ATR 系统。
    """
    return True