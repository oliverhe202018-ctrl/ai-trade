import os
import sys
import time
import json
import logging
import argparse
from datetime import datetime
import requests

# 确保能引入 core 模块
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger

CACHE_DIR = os.path.join(PROJECT_ROOT, "data_cache")
INDEX_CACHE_FILE = os.path.join(CACHE_DIR, "index_sh000001.json")

def fetch_sh000001():
    """
    通过新浪公开接口抓取上证指数 (sh000001) 的最新涨跌幅
    格式说明: var hq_str_sh000001="上证指数,3000.0,3010.0,3020.0,...";
    """
    url = "http://hq.sinajs.cn/list=sh000001"
    headers = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    resp = requests.get(url, headers=headers, timeout=5)
    resp.raise_for_status()
    
    text = resp.text.strip()
    if '="' not in text:
        raise ValueError("Invalid response format")
        
    data_part = text.split('="')[1].split('"')[0]
    fields = data_part.split(',')
    
    if len(fields) < 4:
        raise ValueError("Incomplete data fields")
        
    prev_close = float(fields[2])
    current = float(fields[3])
    
    if prev_close <= 0:
        raise ValueError("Invalid prev_close value")
        
    change_pct = (current - prev_close) / prev_close * 100.0
    return round(change_pct, 2)

def update_cache():
    try:
        change_pct = fetch_sh000001()
        data = {
            "ts": int(time.time()),
            "change_pct": change_pct,
            "source": "index_cache_updater",
            "updated_at": datetime.now().isoformat()
        }
        
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp_file = INDEX_CACHE_FILE + ".tmp"
        
        # 原子写入：先写入临时文件，再做文件替换
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        os.replace(tmp_file, INDEX_CACHE_FILE)
        logger.info(f"[INDEX_CACHE_UPDATE] sh000001 change_pct={change_pct}% updated successfully.")
    except Exception as e:
        logger.error(f"[INDEX_CACHE_UPDATE_FAIL] Failed to update index cache: {e}")

def main():
    import os
    env_interval = os.environ.get("INDEX_CACHE_UPDATE_INTERVAL_SECONDS", "")
    default_interval = int(env_interval) if env_interval.isdigit() else 300

    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=default_interval, help="Update interval in seconds")
    args = parser.parse_args()
    
    logger.info(f"Starting Index Cache Updater... Update interval: {args.interval}s")
    while True:
        update_cache()
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
