"""
core/news_coverage_gate.py — 资讯覆盖率门控模块

职责:
  1. 实时计算当前资讯对全市场的覆盖率
  2. 当覆盖率低于阈值时，自动降权或禁用资讯因子
  3. 提供 `get_news_weight_multiplier()` 供 fusion_engine 等消费者调用

约束:
  - 不修改交易执行逻辑
  - 不导入 live_trader / trade_engine / brain_node
  - 覆盖率文件由 scripts/analyze_news_coverage.py 写入
"""
import os
import sys
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger

CACHE_DIR = Path(PROJECT_ROOT) / "data_cache"
DB_PATH = CACHE_DIR / "news_events.db"
COVERAGE_CACHE_FILE = CACHE_DIR / "news_coverage_cache.json"

# ── 默认门控阈值 ──
DEFAULT_COVERAGE_HEALTHY = 0.20   # ≥20% → 资讯因子正常参与
DEFAULT_COVERAGE_WEAK = 0.05      # 5-20% → 资讯因子降权
# < 5% → 资讯因子禁用 (权重归零)


class NewsCoverageGate:
    """
    覆盖率门控器

    数据来源:
      - 优先读取 news_coverage_cache.json (由 analyze_news_coverage.py 写入)
      - 回退: 直接查询 news_events.db 计算覆盖率
      - 最终回退: 假设覆盖率 0%

    权重策略:
      coverage >= HEALTHY (20%) → multiplier = 1.0   (正常)
      coverage >= WEAK (5%)    → multiplier = 0.3   (降权至 30%)
      coverage < WEAK          → multiplier = 0.0   (禁用)

    缓存策略:
      - coverage_cache.json 的 TTL 为 10 分钟
      - 过期后回退到 DB 实时查询 (轻量)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._healthy_threshold = DEFAULT_COVERAGE_HEALTHY
        self._weak_threshold = DEFAULT_COVERAGE_WEAK
        self._cache_ttl_seconds = 600  # 10 minutes

    # ── 公共 API ────────────────────────────────────

    def get_coverage(self) -> dict:
        """
        返回当前覆盖率快照:
          {
            "coverage_rate": 0.0023,
            "coverage_rate_pct": "0.23%",
            "covered_symbols": 12,
            "total_symbols": 5210,
            "status": "INSUFFICIENT",
            "computed_at": "2026-06-26 15:00:00",
            "source": "cache" | "db_live" | "fallback_zero"
          }
        """
        # 1. Try cache
        cached = self._read_cache()
        if cached and self._cache_fresh(cached):
            logger.debug(f"[CoverageGate] 命中缓存: {cached['coverage_rate_pct']}")
            return cached

        # 2. Compute live from DB
        live = self._compute_from_db()
        if live:
            self._write_cache(live)
            return live

        # 3. Fallback: assume zero
        fallback = {
            "coverage_rate": 0.0,
            "coverage_rate_pct": "0.00%",
            "covered_symbols": 0,
            "total_symbols": 5210,
            "status": "INSUFFICIENT",
            "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "fallback_zero",
        }
        logger.warning("[CoverageGate] 无法计算覆盖率，回退至 0%")
        return fallback

    def get_news_weight_multiplier(self) -> float:
        """
        返回资讯因子权重乘数。

        Returns:
            1.0  → 正常
            0.3  → 降权至 30%
            0.0  → 禁用
        """
        cov = self.get_coverage()
        rate = cov.get("coverage_rate", 0.0)
        status = cov.get("status", "INSUFFICIENT")

        if rate >= self._healthy_threshold:
            logger.debug(f"[CoverageGate] 覆盖率 {rate:.1%} ≥ {self._healthy_threshold:.0%} → 正常权重")
            return 1.0
        elif rate >= self._weak_threshold:
            logger.warning(
                f"[CoverageGate] 覆盖率 {rate:.1%} 在 {self._weak_threshold:.0%}-{self._healthy_threshold:.0%} → 资讯因子降权至 30%"
            )
            return 0.3
        else:
            logger.warning(
                f"[CoverageGate] 覆盖率 {rate:.1%} < {self._weak_threshold:.0%} → 资讯因子禁用 (权重归零)"
            )
            return 0.0

    def get_status_report(self) -> dict:
        """返回完整的门控状态报告，供报告/通知使用"""
        cov = self.get_coverage()
        mult = self.get_news_weight_multiplier()

        level = "safe"
        if mult == 0.3:
            level = "degraded"
        elif mult == 0.0:
            level = "disabled"

        return {
            **cov,
            "weight_multiplier": mult,
            "protection_level": level,
            "threshold_healthy": self._healthy_threshold,
            "threshold_weak": self._weak_threshold,
            "message": self._status_message(mult, cov),
        }

    # ── 内部实现 ────────────────────────────────────

    def _status_message(self, multiplier: float, cov: dict) -> str:
        rate = cov.get("coverage_rate_pct", "?")
        covered = cov.get("covered_symbols", 0)
        total = cov.get("total_symbols", 0)
        if multiplier == 1.0:
            return f"资讯覆盖率 {rate} ({covered}/{total}) 正常，资讯因子权重 100%"
        elif multiplier == 0.3:
            return f"资讯覆盖率 {rate} ({covered}/{total}) 偏低，资讯因子已自动降权至 30%"
        else:
            return f"资讯覆盖率 {rate} ({covered}/{total}) 严重不足，资讯因子已自动禁用"

    def _read_cache(self) -> Optional[dict]:
        try:
            if COVERAGE_CACHE_FILE.exists():
                with self._lock:
                    data = json.loads(COVERAGE_CACHE_FILE.read_text(encoding="utf-8"))
                return data
        except Exception:
            pass
        return None

    def _write_cache(self, data: dict):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with self._lock:
                COVERAGE_CACHE_FILE.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        except Exception as e:
            logger.error(f"[CoverageGate] 写入缓存失败: {e}")

    def _cache_fresh(self, data: dict) -> bool:
        try:
            computed_at = data.get("computed_at", "")
            dt = datetime.strptime(computed_at, "%Y-%m-%d %H:%M:%S")
            age = (datetime.now() - dt).total_seconds()
            return age < self._cache_ttl_seconds
        except Exception:
            return False

    def _compute_from_db(self) -> Optional[dict]:
        """从 news_events.db 实时计算覆盖率 (轻量)"""
        if not DB_PATH.exists():
            return None

        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=3.0)
            cursor = conn.cursor()

            # Count unique symbols
            cursor.execute("SELECT symbols FROM news_events")
            rows = cursor.fetchall()
            conn.close()

            all_symbols = set()
            for (syms_str,) in rows:
                try:
                    syms = json.loads(syms_str) if syms_str else []
                except (json.JSONDecodeError, TypeError):
                    syms = []
                for s in syms:
                    s = s.strip()
                    if s.endswith('.SZ'):
                        all_symbols.add(f"sz{s.split('.')[0]}")
                    elif s.endswith('.SH'):
                        all_symbols.add(f"sh{s.split('.')[0]}")
                    elif s.startswith(('sz', 'sh')) and len(s) == 8:
                        all_symbols.add(s)

            covered = len(all_symbols)
            total = 5210  # fixed: xtdata 沪深A股
            rate = covered / total if total > 0 else 0.0

            if rate >= self._healthy_threshold:
                status = "HEALTHY"
            elif rate >= self._weak_threshold:
                status = "WEAK"
            else:
                status = "INSUFFICIENT"

            return {
                "coverage_rate": round(rate, 4),
                "coverage_rate_pct": f"{rate*100:.2f}%",
                "covered_symbols": covered,
                "total_symbols": total,
                "status": status,
                "computed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": "db_live",
            }
        except Exception as e:
            logger.error(f"[CoverageGate] DB查询失败: {e}")
            return None


# ── 全局单例 ──

_gate_instance: Optional[NewsCoverageGate] = None


def get_coverage_gate() -> NewsCoverageGate:
    global _gate_instance
    if _gate_instance is None:
        _gate_instance = NewsCoverageGate()
    return _gate_instance


def get_news_weight_multiplier() -> float:
    """便捷函数: 返回资讯因子权重乘数 (供 fusion_engine 调用)"""
    return get_coverage_gate().get_news_weight_multiplier()
