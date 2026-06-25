import os
import sys
import time
import json
import threading
import tempfile
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger

try:
    from xtquant import xtdata
except ImportError:
    xtdata = None

CACHE_DIR = Path(PROJECT_ROOT) / "data_cache"
CANDIDATES_FILE = CACHE_DIR / "market_candidates.json"
TRACKING_FILE = CACHE_DIR / "main_money_tracking.json"

LARGE_ORDER_AMOUNT_THRESHOLD = 1_000_000  # 100万判定为大单
STATE_TTL_SECONDS = 30  # 状态缓存存活期

_tape_lock = threading.Lock()
_stop_event = threading.Event()

class TapeReader:
    def __init__(self):
        self.state_cache = {}
        self.tracking_results = {}
        
    def _read_top30(self):
        if not CANDIDATES_FILE.exists():
            return []
        try:
            with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cands = data.get("candidates", [])
                return cands[:30]
        except:
            return []

    def _init_tracking_item(self, code, name):
        if code not in self.tracking_results:
            self.tracking_results[code] = {
                "code": code,
                "name": name,
                "last_price": 0,
                "estimated_active_buy_amount": 0,
                "estimated_active_sell_amount": 0,
                "estimated_large_order_net_inflow": 0,
                "large_order_event_count": 0,
                "order_book_imbalance": 0.0,
                "main_money_proxy_score": 50,
                "sample_count": 0,
                "risk_note": "基于L1快照估算，不等同于真实逐笔主力资金",
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

    def _cleanup_stale_cache(self, current_time):
        stale_keys = [k for k, v in self.state_cache.items() if current_time - v["last_seen"] > STATE_TTL_SECONDS]
        for k in stale_keys:
            del self.state_cache[k]

    def _calculate_proxy_score(self, item):
        score = 50.0
        
        # 1. 委比方向 (±10分)
        score += item["order_book_imbalance"] * 10
        
        # 2. 主动买卖差 (±20分)
        buy_amt = item["estimated_active_buy_amount"]
        sell_amt = item["estimated_active_sell_amount"]
        total_amt = buy_amt + sell_amt
        if total_amt > 0:
            active_imbalance = (buy_amt - sell_amt) / total_amt
            score += active_imbalance * 20
            
        # 3. 大单净流入方向 (±20分)
        net_inflow = item["estimated_large_order_net_inflow"]
        if net_inflow > 0:
            score += min(20, (net_inflow / 10_000_000) * 20)  # 1000万封顶加20分
        elif net_inflow < 0:
            score -= min(20, (abs(net_inflow) / 10_000_000) * 20)
            
        return max(0, min(100, int(score)))

    def tick(self):
        top30 = self._read_top30()
        if not top30:
            return
            
        codes = [s["code"] for s in top30]
        code_to_name = {s["code"]: s["name"] for s in top30}
        
        if not xtdata:
            logger.warning("[TapeReader] xtdata 不可用，跳过本轮分析。")
            return
            
        ticks = xtdata.get_full_tick(codes)
        current_time = time.time()
        self._cleanup_stale_cache(current_time)
        
        for code in codes:
            tick = ticks.get(code)
            if not tick:
                continue
                
            self._init_tracking_item(code, code_to_name[code])
            item = self.tracking_results[code]
            
            last_price = tick.get("lastPrice", 0)
            volume = tick.get("volume", 0)
            amount = tick.get("amount", 0)
            ask_prices = tick.get("askPrice", [0]*5)
            bid_prices = tick.get("bidPrice", [0]*5)
            ask_vols = tick.get("askVol", [0]*5)
            bid_vols = tick.get("bidVol", [0]*5)
            
            # Step 3: 计算盘口失衡
            sum_bid = sum(bid_vols)
            sum_ask = sum(ask_vols)
            item["order_book_imbalance"] = round((sum_bid - sum_ask) / max(sum_bid + sum_ask, 1), 3)
            item["last_price"] = last_price
            
            # Step 4 & 5: 状态机与增量判定
            state = self.state_cache.get(code)
            if state:
                delta_volume = volume - state["last_volume"]
                delta_amount = amount - state["last_amount"]
                
                if delta_volume > 0 and delta_amount > 0:
                    item["sample_count"] += 1
                    
                    is_large = (delta_amount >= LARGE_ORDER_AMOUNT_THRESHOLD)
                    
                    if last_price >= ask_prices[0] and ask_prices[0] > 0:
                        item["estimated_active_buy_amount"] += delta_amount
                        if is_large:
                            item["estimated_large_order_net_inflow"] += delta_amount
                            item["large_order_event_count"] += 1
                    elif last_price <= bid_prices[0] and bid_prices[0] > 0:
                        item["estimated_active_sell_amount"] += delta_amount
                        if is_large:
                            item["estimated_large_order_net_inflow"] -= delta_amount
                            item["large_order_event_count"] += 1
            
            # 更新状态
            self.state_cache[code] = {
                "last_volume": volume,
                "last_amount": amount,
                "last_price": last_price,
                "last_seen": current_time
            }
            
            # Step 6: 计算分数
            item["main_money_proxy_score"] = self._calculate_proxy_score(item)
            item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self._save_tracking_results()

    def _save_tracking_results(self):
        try:
            items_list = list(self.tracking_results.values())
            # 按 proxy_score 降序
            items_list.sort(key=lambda x: x["main_money_proxy_score"], reverse=True)
            
            out_data = {
                "timestamp": time.time(),
                "data_level": "L1_PROXY",
                "precision": "estimated",
                "universe_source": "market_candidates_top30",
                "items": items_list
            }
            
            fd, temp_path = tempfile.mkstemp(dir=CACHE_DIR, prefix="main_money_", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(out_data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, TRACKING_FILE)
        except Exception as e:
            logger.error(f"[TapeReader] 保存盘口数据失败: {e}")

def run_tape_reader_loop():
    logger.info("[TapeReader] 后台盘口阅读器引擎已启动...")
    reader = TapeReader()
    try:
        while not _stop_event.is_set():
            try:
                reader.tick()
            except Exception as e:
                logger.error(f"[TapeReader] Tick 执行异常: {e}")
            
            # 使用小步长 sleep 以快速响应退出信号
            for _ in range(30):
                if _stop_event.is_set():
                    break
                time.sleep(0.1)
    finally:
        if _tape_lock.locked():
            try:
                _tape_lock.release()
            except Exception as e:
                logger.error(f"[TapeReader] 释放锁异常: {e}")
        logger.info("[TapeReader] 引擎安全退出，锁已释放。")

def start_tape_reader_async():
    if not _tape_lock.acquire(blocking=False):
        logger.warning("[TapeReader] 引擎已经在运行或正在退出，跳过启动。")
        return False
        
    _stop_event.clear()
    
    t = threading.Thread(target=run_tape_reader_loop, daemon=True)
    t.start()
    return True

def stop_tape_reader():
    if _tape_lock.locked():
        _stop_event.set()
        logger.info("[TapeReader] 已发送停止信号，等待后台线程自动退出并释放锁。")
