import os
import sys
import time
import json
import pandas as pd
from datetime import datetime
from pathlib import Path
import threading
import tempfile

# 注入项目根目录
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger

try:
    from xtquant import xtdata
except ImportError:
    xtdata = None

CACHE_DIR = Path(PROJECT_ROOT) / "data_cache"
DAILY_CACHE_FILE = CACHE_DIR / "market_scanner_daily.json"
CANDIDATES_FILE = CACHE_DIR / "market_candidates.json"
RADAR_ALERTS_FILE = CACHE_DIR / "radar_alerts.json"
SCANNER_RUNS_DIR = CACHE_DIR / "market_scanner_runs"
os.makedirs(SCANNER_RUNS_DIR, exist_ok=True)

# 全局扫描锁
_scan_lock = threading.Lock()

class MarketScanner:
    def __init__(self):
        self.today_str = datetime.now().strftime("%Y%m%d")
        
    def _get_qmt_codes(self) -> list:
        if not xtdata:
            return []
        sh = xtdata.get_stock_list_in_sector('上证A股')
        sz = xtdata.get_stock_list_in_sector('深证A股')
        return sh + sz

    def _get_std_code(self, qmt_code: str) -> str:
        if qmt_code.endswith(".SH"):
            return f"sh{qmt_code[:6]}"
        if qmt_code.endswith(".SZ"):
            return f"sz{qmt_code[:6]}"
        return qmt_code
        
    def _get_radar_alert_set(self) -> set:
        """从雷达警报文件中快速加载异动标的池"""
        alert_set = set()
        try:
            if RADAR_ALERTS_FILE.exists():
                with open(RADAR_ALERTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data.get("alerts", []):
                        alert_set.add(item.get("code"))
        except Exception:
            pass
        return alert_set

    def _build_daily_static_cache(self, codes: list) -> dict:
        """ 批量拉取历史 K 线并缓存冷数据 (MA 趋势、20日新高、连续上涨天数) """
        if DAILY_CACHE_FILE.exists():
            try:
                with open(DAILY_CACHE_FILE, "r", encoding="utf-8") as f:
                    cache_data = json.load(f)
                    if cache_data.get("date") == self.today_str:
                        logger.info("[MarketScanner] 命中当日冷数据缓存，跳过历史 K 线下载。")
                        return cache_data.get("data", {})
            except Exception:
                pass
                
        logger.info("[MarketScanner] 开始全市场批量下载历史 K 线 (耗时操作，每日仅执行一次)...")
        static_data = {}
        
        try:
            # 下载所有标的的最新 30 天日线数据到本地
            xtdata.download_history_data2(stock_list=codes, period='1d', start_time='', end_time='')
            
            # 每批 1000 只股票拉取，避免爆内存
            batch_size = 1000
            for i in range(0, len(codes), batch_size):
                batch_codes = codes[i:i+batch_size]
                data_dict = xtdata.get_market_data_ex(
                    field_list=['close', 'high', 'low'], 
                    stock_list=batch_codes, 
                    period='1d', 
                    count=30
                )
                for code, df in data_dict.items():
                    if df is None or df.empty or len(df) < 20:
                        continue
                        
                    closes = df['close'].values
                    highs = df['high'].values
                    
                    ma5 = closes[-5:].mean()
                    ma10 = closes[-10:].mean()
                    ma20 = closes[-20:].mean()
                    
                    last_close = closes[-1]
                    trend_score = 0
                    if last_close > ma5 > ma10 > ma20:
                        trend_score = 100
                    elif last_close > ma5 > ma10:
                        trend_score = 60
                    elif last_close > ma20:
                        trend_score = 30
                        
                    max_high_20 = highs[-20:].max()
                    new_high_score = 100 if last_close >= max_high_20 * 0.98 else 0
                    
                    up_days = 0
                    for j in range(len(closes)-1, 0, -1):
                        if closes[j] > closes[j-1]:
                            up_days += 1
                        else:
                            break
                            
                    up_days_score = 0
                    if 1 <= up_days <= 3:
                        up_days_score = 100
                    elif up_days > 3:
                        up_days_score = 50 
                        
                    static_data[code] = {
                        "trend_score": trend_score,
                        "new_high_score": new_high_score,
                        "up_days": up_days,
                        "up_days_score": up_days_score
                    }
                    
            with open(DAILY_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"date": self.today_str, "data": static_data}, f)
        except Exception as e:
            logger.error(f"[MarketScanner] 初始化冷数据缓存失败: {e}")
            
        return static_data

    def scan(self):
        start_time = time.time()
        stats = {
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_codes": 0,
            "success_count": 0,
            "failed_count": 0,
            "cold_cache_used": False,
            "error_summary": []
        }
        
        if not xtdata:
            stats["error_summary"].append("xtdata 不可用")
            self._save_run_stats(start_time, stats, 0)
            logger.error("[MarketScanner] xtdata 不可用。")
            return
            
        codes = self._get_qmt_codes()
        stats["total_codes"] = len(codes)
        if not codes:
            stats["error_summary"].append("获取股票列表为空")
            self._save_run_stats(start_time, stats, 0)
            logger.error("[MarketScanner] 获取股票列表为空。")
            return
            
        logger.info(f"[MarketScanner] 引擎启动，扫描市场标的总数: {len(codes)}")
        
        # 1. 获取冷数据
        cold_start_time = time.time()
        static_data = self._build_daily_static_cache(codes)
        if time.time() - cold_start_time < 2.0:
            stats["cold_cache_used"] = True
        
        # 2. 获取异动雷达数据
        alert_set = self._get_radar_alert_set()
        
        # 3. 订阅并获取热数据全量快照
        logger.info("[MarketScanner] 正在捕获毫秒级全市场 Tick 快照...")
        tick_start = time.time()
        ticks = xtdata.get_full_tick(codes)
        tick_ms = int((time.time() - tick_start) * 1000)
        
        candidates = []
        
        for code in codes:
            tick = ticks.get(code)
            if not tick: continue
            
            static = static_data.get(code, {})
            if not static: 
                stats["failed_count"] += 1
                continue # 无历史K线过滤
            
            last_price = tick.get("lastPrice", 0)
            pre_close = tick.get("preClose", 0)
            if pre_close == 0: 
                stats["failed_count"] += 1
                continue
            
            change_pct = ((last_price / pre_close) - 1.0) * 100
            
            # 过滤一字跌停或停牌
            if change_pct < -9.8: 
                stats["failed_count"] += 1
                continue
            
            # 过滤退市和停牌 (如果没有成交量，或者状态不对)
            if tick.get("volume", 0) == 0: 
                stats["failed_count"] += 1
                continue
            
            # 涨幅打分
            if change_pct >= 9.8:
                pct_score = 30 # 已涨停，买入机会少
            elif 2.0 <= change_pct <= 8.0:
                pct_score = 100
            elif 0 < change_pct < 2.0:
                pct_score = 60
            else:
                pct_score = 0
                
            # 活跃度打分
            turnover = tick.get("turnoverRatio", 0)
            vol_ratio = tick.get("volRatio", 0)
            
            vol_score = 0
            if 5.0 <= turnover <= 20.0 and vol_ratio > 1.5:
                vol_score = 100
            elif turnover > 3.0 and vol_ratio > 1.0:
                vol_score = 50
                
            # 提取冷数据打分
            trend_s = static.get("trend_score", 0)
            new_high_s = static.get("new_high_score", 0)
            up_s = static.get("up_days_score", 0)
            
            # 异动打分
            std_code = self._get_std_code(code)
            alert_score = 100 if std_code in alert_set else 0
            
            # 综合评分生成 (总权 1.0)
            total_score = (pct_score * 0.2) + (vol_score * 0.2) + (trend_s * 0.2) + (new_high_s * 0.2) + (up_s * 0.1) + (alert_score * 0.1)
            
            if total_score > 65: # 仅提取高分标的
                stock_name = tick.get("stockName", code)
                # 过滤 ST 与 退市
                if "ST" in stock_name or "退" in stock_name: 
                    stats["failed_count"] += 1
                    continue
                
                # 构建趋势标签
                trend_flags = []
                if trend_s == 100: trend_flags.append("多头排列")
                if new_high_s == 100: trend_flags.append("创20日新高")
                if up_s == 100: trend_flags.append(f"连涨{static.get('up_days', 0)}天")
                if alert_score == 100: trend_flags.append("近期异动")
                
                candidates.append({
                    "code": std_code,
                    "name": stock_name,
                    "score": round(total_score, 1),
                    "factors": [
                        f"涨幅: {round(change_pct,2)}%",
                        f"换手: {round(turnover,2)}%",
                        f"量比: {round(vol_ratio,2)}",
                        f"连涨: {static.get('up_days', 0)}天"
                    ],
                    "change_pct": round(change_pct, 2),
                    "turnover": round(turnover, 2),
                    "volume_ratio": round(vol_ratio, 2),
                    "trend_flags": trend_flags,
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                
        # 结果排序，取 Top 100
        candidates.sort(key=lambda x: x["score"], reverse=True)
        top100 = candidates[:100]
        
        # 原子写入机制 (写临时文件后替换，防止损坏原文件)
        try:
            fd, temp_path = tempfile.mkstemp(dir=CACHE_DIR, prefix="market_candidates_", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"timestamp": time.time(), "cost_ms": int((time.time()-start_time)*1000), "candidates": top100}, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, CANDIDATES_FILE)
        except Exception as e:
            stats["error_summary"].append(f"保存扫描结果失败: {e}")
            logger.error(f"[MarketScanner] 保存扫描结果失败: {e}")
            
        stats["success_count"] = stats["total_codes"] - stats["failed_count"]
        self._save_run_stats(start_time, stats, len(top100), tick_ms)
        logger.info(f"[MarketScanner] 扫描完美结束。耗时: {time.time() - start_time:.2f}s，有效入库标的: {len(top100)} 只")
        return top100

    def _save_run_stats(self, start_time: float, stats: dict, candidate_count: int, qmt_tick_ms: int = 0):
        try:
            stats["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            stats["duration_ms"] = int((time.time() - start_time) * 1000)
            stats["qmt_tick_ms"] = qmt_tick_ms
            stats["candidate_count"] = candidate_count
            
            run_file = SCANNER_RUNS_DIR / f"run_{int(time.time())}.json"
            with open(run_file, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[MarketScanner] 保存运行日志失败: {e}")

def run_scanner_async():
    """ 异步触发全市场扫描，防止阻塞主线程，内置防并发锁 """
    if not _scan_lock.acquire(blocking=False):
        logger.warning("[MarketScanner] 扫描任务已在运行中，跳过本次请求。")
        return False
        
    def _job():
        try:
            scanner = MarketScanner()
            scanner.scan()
        except Exception as e:
            logger.error(f"MarketScanner Background Error: {e}")
        finally:
            _scan_lock.release()
            
    t = threading.Thread(target=_job, daemon=True)
    t.start()
    return True
