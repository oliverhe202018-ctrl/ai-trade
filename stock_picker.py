"""
AI 选股参考 - 使用实时行情数据
国内网络环境下使用腾讯/新浪接口，失败则模拟
"""
import json
import sys
import os

# 确保能导入同目录下的 market_data
sys.path.insert(0, os.path.dirname(__file__))
from market_data import get_realtime_quotes

if __name__ == "__main__":
    quotes = get_realtime_quotes()
    # 只输出 JSON 到 stdout，日志到 stderr
    print(json.dumps(quotes, ensure_ascii=False, indent=2))
