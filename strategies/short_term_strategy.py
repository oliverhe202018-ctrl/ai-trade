"""
短线策略 MVP — Phase 10

包含两个子策略：
  1. LimitUpRelayStrategy — 打板/涨停接力观察
  2. IntradayTStrategy — 做T/日内波段辅助

策略仅接入 Paper Trading，不触发 QMT 实盘。
"""
import json
import os
import time
from datetime import datetime
from typing import List, Dict, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# ── 统一信号 Schema ──────────────────────────────────────

def _make_signal(
    strategy_name: str,
    symbol: str,
    name: str,
    action: str,
    confidence: float,
    reason: str,
    features: dict,
    risk_flags: list = None,
) -> dict:
    return {
        "strategy_name": strategy_name,
        "symbol": symbol,
        "name": name,
        "action": action,
        "confidence": round(confidence, 3),
        "reason": reason,
        "features": features,
        "risk_flags": risk_flags or [],
        "mode": "paper",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ═══════════════════════════════════════════════════════════
#  LimitUpRelayStrategy — 打板/涨停接力
# ═══════════════════════════════════════════════════════════

class LimitUpRelayStrategy:
    """
    涨停接力观察策略。

    触发条件:
      fusion_score >= 85 且 momentum_score >= 80 且 main_money_score >= 70
      且 price_change_pct >= 7% 且 volume_ratio >= 1.5
      且无明显主力出货信号

    输出: WATCH (仅观察) 或 PAPER_BUY_CANDIDATE (可入 paper queue)
    """

    FUSION_THRESHOLD = 85
    MOMENTUM_THRESHOLD = 80
    MONEY_THRESHOLD = 70
    CHANGE_PCT_THRESHOLD = 7.0
    VOLUME_RATIO_THRESHOLD = 1.5

    def analyze(self, candidates: List[dict]) -> List[dict]:
        signals = []
        for c in candidates:
            fusion = c.get("fusion_score") or c.get("score", 0)
            momentum = c.get("momentum_score", 0)
            money = c.get("main_money_score", c.get("fund_score", 0))
            change_pct = c.get("change_pct", c.get("price_change_pct", 0))
            vol_ratio = c.get("volume_ratio", 1.0)
            code = c.get("code", "")
            name = c.get("name", "")

            # 主力出货检测
            is_distribution = c.get("is_distribution", False)
            active_sell = c.get("estimated_active_sell_amount", 0)
            active_buy = c.get("estimated_active_buy_amount", 0)
            net_flow = active_buy - active_sell

            if is_distribution or (net_flow < 0 and abs(net_flow) > active_buy * 0.5):
                continue

            if (
                fusion >= self.FUSION_THRESHOLD
                and momentum >= self.MOMENTUM_THRESHOLD
                and money >= self.MONEY_THRESHOLD
                and change_pct >= self.CHANGE_PCT_THRESHOLD
                and vol_ratio >= self.VOLUME_RATIO_THRESHOLD
            ):
                # confidence = weighted average of score ratios
                conf = (
                    (fusion / 100) * 0.35
                    + (momentum / 100) * 0.25
                    + (money / 100) * 0.20
                    + (min(change_pct / 10, 1.0)) * 0.10
                    + (min(vol_ratio / 3, 1.0)) * 0.10
                )
                action = "PAPER_BUY_CANDIDATE" if conf >= 0.80 else "WATCH"

                signals.append(_make_signal(
                    "LimitUpRelayStrategy",
                    code, name, action, conf,
                    reason=f"涨停接力候选: fusion={fusion}, 涨幅={change_pct}%, 量比={vol_ratio}",
                    features={
                        "fusion_score": fusion,
                        "momentum_score": momentum,
                        "main_money_score": money,
                        "change_pct": change_pct,
                        "volume_ratio": vol_ratio,
                        "net_flow": net_flow,
                    },
                    risk_flags=["涨停接力风险: 追高可能回落"] if action != "WATCH" else None,
                ))

        return signals


# ═══════════════════════════════════════════════════════════
#  IntradayTStrategy — 做T/日内波段
# ═══════════════════════════════════════════════════════════

class IntradayTStrategy:
    """
    做T/日内波段辅助策略。

    触发条件:
      已有 Paper Trading 持仓
      且当前价格相对成本处于合理区间
      且 Tape Reader 显示资金重新流入
      且 fusion_score 未明显恶化

    VWAP/支撑位为可选字段：存在则使用，不存在则降级为 score-based。

    输出: PAPER_T_BUY_CANDIDATE / PAPER_T_SELL_CANDIDATE
    """

    DIP_THRESHOLD_PCT = -2.0  # 回落超过2%考虑买入做T
    RISE_THRESHOLD_PCT = 2.0   # 上涨超过2%考虑卖出做T
    MIN_FUSION = 60            # fusion 最低阈值

    def analyze(
        self,
        positions: dict,
        quotes: List[dict],
        tracking_data: Optional[dict] = None,
    ) -> List[dict]:
        signals = []
        quotes_by_code = {q.get("code", ""): q for q in quotes}

        # 读取主力资金数据
        tracking_items = {}
        if tracking_data:
            tracking_items = {
                it["code"]: it
                for it in tracking_data.get("items", [])
                if "code" in it
            }

        for code, pos in positions.items():
            q = quotes_by_code.get(code)
            if not q:
                continue

            avg_cost = pos.get("avg_cost", 0)
            current_price = q.get("price", 0)
            if avg_cost <= 0 or current_price <= 0:
                continue

            change_from_cost = (current_price - avg_cost) / avg_cost * 100
            fusion = q.get("fusion_score", q.get("score", 50))

            # 检查 Tape Reader 资金流
            tape = tracking_items.get(code, {})
            imbalance = tape.get("order_book_imbalance", 0)
            proxy_score = tape.get("main_money_proxy_score", 0)
            net_inflow = tape.get("estimated_large_order_net_inflow", 0)
            active_buy = tape.get("estimated_active_buy_amount", 0)
            active_sell = tape.get("estimated_active_sell_amount", 0)

            # VWAP (可选)
            vwap = q.get("vwap")

            # ── T BUY 判断 ──
            buy_signal = False
            buy_reason_parts = []

            if change_from_cost <= self.DIP_THRESHOLD_PCT:
                buy_signal = True
                buy_reason_parts.append(f"回落{abs(change_from_cost):.1f}%")

            if vwap is not None and current_price < vwap * 0.98:
                buy_signal = True
                buy_reason_parts.append(f"低于VWAP")

            if proxy_score > 50 and imbalance > 0.1:
                buy_signal = True
                buy_reason_parts.append(f"资金回流(proxy={proxy_score}, imb={imbalance:.2f})")

            if fusion < self.MIN_FUSION:
                buy_signal = False  # 融合评分太低，全否决

            if buy_signal and fusion >= self.MIN_FUSION:
                conf = (
                    (fusion / 100) * 0.3
                    + (min(proxy_score / 100, 1.0)) * 0.3
                    + (min(abs(change_from_cost) / 5, 1.0)) * 0.2
                    + (0.5 if imbalance > 0 else 0) * 0.2
                )
                signals.append(_make_signal(
                    "IntradayTStrategy", code, q.get("name", code),
                    "PAPER_T_BUY_CANDIDATE", conf,
                    reason=f"T-BUY: {', '.join(buy_reason_parts)}",
                    features={
                        "change_from_cost": round(change_from_cost, 2),
                        "fusion_score": fusion,
                        "proxy_score": proxy_score,
                        "vwap": vwap,
                        "imbalance": round(imbalance, 3),
                    },
                ))

            # ── T SELL 判断 ──
            if change_from_cost >= self.RISE_THRESHOLD_PCT:
                if proxy_score < 30 or imbalance < -0.1:
                    conf = min(change_from_cost / 5, 1.0)
                    signals.append(_make_signal(
                        "IntradayTStrategy", code, q.get("name", code),
                        "PAPER_T_SELL_CANDIDATE", conf,
                        reason=f"T-SELL: 涨幅{change_from_cost:.1f}%, 资金流出信号",
                        features={
                            "change_from_cost": round(change_from_cost, 2),
                            "fusion_score": fusion,
                            "proxy_score": proxy_score,
                        },
                    ))

        return signals


# ═══════════════════════════════════════════════════════════
#  ShortTermStrategyRunner — 统一入口
# ═══════════════════════════════════════════════════════════

class ShortTermStrategyRunner:
    """短线策略统一执行器。"""

    def __init__(self):
        self.limit_up = LimitUpRelayStrategy()
        self.intraday_t = IntradayTStrategy()

    def run(
        self,
        candidates: List[dict] = None,
        positions: dict = None,
        quotes: List[dict] = None,
        tracking_data: dict = None,
    ) -> List[dict]:
        all_signals = []

        if candidates:
            all_signals.extend(self.limit_up.analyze(candidates))

        if positions:
            all_signals.extend(
                self.intraday_t.analyze(positions, quotes or [], tracking_data)
            )

        # 发通知
        if all_signals:
            try:
                from core.notification_service import notify_event
                for s in all_signals:
                    level = "warning" if s["confidence"] >= 0.8 else "info"
                    notify_event(
                        "strategy_signal",
                        f"短线策略: {s['strategy_name']}",
                        symbol=f"{s['symbol']} {s['name']}",
                        message=(
                            f"动作: {s['action']}\n"
                            f"置信度: {s['confidence']}\n"
                            f"原因: {s['reason']}\n"
                            f"风险: {s['risk_flags']}"
                        ),
                        level=level,
                    )
            except Exception:
                pass

        return all_signals


def run_short_term_analysis(
    candidates: List[dict] = None,
    positions: dict = None,
    quotes: List[dict] = None,
) -> List[dict]:
    """便捷入口。"""
    runner = ShortTermStrategyRunner()
    return runner.run(candidates=candidates, positions=positions, quotes=quotes)
