"""
PaperTradingProvider — Copilot 模拟盘查询上下文

为 Copilot 的自然语言查询提供模拟盘绩效数据。
只读 paper_performance.json，不修改任何交易状态。
"""
import json
import os
from datetime import datetime


class PaperTradingProvider:
    """读取 paper_performance.json，提供结构化问答上下文。"""

    def __init__(self, perf_path: str | None = None):
        if perf_path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            perf_path = os.path.join(project_root, "data_cache", "paper_performance.json")
        self.perf_path = perf_path

    def _load(self) -> dict | None:
        """加载最新绩效数据。"""
        if not os.path.exists(self.perf_path):
            return None
        try:
            with open(self.perf_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def get_summary(self) -> str:
        """返回模拟盘绩效摘要文本。"""
        s = self._load()
        if not s:
            return "暂无模拟盘绩效数据。"

        b = s.get("basic_stats", {})
        d = s.get("direction_stats", {})
        p = s.get("portfolio_stats", {})

        return (
            f"模拟盘绩效摘要（{s.get('generated_at', '?')}）："
            f"信号总数 {b.get('total_signals', 0)}，"
            f"成交 {b.get('filled_orders', 0)} 笔（BUY {d.get('buy_count', 0)} / SELL {d.get('sell_count', 0)}），"
            f"拒单 {b.get('rejected_orders', 0)} 笔，"
            f"跳过 {b.get('skipped_orders', 0)} 笔。"
            f"当前现金 ¥{p.get('current_cash', 0):,.2f}，"
            f"持仓 {p.get('open_positions', 0)} 只。"
        )

    def get_positions(self) -> str:
        """返回当前持仓明细。"""
        s = self._load()
        if not s:
            return "暂无持仓数据。"

        p = s.get("portfolio_stats", {})
        detail = p.get("positions_detail", [])
        if not detail:
            return f"当前无持仓，现金 ¥{p.get('current_cash', 0):,.2f}。"

        lines = [f"当前持仓（现金 ¥{p.get('current_cash', 0):,.2f}）："]
        for pos in detail:
            lines.append(
                f"  {pos['code']} {pos['quantity']}股 "
                f"均价¥{pos['avg_cost']:.3f} 成本¥{pos['cost_basis']:,.2f}"
            )
        return "\n".join(lines)

    def get_recent_fills(self, n: int = 5) -> str:
        """返回最近 N 笔成交。"""
        s = self._load()
        if not s:
            return "暂无成交流水。"

        rf = s.get("recent_fills", [])[:n]
        if not rf:
            return "暂无成交流水。"

        lines = [f"最近 {len(rf)} 笔成交流水："]
        for f in rf:
            icon = {"FILLED": "✅", "REJECTED": "❌", "SKIPPED": "⏭️"}.get(f.get("status", ""), "❓")
            lines.append(
                f"  {icon} {f.get('timestamp', '?')} {f.get('action', '?')} "
                f"{f.get('code', '?')} x{f.get('quantity', 0)} "
                f"@{f.get('avg_price', 0):.3f} [{f.get('status', '?')}]"
            )
        return "\n".join(lines)

    def get_rejections(self) -> str:
        """返回拒单原因汇总。"""
        s = self._load()
        if not s:
            return "暂无拒单数据。"

        a = s.get("anomaly_stats", {})
        reasons = a.get("reject_reasons", {})
        if not reasons:
            return "无拒单记录。"

        lines = ["拒单原因汇总："]
        for reason, count in reasons.items():
            lines.append(f"  {reason}: {count} 次")
        return "\n".join(lines)

    def get_context(self) -> str:
        """返回 Copilot 可直接使用的上下文字符串。"""
        parts = [
            self.get_summary(),
            self.get_recent_fills(n=5),
        ]
        return "\n\n".join(parts)
