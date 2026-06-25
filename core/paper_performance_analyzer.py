"""
Paper Performance Analyzer — Phase 8b

职责：
  1. 读取 paper_trade_fills.jsonl + paper_portfolio.json
  2. 计算基础交易统计 / 持仓统计 / 异常统计
  3. 无法准确计算的指标标记为 unavailable（不伪造）
  4. 输出 paper_performance.json + paper_trading_performance.md

约束：
  - 只读 fills + portfolio，不写任何交易数据
  - 不依赖行情源、QMT、或任何实时数据
  - 不影响任何交易决策
"""
import json
import os
from collections import Counter
from datetime import datetime

from core.logger_config import logger

# ── 文件路径 ──────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILLS_PATH = os.path.join(
    PROJECT_ROOT, "data_cache", "paper_trade_fills.jsonl"
)
PORTFOLIO_PATH = os.path.join(
    PROJECT_ROOT, "data_cache", "paper_portfolio.json"
)
PERF_JSON_PATH = os.path.join(
    PROJECT_ROOT, "data_cache", "paper_performance.json"
)
PERF_MD_PATH = os.path.join(
    PROJECT_ROOT, "reports", "paper_trading_performance.md"
)

INITIAL_CASH = 100_000.0
WINDOW_HOURS = 72


# ═══════════════════════════════════════════════════════════
#  Analyzer
# ═══════════════════════════════════════════════════════════

class PaperPerformanceAnalyzer:
    """读取 paper fills + portfolio，生成统计报告。"""

    def __init__(
        self,
        fills_path: str = FILLS_PATH,
        portfolio_path: str = PORTFOLIO_PATH,
    ):
        self.fills_path = fills_path
        self.portfolio_path = portfolio_path

    # ── 数据加载 ──────────────────────────────────────────

    def _load_fills(self, window_hours: int = WINDOW_HOURS) -> list[dict]:
        """加载 paper_trade_fills.jsonl，可选按时间窗口过滤。"""
        if not os.path.exists(self.fills_path):
            return []
        fills = []
        cutoff = None
        if window_hours and window_hours > 0:
            from datetime import timedelta
            cutoff = datetime.now() - timedelta(hours=window_hours)

        json_errors = 0
        with open(self.fills_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    json_errors += 1
                    continue

                if cutoff:
                    try:
                        ts = datetime.strptime(
                            entry.get("timestamp", ""), "%Y-%m-%d %H:%M:%S"
                        )
                        if ts < cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass

                fills.append(entry)
        return fills

    def _load_portfolio(self) -> dict:
        """加载 paper_portfolio.json。"""
        if not os.path.exists(self.portfolio_path):
            return {"cash": INITIAL_CASH, "positions": {}}
        try:
            with open(self.portfolio_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("[PaperAnalyzer] portfolio 读取失败，使用默认")
            return {"cash": INITIAL_CASH, "positions": {}}

    # ── 基础统计 ──────────────────────────────────────────

    def _compute_basic(self, fills: list[dict]) -> dict:
        """从 fills 计算订单级别统计。"""
        total_signals = len(fills)
        filled = sum(1 for e in fills if e.get("status") == "FILLED")
        rejected = sum(1 for e in fills if e.get("status") == "REJECTED")
        skipped = sum(1 for e in fills if e.get("status") == "SKIPPED")
        failed = sum(1 for e in fills if e.get("status") == "FAILED")
        other = total_signals - filled - rejected - skipped - failed

        return {
            "total_signals":    total_signals,
            "filled_orders":    filled,
            "rejected_orders":  rejected,
            "skipped_orders":   skipped,
            "failed_orders":    failed,
            "other_statuses":   other,
        }

    # ── 交易方向统计 ──────────────────────────────────────

    def _compute_direction(self, fills: list[dict]) -> dict:
        """按 BUY / SELL 统计成交。"""
        buy_count = sum(
            1 for e in fills
            if e.get("status") == "FILLED" and e.get("action") == "BUY"
        )
        sell_count = sum(
            1 for e in fills
            if e.get("status") == "FILLED" and e.get("action") == "SELL"
        )
        return {
            "buy_count":   buy_count,
            "sell_count":  sell_count,
        }

    # ── 组合统计 ──────────────────────────────────────────

    def _compute_portfolio(self, portfolio: dict) -> dict:
        """从 portfolio 快照提取持仓/现金信息。"""
        positions = portfolio.get("positions", {})
        cash = portfolio.get("cash", INITIAL_CASH)

        positions_detail = []
        total_cost = 0.0
        for code, pos in positions.items():
            qty = pos.get("quantity", 0)
            avg_cost = pos.get("avg_cost", 0.0)
            cost_basis = qty * avg_cost
            total_cost += cost_basis
            positions_detail.append({
                "code":       code,
                "quantity":   qty,
                "avg_cost":   avg_cost,
                "cost_basis": round(cost_basis, 2),
            })

        return {
            "initial_cash":       INITIAL_CASH,
            "current_cash":       cash,
            "open_positions":     len(positions),
            "positions_detail":   positions_detail,
            "total_cost_basis":   round(total_cost, 2),
        }

    # ── 异常统计 ──────────────────────────────────────────

    def _compute_anomalies(self, fills: list[dict]) -> dict:
        """检测异常：数据污染 / 拒单原因 / JSON 错误。"""
        # REJECTED 但 filled_qty > 0 或 avg_price > 0 → 污染
        polluted = sum(
            1 for e in fills
            if e.get("status") == "REJECTED"
            and (e.get("filled_qty", 0) > 0 or e.get("avg_price", 0) > 0)
        )

        # 拒单原因汇总
        reject_reasons = Counter()
        for e in fills:
            if e.get("status") == "REJECTED" and e.get("reason"):
                reject_reasons[e["reason"]] += 1

        # 重复 order_id 检测
        order_ids = [e.get("order_id", "") for e in fills if e.get("order_id")]
        duplicate_count = len(order_ids) - len(set(order_ids))

        return {
            "reject_reasons": dict(reject_reasons),
            "data_pollution": {
                "detected": polluted > 0,
                "polluted_count": polluted,
                "description": (
                    "REJECTED 订单存在 filled_qty>0 或 avg_price>0 (数据来自 Phase 7 之前旧版)"
                    if polluted > 0
                    else "无污染"
                ),
            },
            "duplicate_order_ids": {
                "detected": duplicate_count > 0,
                "count": duplicate_count,
            },
        }

    # ── 不可用指标 ────────────────────────────────────────

    def _unavailable(self) -> dict:
        """返回当前不可计算的高阶指标及原因。"""
        return {
            "estimated_market_value": "unavailable: 缺少实时行情, paper_portfolio 无 current_price",
            "total_equity":           "unavailable: 需要 market_value + cash",
            "unrealized_pnl":         "unavailable: 需要 market_value - cost_basis",
            "realized_pnl":           "unavailable: fills 缺少 realized_pnl 字段",
            "total_return":           "unavailable: 需要净值序列 (cash + market_value over time)",
            "win_rate":               "unavailable: fills 缺少逐笔 realized_pnl",
            "avg_win":                "unavailable: 同上",
            "avg_loss":               "unavailable: 同上",
            "profit_factor":          "unavailable: 同上",
            "max_drawdown":           "unavailable: 需要日频净值曲线",
            "sharpe_ratio":           "unavailable: 需要日频收益率序列",
        }

    # ── 最近成交流水 ──────────────────────────────────────

    def _recent_fills(self, fills: list[dict], n: int = 10) -> list[dict]:
        """返回最近 N 条成交流水（按 timestamp 倒序）。"""
        sorted_fills = sorted(
            fills,
            key=lambda e: e.get("timestamp", ""),
            reverse=True,
        )
        return sorted_fills[:n]

    # ── 主分析方法 ────────────────────────────────────────

    def analyze(self, window_hours: int = WINDOW_HOURS) -> dict:
        """执行全量分析，返回统计字典。"""
        fills = self._load_fills(window_hours=window_hours)
        portfolio = self._load_portfolio()

        stats = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_sources": {
                "fills_path":     self.fills_path,
                "portfolio_path": self.portfolio_path,
            },
            "window": {
                "hours":         window_hours,
                "fill_entries":  len(fills),
            },
            "basic_stats":      self._compute_basic(fills),
            "direction_stats":  self._compute_direction(fills),
            "portfolio_stats":  self._compute_portfolio(portfolio),
            "anomaly_stats":    self._compute_anomalies(fills),
            "unavailable":      self._unavailable(),
            "recent_fills":     self._recent_fills(fills, n=10),
            "disclaimer": (
                "当前报告只基于模拟盘成交/持仓文件统计，"
                "不包含真实行情估值，不构成投资建议。"
            ),
        }
        return stats

    # ── 输出 ──────────────────────────────────────────────

    def write_json(self, stats: dict, path: str = PERF_JSON_PATH) -> None:
        """写入 paper_performance.json。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        logger.info(f"[PaperAnalyzer] JSON 已写入: {path}")

    def write_markdown_report(
        self, stats: dict, path: str = PERF_MD_PATH
    ) -> None:
        """生成 paper_trading_performance.md。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)

        b = stats["basic_stats"]
        d = stats["direction_stats"]
        p = stats["portfolio_stats"]
        a = stats["anomaly_stats"]
        u = stats["unavailable"]
        rf = stats["recent_fills"]

        lines = [
            "# 模拟盘绩效报告 (Paper Trading Performance)",
            "",
            f"> 生成时间: {stats['generated_at']}",
            f"> 观察窗口: {stats['window']['hours']} 小时",
            f"> 数据条目: {stats['window']['fill_entries']} 条",
            "",
            "> ⚠️ **免责声明**: "
            "当前报告只基于模拟盘成交/持仓文件统计，"
            "不包含真实行情估值，不构成投资建议。",
            "",
            "---",
            "",
            "## 1. 基础统计",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            f"| 信号总数 | {b['total_signals']} |",
            f"| 成交 (FILLED) | {b['filled_orders']} |",
            f"| 拒单 (REJECTED) | {b['rejected_orders']} |",
            f"| 跳过 (SKIPPED) | {b['skipped_orders']} |",
            f"| 失败 (FAILED) | {b['failed_orders']} |",
            f"| 其他状态 | {b['other_statuses']} |",
            "",
            "## 2. 交易方向",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            f"| BUY 成交 | {d['buy_count']} |",
            f"| SELL 成交 | {d['sell_count']} |",
            "",
            "## 3. 组合快照",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            f"| 初始资金 | ¥{p['initial_cash']:,.2f} |",
            f"| 当前现金 | ¥{p['current_cash']:,.2f} |",
            f"| 持仓标的数 | {p['open_positions']} |",
            f"| 持仓总成本 | ¥{p['total_cost_basis']:,.2f} |",
        ]

        if p["positions_detail"]:
            lines.append("")
            lines.append("### 持仓明细")
            lines.append("")
            lines.append("| 代码 | 数量 | 均价 | 成本 |")
            lines.append("|------|------|------|------|")
            for pos in p["positions_detail"]:
                lines.append(
                    f"| {pos['code']} | {pos['quantity']} | "
                    f"¥{pos['avg_cost']:.3f} | ¥{pos['cost_basis']:,.2f} |"
                )

        lines += [
            "",
            "## 4. 异常统计",
            "",
            f"- 数据污染: {'⚠️ 检测到' if a['data_pollution']['detected'] else '✅ 无'} "
            f"({a['data_pollution']['polluted_count']} 条)",
            f"- 重复订单: {'⚠️ 检测到' if a['duplicate_order_ids']['detected'] else '✅ 无'} "
            f"({a['duplicate_order_ids']['count']} 条)",
        ]

        if a["reject_reasons"]:
            lines.append("")
            lines.append("### 拒单原因汇总")
            lines.append("")
            for reason, count in a["reject_reasons"].items():
                lines.append(f"- {reason}: {count} 次")

        lines += [
            "",
            "## 5. 不可用指标 (unavailable)",
            "",
            "以下指标因缺少实时行情数据或 fills 字段不完整，当前无法准确计算：",
            "",
            "| 指标 | 原因 |",
            "|------|------|",
        ]
        for metric, reason in sorted(u.items()):
            lines.append(f"| `{metric}` | {reason} |")

        if rf:
            lines += [
                "",
                "## 6. 最近成交流水",
                "",
                "| 时间 | 操作 | 代码 | 数量 | 状态 |",
                "|------|------|------|------|------|",
            ]
            for fill in rf[:10]:
                ts = fill.get("timestamp", "?")
                action = fill.get("action", "?")
                code = fill.get("code", "?")
                qty = fill.get("quantity", 0)
                status = fill.get("status", "?")
                icon = {
                    "FILLED": "✅", "REJECTED": "❌",
                    "SKIPPED": "⏭️", "FAILED": "💥",
                }.get(status, "❓")
                lines.append(
                    f"| {ts} | {icon} {action} | "
                    f"{code} | {qty} | {status} |"
                )

        lines += [
            "",
            "---",
            "",
            f"*报告由 paper_performance_analyzer.py 自动生成于 {stats['generated_at']}*",
        ]

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        logger.info(f"[PaperAnalyzer] Markdown 报告已写入: {path}")


# ═══════════════════════════════════════════════════════════
#  便捷入口
# ═══════════════════════════════════════════════════════════

def run_analysis(
    window_hours: int = WINDOW_HOURS,
    json_path: str = PERF_JSON_PATH,
    md_path: str = PERF_MD_PATH,
) -> dict:
    """一键分析并输出 JSON + Markdown。"""
    analyzer = PaperPerformanceAnalyzer()
    stats = analyzer.analyze(window_hours=window_hours)
    analyzer.write_json(stats, path=json_path)
    analyzer.write_markdown_report(stats, path=md_path)
    return stats


if __name__ == "__main__":
    run_analysis()
