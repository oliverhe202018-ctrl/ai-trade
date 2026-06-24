import os
import json
from enum import Enum
from pathlib import Path
from core.logger_config import logger

CACHE_DIR = Path("data_cache")
CACHE_DIR.mkdir(exist_ok=True)
STATE_FILE = CACHE_DIR / "system_state.json"

class TradingState(Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    FROZEN = "FROZEN"
    EMERGENCY = "EMERGENCY"

def get_trading_state() -> str:
    """获取系统风控状态，默认为 RUNNING"""
    if not STATE_FILE.exists():
        return TradingState.RUNNING.value
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("state", TradingState.RUNNING.value)
    except Exception as e:
        logger.error(f"读取全局风控状态失败: {e}")
        return TradingState.RUNNING.value

def set_trading_state(state: TradingState):
    """持久化系统风控状态"""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"state": state.value}, f)
        logger.warning(f"🚨 全局风控状态已变更为: {state.value}")
    except Exception as e:
        logger.error(f"写入全局风控状态失败: {e}")
