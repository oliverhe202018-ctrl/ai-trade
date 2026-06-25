import os
import time
import json
import sqlite3
from pathlib import Path
import pandas as pd
from datetime import datetime, timedelta
import sys

# 注入项目根目录以处理模块引入问题
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger
from feeds.qmt_market_provider import QMTMarketProvider

CACHE_DIR = Path(PROJECT_ROOT) / "data_cache"
DB_PATH = CACHE_DIR / "news_events.db"

class FusionEngine:
    def __init__(self):
        self.qmt = None
        try:
            self.qmt = QMTMarketProvider()
        except Exception as e:
            logger.warning(f"[FusionEngine] QMT未能完全就绪，资金面和趋势面将降级为中性: {e}")

    def score_message(self, symbol: str) -> float:
        """ 消息面 (Message Score - 25%) """
        if not DB_PATH.exists():
            return 50.0
            
        try:
            with sqlite3.connect(DB_PATH, timeout=5.0) as conn:
                cursor = conn.cursor()
                yesterday = (datetime.now() - timedelta(days=1)).isoformat()
                # 模糊匹配 symbol
                cursor.execute(
                    "SELECT sentiment, confidence FROM news_events WHERE event_time >= ? AND symbols LIKE ?", 
                    (yesterday, f'%"{symbol}"%')
                )
                rows = cursor.fetchall()
                
                if not rows:
                    return 50.0 # 默认中性
                
                total_score = 0
                total_weight = 0
                for sentiment, confidence in rows:
                    # sentiment 范围通常是 -1.0(极差) 到 1.0(极好)，将其映射到 0-100
                    base = (sentiment + 1.0) * 50
                    total_score += base * confidence
                    total_weight += confidence
                
                if total_weight == 0:
                    return 50.0
                return min(100.0, max(0.0, total_score / total_weight))
        except Exception as e:
            logger.error(f"[FusionEngine] 消息面算分异常: {e}")
            return 50.0

    def score_fund_flow(self, symbol: str) -> float:
        """ 资金面 (Fund Flow Score - 30%) """
        if not self.qmt:
            return 50.0
            
        try:
            snap = self.qmt.get_market_snapshot([symbol]).get(symbol)
            if not snap:
                return 50.0
                
            ask_vols = sum(snap.get("askVol", []))
            bid_vols = sum(snap.get("bidVol", []))
            total_vol = ask_vols + bid_vols
            
            # 如果买盘资金更强(下方托单量大)，得分更高
            if total_vol == 0:
                score = 50.0
            else:
                imbalance = bid_vols / total_vol
                score = imbalance * 100.0
                
            # 如果当日换手率异常放大，给予 1.2 倍资金活跃度加权
            turnover = snap.get("turnover_rate", 0.0)
            if turnover > 5.0:
                score = score * 1.2
                
            return min(100.0, score)
        except Exception as e:
            return 50.0

    def score_trend(self, symbol: str) -> float:
        """ 趋势面 (Trend Score - 25%) """
        if not self.qmt:
            return 50.0
            
        try:
            # 取最近 10 天日线
            bars = self.qmt.get_bars(symbol, period='1d', count=10)
            if bars is None or bars.empty:
                return 50.0
                
            closes = bars['close'].values
            if len(closes) < 5:
                return 50.0
                
            ma5 = closes[-5:].mean()
            last_close = closes[-1]
            
            # 最简单的多空强弱判断
            if last_close > ma5 * 1.05:
                return 90.0 # 强势多头
            elif last_close > ma5:
                return 70.0 # 温和多头
            elif last_close > ma5 * 0.95:
                return 40.0 # 温和空头
            else:
                return 20.0 # 弱势空头
        except Exception as e:
            return 50.0

    def score_ai_signal(self, symbol: str) -> float:
        """ AI 信号面 (AI Signal Score - 20%) """
        score = 50.0
        try:
            # 尝试读取深度报告大模型的打分
            analysis_file = CACHE_DIR / "analysis" / f"{symbol.replace('.', '_')}.json"
            if analysis_file.exists():
                with open(analysis_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    ai_score = data.get("ai_decision", {}).get("score", 50)
                    score = float(ai_score)
        except Exception:
            pass
        return min(100.0, max(0.0, score))

    def evaluate(self, symbol: str) -> dict:
        """
        生成全维度的综合雷达打分
        """
        msg_score = self.score_message(symbol)
        fund_score = self.score_fund_flow(symbol)
        trend_score = self.score_trend(symbol)
        ai_score = self.score_ai_signal(symbol)
        
        # 权重映射公式
        fusion_score = (msg_score * 0.25) + (fund_score * 0.30) + (trend_score * 0.25) + (ai_score * 0.20)
        
        reason = []
        if msg_score > 80: reason.append("消息面重大利好")
        if fund_score > 80: reason.append("主力资金异动流入")
        if trend_score > 80: reason.append("日线趋势强势上攻")
        if ai_score > 80: reason.append("AI模型高分推荐")
        
        return {
            "code": symbol,
            "fusion_score": round(fusion_score, 2),
            "message_score": round(msg_score, 2),
            "fund_score": round(fund_score, 2),
            "trend_score": round(trend_score, 2),
            "ai_score": round(ai_score, 2),
            "reason": " | ".join(reason) if reason else "表现平稳",
            "timestamp": time.time()
        }
