"""
QMT 实盘保护门 — Phase 9.5

在所有 QMT 连接点之前调用 check_qmt_guard()。
如果 broker.qmt_enabled != true，则禁止任何 QMT 调用。
"""
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger

QMT_FORBIDDEN_MSG = (
    "[QMT GUARD] live trading is disabled in Phase 9.5. "
    "设置 broker.qmt_enabled: true 才能启用实盘。"
)


def check_qmt_guard() -> bool:
    """
    检查是否允许 QMT 实盘操作。

    返回 True 表示允许，False 表示被拦截。
    """
    try:
        from core.broker import load_config
        cfg = load_config()
        broker_cfg = cfg.get("broker", {})
        if broker_cfg.get("qmt_enabled", False) is True:
            logger.info("[QMT GUARD] QMT 实盘已启用 (qmt_enabled=true)")
            return True
    except Exception as e:
        logger.error(f"[QMT GUARD] 配置读取失败，默认禁止: {e}")

    logger.warning(QMT_FORBIDDEN_MSG)
    return False


def enforce_qmt_disabled():
    """强制阻止 QMT 调用，如果不允许则抛出 PermissionError。"""
    if not check_qmt_guard():
        raise PermissionError(QMT_FORBIDDEN_MSG)
