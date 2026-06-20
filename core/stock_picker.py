"""
AI 选股参考 - 使用实时行情数据
国内网络环境下使用腾讯/新浪接口，失败则模拟
"""
import json
import sys
import os

# 将项目根目录加入 sys.path，确保 feeds 模块可被引用
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from feeds.market_data import get_realtime_quotes
from core.logger_config import logger

if __name__ == "__main__":
    quotes = get_realtime_quotes()
    # 只输出 JSON 到 stdout，日志到 stderr
    logger.info(json.dumps(quotes, ensure_ascii=False, indent=2))
