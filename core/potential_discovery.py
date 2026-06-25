import os
import sys
import time
import json
import threading
import tempfile
from pathlib import Path
from datetime import datetime

# 注入项目根目录
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger
from core.fusion_engine import FusionEngine

# LLM 服务加载
try:
    from core.llm_service import call_llm
except ImportError:
    # 兼容现有基建可能存放在外层的情况
    try:
        from llm_service import call_llm
    except ImportError:
        logger.error("[Discovery] 无法导入大模型服务 call_llm")
        call_llm = None

CACHE_DIR = Path(PROJECT_ROOT) / "data_cache"
CANDIDATES_FILE = CACHE_DIR / "market_candidates.json"
PICKS_FILE = CACHE_DIR / "potential_picks.json"
DISCOVERY_RUNS_DIR = CACHE_DIR / "potential_discovery_runs"
os.makedirs(DISCOVERY_RUNS_DIR, exist_ok=True)

_discovery_lock = threading.Lock()

class PotentialDiscoveryEngine:
    def __init__(self):
        self.fusion = FusionEngine()
        
    def _read_candidates(self) -> list:
        if not CANDIDATES_FILE.exists():
            return []
        try:
            with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("candidates", [])
        except Exception as e:
            logger.error(f"[Discovery] 读取候选池失败: {e}")
            return []
            
    def run(self):
        start_time = time.time()
        logger.info("[Discovery] 启动潜力股自动发现引擎...")
        
        stats = {
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "llm_called": False,
            "llm_success": False,
            "fallback_used": False,
            "error_summary": []
        }
        
        # Stage 1 & 2: 读取 Top 100 并降噪至 Top 30
        candidates = self._read_candidates()
        if not candidates:
            stats["error_summary"].append("候选池为空")
            self._save_run_stats(start_time, stats, 0)
            logger.warning("[Discovery] 候选池为空，请先运行 Market Scanner。")
            return
            
        top30 = candidates[:30]
        
        # Stage 3: Fusion Score 复核
        logger.info("[Discovery] 对 Top 30 进行 Fusion Score 深度复核...")
        fusion_results = []
        for c in top30:
            code = c["code"]
            try:
                f_res = self.fusion.evaluate(code)
                c["fusion_score"] = f_res.get("fusion_score", 50)
                c["fusion_reason"] = f_res.get("reason", "无")
            except Exception:
                c["fusion_score"] = 50
                c["fusion_reason"] = "复核异常"
            fusion_results.append(c)
            
        # 根据 fusion score 排序降噪至 Top 10
        fusion_results.sort(key=lambda x: x["fusion_score"], reverse=True)
        top10 = fusion_results[:10]
        
        picks = []
        
        # Stage 4: LLM 批量深度研判
        if not call_llm:
            logger.error("[Discovery] 大模型服务未就绪，触发规则降级处理。")
            stats["error_summary"].append("LLM未就绪")
            picks = self._generate_fallback_picks(top10[:5])
            stats["fallback_used"] = True
        else:
            logger.info("[Discovery] 提交 Top 10 至大模型进行打包深度研判...")
            stats["llm_called"] = True
            prompt = self._build_llm_prompt(top10)
            try:
                # 同步阻塞调用 LLM
                response_text = call_llm(prompt)
                llm_picks = self._parse_llm_response(response_text)
                
                if llm_picks:
                    picks = self._merge_llm_picks(top10, llm_picks)
                    stats["llm_success"] = True
                else:
                    logger.warning("[Discovery] 大模型未返回有效研判结果或解析失败，触发降级机制。")
                    stats["error_summary"].append("LLM解析为空")
                    picks = self._generate_fallback_picks(top10[:5])
                    stats["fallback_used"] = True
            except Exception as e:
                logger.error(f"[Discovery] 大模型研判异常: {e}，触发降级机制。")
                stats["error_summary"].append(f"LLM调用异常: {e}")
                picks = self._generate_fallback_picks(top10[:5])
                stats["fallback_used"] = True
                
        # 原子写入并持久化
        if picks:
            self._save_picks(picks)
            logger.info(f"[Discovery] 潜力股挖掘完成，成功提取 {len(picks)} 只最终标的。")
            
        self._save_run_stats(start_time, stats, len(picks))

    def _generate_fallback_picks(self, fallback_list: list) -> list:
        picks = []
        for s in fallback_list:
            picks.append({
                "code": s["code"],
                "name": s["name"],
                "scanner_score": s.get("score", 0),
                "fusion_score": s.get("fusion_score", 0),
                "potential_score": 80.0, # 降级时给予默认潜力分
                "reason": "系统降级：基于量化指标与异动雷达信号选出。",
                "risk_tags": ["系统降级免测", "需人工复核"],
                "watch_priority": "Medium",
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
        return picks

    def _merge_llm_picks(self, top10: list, llm_picks: list) -> list:
        # 将 LLM 输出转化为标准 Schema
        stock_map = {s["code"]: s for s in top10}
        picks = []
        for p in llm_picks:
            code = p.get("code")
            if code in stock_map:
                s = stock_map[code]
                picks.append({
                    "code": code,
                    "name": p.get("name", s["name"]),
                    "scanner_score": s.get("score", 0),
                    "fusion_score": s.get("fusion_score", 0),
                    "potential_score": p.get("potential_score", 90.0), # 如果LLM未提供则默认90
                    "reason": p.get("reason", p.get("analysis", "无理由")),
                    "risk_tags": p.get("risk_tags", []),
                    "watch_priority": p.get("watch_priority", p.get("priority", "Medium")),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
        return picks

    def _build_llm_prompt(self, top10: list) -> str:
        prompt = "你是一个专业客观的量化策略辅助分析系统。请对以下经过量化法则和多因子引擎层层筛选出的 10 只异动股票进行最后研判。\n"
        prompt += "【严格纪律红线】：你的任务是发现和剖析基本面/技术面潜力，【绝对禁止】输出任何暗示直接交易的词汇，如“立即买入”、“立即卖出”、“买入”、“卖出”、“建仓”、“加仓”、“满仓”、“重仓”、“梭哈”、“保证收益”、“一定上涨”、“必涨”、“无风险”等。如果你认为股票很好，请使用“建议加入观察池”、“极具追踪价值”来代替。\n\n"
        prompt += "请从中挑选出你认为各维度最共振、最值得持续观察的 3~5 只股票，并严格按照以下 JSON 数组格式输出（除了 JSON 字符串，不要输出任何其他说明废话）。\n\n"
        prompt += "[\n  {\n"
        prompt += '    "code": "股票代码",\n'
        prompt += '    "name": "股票名称",\n'
        prompt += '    "potential_score": 95.0,\n'
        prompt += '    "reason": "核心分析理由（50字内，说明资金/趋势/消息为何共振）",\n'
        prompt += '    "risk_tags": ["高位回落风险", "成交异常缩量"],\n'
        prompt += '    "watch_priority": "High / Medium / Low"\n'
        prompt += "  }\n]\n\n"
        prompt += "【Top 10 候选名单与数据】：\n"
        for stock in top10:
            prompt += f"- 代码:{stock['code']} 名称:{stock['name']} 规则打分:{stock.get('score',0)} Fusion异动分:{stock.get('fusion_score',0)} "
            prompt += f"核心指标:[{', '.join(stock.get('factors',[]))}] 异动原因:{stock.get('fusion_reason','')}\n"
        return prompt

    def _parse_llm_response(self, text: str) -> list:
        # 清洗可能带有的 markdown code block 标记
        text = text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
            
        try:
            picks = json.loads(text)
            if isinstance(picks, list):
                # 最后一道防线：代码层暴力拦截与替换违规词汇
                banned_words = ["立即买入", "立即卖出", "买入", "卖出", "建仓", "加仓", "满仓", "重仓", "梭哈", "保证收益", "一定上涨", "必涨", "无风险", "下单"]
                for p in picks:
                    # 兼容不同键名
                    reason_key = "reason" if "reason" in p else "analysis" if "analysis" in p else None
                    if reason_key and isinstance(p[reason_key], str):
                        for word in banned_words:
                            p[reason_key] = p[reason_key].replace(word, "【持续观察】")
                return picks
        except Exception as e:
            logger.error(f"[Discovery] LLM JSON解析失败，原始文本: {text[:100]}... 异常: {e}")
        return []
        
    def _save_picks(self, picks: list):
        try:
            fd, temp_path = tempfile.mkstemp(dir=CACHE_DIR, prefix="potential_picks_", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({
                    "timestamp": time.time(),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "picks": picks
                }, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, PICKS_FILE)
        except Exception as e:
            logger.error(f"[Discovery] 保存潜力股 JSON 失败: {e}")

    def _save_run_stats(self, start_time: float, stats: dict, candidate_count: int):
        try:
            stats["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            stats["duration_ms"] = int((time.time() - start_time) * 1000)
            stats["candidate_count"] = candidate_count
            
            run_file = DISCOVERY_RUNS_DIR / f"run_{int(time.time())}.json"
            with open(run_file, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[Discovery] 保存运行日志失败: {e}")

def run_discovery_async():
    """ 异步执行引擎，防并发拦截 """
    if not _discovery_lock.acquire(blocking=False):
        logger.warning("[Discovery] 潜力挖掘任务正在运行，已拦截并发请求。")
        return False
        
    def _job():
        try:
            engine = PotentialDiscoveryEngine()
            engine.run()
        except Exception as e:
            logger.error(f"[Discovery] 后台挖掘线程崩溃: {e}")
        finally:
            _discovery_lock.release()
            
    t = threading.Thread(target=_job, daemon=True)
    t.start()
    return True
