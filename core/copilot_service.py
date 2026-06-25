import os
import json
import time
import requests
from datetime import datetime
from pathlib import Path
import re

# 注入项目根目录以处理模块引入问题
import sys
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..')) 
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.logger_config import logger

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080")
CACHE_DIR = Path(PROJECT_ROOT) / "data_cache"
COPILOT_LOG_DIR = CACHE_DIR / "copilot_logs"
COPILOT_LOG_DIR.mkdir(parents=True, exist_ok=True)

def classify_intent(query: str) -> str:
    """
    对用户问题进行分类，控制上下文注入。
    """
    q = query.lower()
    if any(k in q for k in ["持仓", "仓位", "账户", "资产", "资金", "盈亏", "风险"]):
        return "PORTFOLIO"
    elif any(k in q for k in ["分析", "股票", "基本面", "走势", "个股", "茅台"]) or ("代码" in q):
        return "STOCK"
    elif any(k in q for k in ["新闻", "消息", "热点", "发生", "大事件", "情绪", "今日"]):
        return "NEWS"
    elif any(k in q for k in ["系统", "健康", "状态", "离线", "在线", "为什么", "没有下单", "不买"]):
        return "SYSTEM"
    elif any(k in q for k in ["信号", "下单", "买入", "卖出", "指示", "交易", "触发"]):
        return "SIGNAL"
    elif any(k in q for k in ["潜力", "值得关注", "新机会", "好机会", "精选", "金股"]):
        return "POTENTIAL"
    elif any(k in q for k in ["主力", "资金", "大单", "资金流", "托单", "压单"]):
        return "MAIN_MONEY"
    else:
        return "GENERAL"

def _load_json_safe(path: Path):
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

class ContextProvider:
    def provide(self, intent: str, query: str, app_state: dict) -> str:
        return ""

class PortfolioProvider(ContextProvider):
    def provide(self, intent: str, query: str, app_state: dict) -> str:
        if intent not in ["PORTFOLIO", "GENERAL", "SYSTEM"]:
            return ""
        portfolio = _load_json_safe(CACHE_DIR / "live_portfolio.json")
        context = ["【当前持仓状态】", f"总资产: {portfolio.get('total_equity', 0)}", f"可用现金: {portfolio.get('cash', 0)}"]
        pos = portfolio.get("positions", {})
        if pos:
            pos_str = []
            for code, p in pos.items():
                pos_str.append(f"- {p.get('name', code)}: {p.get('shares', 0)}股, 盈亏: {p.get('profit', 0)}, 当前价值: {p.get('current_value', 0)}")
            context.append("\n".join(pos_str))
        else:
            context.append("当前无持仓。")
        return "\n".join(context)

class NewsProvider(ContextProvider):
    def provide(self, intent: str, query: str, app_state: dict) -> str:
        if intent not in ["NEWS", "GENERAL", "STOCK"]:
            return ""
        news_data = _load_json_safe(CACHE_DIR / "news_sentiment_cache.json")
        context = ["【近期市场情绪与新闻】", f"宏观情绪指数: {news_data.get('macro_sentiment', 0)}"]
        hot = news_data.get('hot_sectors', [])
        if hot:
            context.append(f"当前热点板块: {hot}")
        
        raw_news = news_data.get('news_list', news_data.get('raw_news', []))
        if raw_news:
            news_strs = []
            for item in raw_news[:30]: 
                if isinstance(item, dict):
                    title = item.get('title', item.get('content', ''))
                    sentiment = item.get('sentiment', '')
                    news_strs.append(f"- {title} (情感:{sentiment})")
                else:
                    news_strs.append(f"- {str(item)[:100]}")
            context.append("\n".join(news_strs))
        return "\n".join(context)

class SignalProvider(ContextProvider):
    def provide(self, intent: str, query: str, app_state: dict) -> str:
        if intent not in ["SIGNAL", "SYSTEM", "GENERAL"]:
            return ""
        order_queue = app_state.get("order_queue", [])
        context = ["【最近 AI 交易信号】"]
        if order_queue:
            sigs = []
            for o in list(order_queue)[:15]:
                sigs.append(f"- {o.get('recv_time', '')}: {o.get('action', '')} {o.get('code', '')} (数量:{o.get('quantity', 0)}), 理由: {o.get('reason', '')}")
            context.append("\n".join(sigs))
        else:
            context.append("暂无最近的 AI 交易信号。")
        return "\n".join(context)

class SystemProvider(ContextProvider):
    def provide(self, intent: str, query: str, app_state: dict) -> str:
        from core.trading_state import get_trading_state
        state = get_trading_state()
        return f"【系统风控状态】: {state}"

class AlertProvider(ContextProvider):
    def provide(self, intent: str, query: str, app_state: dict) -> str:
        if intent not in ["NEWS", "GENERAL", "STOCK", "SIGNAL"]:
            return ""
        context = ["【异动雷达扫描结果】"]
        radar_file = CACHE_DIR / "radar_alerts.json"
        radar_data = _load_json_safe(radar_file)
        alerts = radar_data.get("alerts", [])
        if alerts:
            for alert in alerts[:5]:
                context.append(f"- 异动标的: {alert.get('code', '')}, 综合打分: {alert.get('fusion_score', 0)}, 原因: {alert.get('reason', '')}")
        else:
            context.append("暂无近期警报。")
        return "\n".join(context)

class PotentialProvider(ContextProvider):
    def provide(self, intent: str, query: str, app_state: dict) -> str:
        if intent not in ["POTENTIAL", "GENERAL", "STOCK"]:
            return ""
        picks_file = CACHE_DIR / "potential_picks.json"
        picks_data = _load_json_safe(picks_file)
        picks = picks_data.get("picks", [])
        context = ["【深度挖掘潜力股清单】"]
        if picks:
            for p in picks[:5]: # 只喂前5个防止token超限
                context.append(f"- 标的: {p.get('name', '')} ({p.get('code', '')}) [优先级: {p.get('watch_priority', '')}]\n  综合分: {p.get('potential_score', 0)}\n  理由: {p.get('reason', '')}\n  风险: {', '.join(p.get('risk_tags', []))}")
        else:
            context.append("当前暂无系统挖掘的潜力股清单。请先运行潜力股发现引擎。")
        return "\n".join(context)

class MainMoneyProvider(ContextProvider):
    def provide(self, intent: str, query: str, app_state: dict) -> str:
        if intent not in ["MAIN_MONEY", "GENERAL", "STOCK"]:
            return ""
        tracking_file = CACHE_DIR / "main_money_tracking.json"
        data = _load_json_safe(tracking_file)
        items = data.get("items", [])
        
        context = ["【主力资金盘口追踪 (基于 L1 快照估算，非绝对真实主力)】"]
        if items:
            for it in items[:5]:
                context.append(f"- {it.get('name', '')} ({it.get('code', '')}): 估算大单净流 {it.get('estimated_large_order_net_inflow', 0)}, 委比 {it.get('order_book_imbalance', 0)}, 资金代理分 {it.get('main_money_proxy_score', 0)}")
        else:
            context.append("当前后台未捕获到主力资金流水，请确认盘口追踪器是否开启。")
        return "\n".join(context)

def build_dynamic_context(intent: str, query: str, app_state: dict) -> tuple[str, list]:
    providers = [SystemProvider(), PortfolioProvider(), NewsProvider(), SignalProvider(), AlertProvider(), PotentialProvider(), MainMoneyProvider()]
    context_parts = []
    used_providers = []
    
    for provider in providers:
        part = provider.provide(intent, query, app_state)
        if part:
            context_parts.append(part)
            used_providers.append(provider.__class__.__name__)
            
    final_context = "\n\n".join(context_parts)[:4000]
    return final_context, used_providers

def apply_prompt_guard(answer: str) -> tuple[str, bool]:
    """ Prompt Guard 拦截机制 """
    DANGEROUS_WORDS = ["立即买入", "立即卖出", "满仓", "梭哈", "保证收益", "一定能涨"]
    blocked = False
    for word in DANGEROUS_WORDS:
        if word in answer:
            answer = answer.replace(word, "仅供分析参考，不构成交易建议")
            blocked = True
    return answer, blocked

def log_interaction(query, answer, latency_ms, intent, context_len, used_providers, response_status):
    """升级版 Context 审计日志"""
    log_file = COPILOT_LOG_DIR / f"{datetime.now().strftime('%Y%m%d')}.jsonl"
    entry = {
        "timestamp": datetime.now().isoformat(),
        "intent": intent,
        "query": query,
        "answer": answer,
        "context_source": used_providers,
        "tokens_estimated": context_len // 2,
        "latency": latency_ms,
        "response_status": response_status
    }
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"[Copilot] 审计日志写入失败: {e}")

def chat_with_copilot(query: str, chat_history: list, app_state: dict) -> dict:
    """
    调用 LLM，并在超时、离线等状态下触发 fallback 错误，包含 Prompt Guard 安全拦截。
    """
    start_time = time.time()
    
    intent = classify_intent(query)
    context_str, used_providers = build_dynamic_context(intent, query, app_state)
    
    system_prompt = (
        "你是一个名为 AI Trader Copilot 的智能量化股票助手。你的职责是基于系统提供的数据和你的金融知识，为用户提供专业的分析和辅助决策。\n\n"
        "【严格约束】\n"
        "1. 你的所有回答必须严格遵循以下四段式模板（直接以这些标题开头，不可省略）：\n"
        "【结论】\n(简要得出核心结论)\n\n"
        "【核心依据】\n(提供得出该结论的逻辑链)\n\n"
        "【相关数据】\n(罗列上下文中支持该结论的具体数据)\n\n"
        "【风险提示】\n(提出反向风险或需要警惕的地方)\n\n"
        "2. 绝对不能输出“直接买入”、“满仓”、“直接卖出”等命令词，不能承诺保证收益或做确定性预测。\n"
        "3. 你只能提供辅助决策，决策权在用户手中。\n"
    )
    
    messages = [{"role": "system", "content": system_prompt}]
    
    for msg in chat_history[-6:]: 
        messages.append({"role": msg["role"], "content": msg["content"]})
        
    final_query = f"用户提问：{query}\n\n=== 系统提供给你的上下文环境 (分类: {intent}) ===\n{context_str}\n\n请按要求的四段式模板认真作答。"
    messages.append({"role": "user", "content": final_query})

    response_status = "SUCCESS"
    try:
        response = requests.post(
            f"{LLM_BASE_URL}/v1/chat/completions",
            json={
                "model": "local-model",
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 1500,
            },
            timeout=10
        )
        response.raise_for_status()
        raw_answer = response.json()["choices"][0]["message"]["content"]
        
        answer = re.sub(r'<think>.*?</think>', '', raw_answer, flags=re.DOTALL).strip()
        
        # 应用 Prompt Guard
        answer, blocked = apply_prompt_guard(answer)
        if blocked:
            response_status = "BLOCKED_BY_GUARD"
            
        if "【结论】" not in answer:
            answer = f"【结论】\n基于分析生成。\n\n【核心依据】\n{answer}\n\n【相关数据】\n见上文。\n\n【风险提示】\n自动生成格式不严谨，请以实际为准。"
        
        latency = int((time.time() - start_time) * 1000)
        log_interaction(query, answer, latency, intent, len(context_str), used_providers, response_status)
        
        return {
            "status": "success",
            "answer": answer,
            "intent": intent,
            "latency": latency
        }
    except requests.exceptions.Timeout:
        logger.warning("[Copilot] LLM timeout (>10s)")
        latency = int((time.time() - start_time) * 1000)
        answer = "【结论】\n抱歉，AI 引擎响应超时。\n\n【核心依据】\n当前系统设置了严格的 10 秒超时门槛以保障系统流畅性，刚才的问题分析耗时过长。\n\n【相关数据】\n未能从大模型获取反馈。\n\n【风险提示】\n请求被截断，请稍后重试或简化问题。"
        log_interaction(query, answer, latency, intent, len(context_str), used_providers, "TIMEOUT")
        return {
            "status": "timeout",
            "answer": answer,
            "intent": intent,
            "latency": latency
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"[Copilot] Request failed: {e}")
        latency = int((time.time() - start_time) * 1000)
        answer = "【结论】\n无法连接到 AI 推理服务。\n\n【核心依据】\n网络请求被拒绝或模型服务未启动。\n\n【相关数据】\n服务地址：不可达。\n\n【风险提示】\n系统当前只能依靠预设规则运行，无法进行自然语言对话，请手动检查系统状态页并确保后台 LLM (如 LLaMA-Server) 正在运行。"
        log_interaction(query, answer, latency, intent, len(context_str), used_providers, "OFFLINE_FALLBACK")
        return {
            "status": "error",
            "answer": answer,
            "intent": intent,
            "latency": latency
        }
    except Exception as e:
        logger.error(f"[Copilot] Unexpected error: {e}")
        latency = int((time.time() - start_time) * 1000)
        answer = f"【结论】\n系统发生未知异常：{str(e)[:50]}\n\n【核心依据】\n代码解析或者 JSON 数据格式错误。\n\n【相关数据】\n无有效数据。\n\n【风险提示】\n请管理员查看后台日志定位问题。"
        log_interaction(query, answer, latency, intent, len(context_str), used_providers, "EXCEPTION_FALLBACK")
        return {
            "status": "error",
            "answer": answer,
            "intent": intent,
            "latency": latency
        }
