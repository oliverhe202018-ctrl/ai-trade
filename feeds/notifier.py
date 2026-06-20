"""
哨兵预警推送模块 (Sentinel Notifier)
预留 HTTP POST Webhook 接口，支持飞书/钉钉/企业微信等机器人推送。
"""
import os
import requests
from datetime import datetime

from core.logger_config import logger

# Webhook 地址从环境变量读取，未配置时静默跳过
WEBHOOK_URL = os.environ.get("NOTIFIER_WEBHOOK_URL", "")


def send_notification(title, content):
    """
    发送预警通知。
    当前实现: HTTP POST JSON 到 Webhook。
    失败时打印警告，不抛异常（避免影响主流程）。
    """
    if not WEBHOOK_URL:
        logger.info(f"[NOTIFIER] Webhook 未配置，跳过推送: {title}")
        return

    payload = {
        "title": title,
        "content": content,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=5)
        resp.raise_for_status()
        logger.info(f"[NOTIFIER] 推送成功: {title}")
    except requests.exceptions.RequestException as e:
        logger.info(f"[NOTIFIER WARN] 推送失败: {e}")
