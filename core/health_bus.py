from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# 动态获取项目根目录
PROJECT_ROOT = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
HEALTH_DIR = PROJECT_ROOT / "data_cache" / "health"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_heartbeat(
    channel: str,
    status: str = "OK",
    source: Optional[str] = None,
    message: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    写入指定通道 heartbeat。
    channel 示例：L1 / L2 / L3
    """
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "channel": channel,
        "status": status,
        "last_seen": utc_now_iso(),
        "source": source,
        "message": message,
        "extra": extra or {},
    }

    target = HEALTH_DIR / f"{channel.lower()}_heartbeat.json"
    tmp = target.with_suffix(".json.tmp")

    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    os.replace(tmp, target)


def read_heartbeat(channel: str) -> Optional[Dict[str, Any]]:
    """
    读取指定通道 heartbeat。
    找不到文件时返回 None。
    """
    target = HEALTH_DIR / f"{channel.lower()}_heartbeat.json"

    if not target.exists():
        return None

    try:
        with target.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None
