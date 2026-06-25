"""
通知服务 — Phase 9.5

统一通知接口，支持 Telegram Bot 和 Generic Webhook。
配置为空时静默跳过，发送失败不中断主流程。

使用:
    from core.notification_service import notify_event
    notify_event("fusion_alert", "高分异动", "sh601318", message="...", level="warning")
"""
import json
import os
import time
import requests
import threading
from datetime import datetime

from core.logger_config import logger

# ── 常量 ─────────────────────────────────────────────────
PHASE = "9.5"
DEDUP_CACHE: dict[str, float] = {}
DEDUP_LOCK = threading.Lock()


# ── 配置读取 ─────────────────────────────────────────────

def _load_notify_config() -> dict:
    """从统一配置读取 notify 节。"""
    try:
        from core.broker import load_config
        return load_config().get("notify", {})
    except Exception:
        return {}


# ── 核心接口 ─────────────────────────────────────────────

def notify_event(
    event_type: str,
    title: str,
    symbol: str = "",
    message: str = "",
    level: str = "info",
    payload: dict | None = None,
) -> bool:
    """
    发送通知事件。

    Args:
        event_type: scanner_signal | fusion_alert | tape_alert | paper_trade | performance_report | system_error
        title:      通知标题
        symbol:     相关股票代码
        message:    通知正文
        level:      info | warning | critical
        payload:    附加数据（用于 webhook JSON body）

    Returns:
        True 如果至少一个通道发送成功
    """
    cfg = _load_notify_config()

    if not cfg.get("enabled", True):
        logger.info(f"[NOTIFY] 通知服务已禁用，跳过: {title}")
        return True  # 不算失败

    # dedup 检查
    if not _dedup_check(event_type, symbol, cfg.get("dedup_window_seconds", 300)):
        logger.info(f"[NOTIFY] 重复通知，跳过: {event_type} {symbol}")
        return True

    sent_any = False

    # Telegram
    token = cfg.get("telegram_token", "")
    chat_id = cfg.get("chat_id", "")
    if token and chat_id:
        if _send_telegram(token, chat_id, title, symbol, message, level):
            sent_any = True

    # Webhook
    webhook = cfg.get("webhook", "")
    if webhook:
        if _send_webhook(webhook, event_type, title, symbol, message, level, payload):
            sent_any = True

    if not token and not webhook:
        logger.info(f"[NOTIFY] 未配置通知通道 (telegram_token/webhook 为空)，跳过: {title}")

    return sent_any


# ── Dedup ────────────────────────────────────────────────

def _dedup_check(event_type: str, symbol: str, window_seconds: int) -> bool:
    """同一 event_type + symbol 在 window 内不重复推送。返回 True=允许发送。"""
    if window_seconds <= 0:
        return True
    key = f"{event_type}:{symbol}"
    now = time.time()
    with DEDUP_LOCK:
        if key in DEDUP_CACHE and (now - DEDUP_CACHE[key]) < window_seconds:
            return False
        DEDUP_CACHE[key] = now
    return True


# ── Telegram ─────────────────────────────────────────────

def _send_telegram(token: str, chat_id: str, title: str, symbol: str, message: str, level: str) -> bool:
    """通过 Telegram Bot API 发送消息。"""
    icon = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}.get(level, "ℹ️")
    symbol_line = f"\n股票：{symbol}" if symbol else ""
    text = (
        f"{icon} 【AI-Trader {title}】\n"
        f"{symbol_line}\n"
        f"{message}\n\n"
        f"级别：{level.upper()}\n"
        f"模式：Paper Trading\n"
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"⚠️ 该提醒仅用于模拟交易和观察，不代表实盘交易建议。"
    )
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=5,
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info(f"[NOTIFY] Telegram 发送成功: {title}")
            return True
        else:
            logger.warning(f"[NOTIFY] Telegram 发送失败: HTTP {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        logger.warning(f"[NOTIFY] Telegram 发送异常: {e}")
        return False


# ── Webhook ──────────────────────────────────────────────

def _send_webhook(
    url: str,
    event_type: str,
    title: str,
    symbol: str,
    message: str,
    level: str,
    payload: dict | None = None,
) -> bool:
    """通过 HTTP POST 发送 Webhook。"""
    body = {
        "event_type": event_type,
        "title": title,
        "symbol": symbol,
        "message": message,
        "level": level,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "phase": PHASE,
        "mode": "paper_trading",
        "payload": payload or {},
    }
    try:
        resp = requests.post(url, json=body, timeout=5)
        if resp.status_code < 400:
            logger.info(f"[NOTIFY] Webhook 发送成功: {title}")
            return True
        else:
            logger.warning(f"[NOTIFY] Webhook 发送失败: HTTP {resp.status_code}")
            return False
    except Exception as e:
        logger.warning(f"[NOTIFY] Webhook 发送异常: {e}")
        return False
