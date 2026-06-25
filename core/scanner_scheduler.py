"""
Scanner 自动调度器 — Phase 9.5

职责：
  1. 按配置间隔自动调用 MarketScanner.scan()
  2. 扫描后将 Top N 高分标的通过 notification_service 推送
  3. 内建 dedup，防止同一标的重复刷屏
  4. 可通过 scanner.auto_run: false 完全关闭

使用:
    python core/scanner_scheduler.py          # 前台运行
    # 或通过 cron: python core/scanner_scheduler.py --once
"""
import os
import sys
import time
import json
import threading

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger

INTERVAL_SECONDS = 60
TOP_N = 10
ALERT_THRESHOLD = 80

_stop_event = threading.Event()
_last_scan_time = 0.0


def _load_config():
    try:
        from core.broker import load_config
        return load_config()
    except Exception:
        return {}


def _should_run(cfg: dict) -> bool:
    """检查 scanner.auto_run 是否开启。"""
    scanner_cfg = cfg.get("scanner", {})
    return scanner_cfg.get("auto_run", False)


def _get_interval(cfg: dict) -> int:
    return int(cfg.get("scanner", {}).get("interval_seconds", 60))


def run_scan_once(cfg: dict | None = None) -> list[dict]:
    """
    执行一次全市场扫描，返回高分标的列表。
    如果 scanner.auto_run 关闭则跳过。
    """
    if cfg is None:
        cfg = _load_config()

    scanner_cfg = cfg.get("scanner", {})
    threshold = scanner_cfg.get("alert_score_threshold", 80)

    logger.info("[ScannerScheduler] 开始全市场扫描...")
    try:
        from core.market_scanner import run_scanner_async
        # run_scanner_async 是异步的，我们得等它完成
        # 改用同步 scan 直接跑
        from core.market_scanner import MarketScanner
        scanner = MarketScanner()
        scanner.scan()
    except Exception as e:
        logger.error(f"[ScannerScheduler] 扫描失败: {e}")
        return []

    # 读取扫描结果
    candidates_file = os.path.join(
        PROJECT_ROOT, "data_cache", "market_candidates.json"
    )
    if not os.path.exists(candidates_file):
        logger.warning("[ScannerScheduler] 扫描完成但无候选文件")
        return []

    try:
        with open(candidates_file, "r", encoding="utf-8") as f:
            candidates = json.load(f)
    except Exception as e:
        logger.error(f"[ScannerScheduler] 读取候选文件失败: {e}")
        return []

    # 提取高分标的
    if isinstance(candidates, dict):
        candidates = candidates.get("candidates", [])

    high_scorers = []
    for c in candidates:
        score = c.get("fusion_score") or c.get("score", 0)
        if score >= threshold:
            high_scorers.append(c)

    logger.info(
        f"[ScannerScheduler] 扫描完成: {len(candidates)} 候选, "
        f"{len(high_scorers)} 高分 (≥{threshold})"
    )

    # 发通知
    if high_scorers:
        from core.notification_service import notify_event
        for c in high_scorers[:TOP_N]:
            code = c.get("code", "?")
            name = c.get("name", "")
            score = c.get("fusion_score") or c.get("score", 0)
            notify_event(
                "scanner_signal",
                "全市场扫描高分发现",
                symbol=f"{code} {name}".strip(),
                message=f"评分: {score}\n名称: {name}",
                level="warning" if score >= 90 else "info",
            )

    return high_scorers


def run_scanner_loop():
    """自动扫描主循环。"""
    cfg = _load_config()

    if not _should_run(cfg):
        logger.info(
            "[ScannerScheduler] scanner.auto_run=false，自动扫描未启用。"
            " 可手动运行 Dashboard 或设置 scanner.auto_run: true。"
        )
        return

    interval = _get_interval(cfg)
    logger.info(f"[ScannerScheduler] 自动扫描已启用，间隔 {interval}s，阈值 {cfg.get('scanner', {}).get('alert_score_threshold', 80)}")

    while not _stop_event.is_set():
        try:
            run_scan_once(cfg)
        except Exception as e:
            logger.error(f"[ScannerScheduler] 循环异常: {e}")
            from core.notification_service import notify_event
            notify_event("system_error", "Scanner 异常", message=str(e), level="critical")

        # 小步 sleep 以快速响应停止信号
        for _ in range(interval):
            if _stop_event.is_set():
                break
            time.sleep(1)

    logger.info("[ScannerScheduler] 自动扫描已停止。")


def stop_scanner():
    """停止自动扫描循环。"""
    _stop_event.set()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="仅运行一次")
    args = p.parse_args()
    if args.once:
        run_scan_once()
    else:
        run_scanner_loop()
