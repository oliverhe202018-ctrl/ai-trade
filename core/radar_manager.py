import os
import sys
import time
import json
import threading
from pathlib import Path

# 注入项目根目录以处理模块引入问题
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger
from core.fusion_engine import FusionEngine

CACHE_DIR = Path(PROJECT_ROOT) / "data_cache"
WATCHLIST_FILE = CACHE_DIR / "custom_watchlist.json"
RADAR_ALERTS_FILE = CACHE_DIR / "radar_alerts.json"

class RadarManager:
    def __init__(self):
        self.engine = FusionEngine()
        self.running = False
        self._thread = None

    def _load_watchlist(self):
        if not WATCHLIST_FILE.exists():
            return []
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    # 兼容新旧格式
                    if len(data) > 0 and isinstance(data[0], str):
                        return data
                    elif len(data) > 0 and isinstance(data[0], dict):
                        return [item.get("code") for item in data if item.get("code")]
        except Exception as e:
            logger.error(f"[RadarManager] 解析 watchlist 失败: {e}")
        return []

    def scan_once(self):
        symbols = self._load_watchlist()
        if not symbols:
            return

        alerts = []
        for sym in symbols:
            try:
                res = self.engine.evaluate(sym)
                # 触发阈值条件：综合得分 > 70，或者单项(资金/消息)极端异动 > 85
                if res["fusion_score"] > 70 or res["fund_score"] > 85 or res["message_score"] > 85:
                    if res["reason"] == "表现平稳":
                        res["reason"] = "多因子综合共振异动"
                    alerts.append(res)
            except Exception as e:
                logger.error(f"[RadarManager] 扫描标的 {sym} 异常: {e}")
                
        # 按分数倒序排列
        alerts.sort(key=lambda x: x["fusion_score"], reverse=True)
        
        # 写入警报文件，供 Copilot 的 AlertProvider 读取
        try:
            with open(RADAR_ALERTS_FILE, "w", encoding="utf-8") as f:
                json.dump({"timestamp": time.time(), "alerts": alerts}, f, ensure_ascii=False, indent=2)
            logger.info(f"[RadarManager] 雷达扫描完成，共发现 {len(alerts)} 个异动标的。")
        except Exception as e:
            logger.error(f"[RadarManager] 保存雷达扫描结果失败: {e}")

    def _loop(self, interval: int):
        while self.running:
            try:
                self.scan_once()
            except Exception as e:
                logger.error(f"[RadarManager] 雷达扫描崩溃: {e}")
            time.sleep(interval)

    def start(self, interval_seconds: int = 180):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, args=(interval_seconds,), daemon=True)
        self._thread.start()
        logger.info(f"[RadarManager] 异动雷达已启动，轮询间隔: {interval_seconds}秒")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            logger.info("[RadarManager] 异动雷达已安全停止。")

# 提供一个便捷的全局启动函数供外部调用
_global_radar = None

def start_radar_daemon():
    global _global_radar
    if _global_radar is None:
        _global_radar = RadarManager()
        _global_radar.start(interval_seconds=180) # 3分钟扫一次
