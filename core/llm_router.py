"""
LLM Router — Phase 10 双模型架构

路由: Local Qwen (本地) ↔ DeepSeek V4 Pro (远程)

模型职责:
  Local Qwen:  Copilot 问答 / 摘要 / 解释 / 低成本批量分析 / DeepSeek fallback
  DeepSeek:    Potential Discovery / 深度分析 / 异动复核 / 策略解释 / 风险分析

.env 配置:
  DEEPSEEK_API_KEY      — DeepSeek API Key
  DEEPSEEK_BASE_URL     — DeepSeek API 地址
  DEEPSEEK_MODEL        — 模型名 (默认 deepseek-v4-pro)
  LOCAL_QWEN_BASE_URL   — 本地 Qwen 地址 (默认 http://localhost:8080)
  LOCAL_QWEN_MODEL      — 本地模型名 (默认 local-model)
"""
import json
import os
import time
import requests
from datetime import datetime

from core.logger_config import logger

# ── 环境变量读取 ──────────────────────────────────────────

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
LOCAL_QWEN_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:8080")
LOCAL_QWEN_MODEL = os.environ.get("LOCAL_QWEN_MODEL", "local-model")

# ── 路由表 ────────────────────────────────────────────────

DEEPSEEK_TASKS = {
    "potential_discovery",
    "stock_deep_analysis",
    "strategy_explanation",
    "risk_review",
}

QWEN_TASKS = {
    "general_chat",
    "copilot",
    "summarize",
}


# ═══════════════════════════════════════════════════════════

def _call_local_qwen(prompt: str, system: str = "", max_tokens: int = 1000) -> dict:
    """调用本地 Qwen 模型。"""
    try:
        resp = requests.post(
            f"{LOCAL_QWEN_BASE_URL}/v1/chat/completions",
            json={
                "model": LOCAL_QWEN_MODEL,
                "messages": (
                    [{"role": "system", "content": system}]
                    if system else []
                ) + [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": max_tokens,
            },
            timeout=30,
        )
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            return {"ok": True, "model": "local_qwen", "fallback_used": False, "content": content}
        else:
            return {"ok": False, "model": "local_qwen", "error": f"HTTP {resp.status_code}", "fallback_used": False}
    except Exception as e:
        return {"ok": False, "model": "local_qwen", "error": str(e), "fallback_used": False}


def _call_deepseek(prompt: str, system: str = "", max_tokens: int = 1500) -> dict:
    """调用 DeepSeek V4 Pro。"""
    if not DEEPSEEK_API_KEY:
        return {"ok": False, "model": "deepseek", "error": "DEEPSEEK_API_KEY 未配置"}
    try:
        resp = requests.post(
            f"{DEEPSEEK_BASE_URL or 'https://api.deepseek.com'}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": (
                    [{"role": "system", "content": system}]
                    if system else []
                ) + [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": max_tokens,
            },
            timeout=45,
        )
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"]["content"]
            return {"ok": True, "model": "deepseek", "fallback_used": False, "content": content}
        else:
            return {"ok": False, "model": "deepseek", "error": f"HTTP {resp.status_code} {resp.text[:200]}"}
    except Exception as e:
        return {"ok": False, "model": "deepseek", "error": str(e)}


def analyze_with_llm(
    task_type: str,
    prompt: str,
    payload: dict | None = None,
    preferred_model: str | None = None,
    system: str = "",
) -> dict:
    """
    双模型路由入口。

    Args:
        task_type:       general_chat | copilot | summarize | potential_discovery | stock_deep_analysis | strategy_explanation | risk_review
        prompt:          用户提示词
        payload:         附加数据（暂未使用）
        preferred_model: 优先模型 (deepseek | local_qwen)，None 则按路由表
        system:          system prompt

    Returns:
        {ok, model, fallback_used, content|error}
    """
    if preferred_model:
        primary = preferred_model
        fallback = "local_qwen" if preferred_model == "deepseek" else None
    elif task_type in DEEPSEEK_TASKS:
        primary = "deepseek"
        fallback = "local_qwen"
    else:
        primary = "local_qwen"
        fallback = None

    # ── Primary call ──
    if primary == "deepseek":
        result = _call_deepseek(prompt, system=system)
    else:
        result = _call_local_qwen(prompt, system=system)

    if result["ok"]:
        return result

    # ── Fallback ──
    if fallback:
        logger.warning(
            f"[LLM Router] {primary} 调用失败 ({result.get('error')})，"
            f"回退到 {fallback}"
        )
        fb_result = _call_local_qwen(prompt, system=system)
        if fb_result["ok"]:
            fb_result["fallback_used"] = True
            fb_result["fallback_reason"] = f"{primary} unavailable: {result.get('error')}"
            return fb_result

    # ── 全部失败 ──
    return {
        "ok": False,
        "model": primary,
        "error": result.get("error", "Unknown"),
        "fallback_used": False,
    }


# ═══════════════════════════════════════════════════════════
#  便捷函数
# ═══════════════════════════════════════════════════════════

def deep_analyze_stock(symbol: str, name: str, features: dict) -> dict:
    """对单只股票做 DeepSeek 深度分析。"""
    prompt = (
        f"请对股票 {symbol} ({name}) 做一次深度分析。\n\n"
        f"关键指标:\n"
        f"  fusion_score: {features.get('fusion_score', 'N/A')}\n"
        f"  momentum_score: {features.get('momentum_score', 'N/A')}\n"
        f"  main_money_score: {features.get('main_money_score', 'N/A')}\n"
        f"  change_pct: {features.get('change_pct', 'N/A')}%\n"
        f"  volume_ratio: {features.get('volume_ratio', 'N/A')}\n\n"
        f"请用中文回答，不超过300字，给出你的判断和风险提示。"
    )
    system = "你是专业的证券分析师，回答需客观，不提供买卖建议。"
    return analyze_with_llm("stock_deep_analysis", prompt, system=system)


def explain_strategy_signal(signal: dict) -> dict:
    """对短线策略信号生成解释。"""
    if signal.get("confidence", 0) < 0.8:
        return {"ok": False, "error": "confidence too low for deep analysis"}

    prompt = (
        f"请用通俗中文解释以下短线策略信号:\n\n"
        f"策略: {signal.get('strategy_name')}\n"
        f"股票: {signal.get('symbol')} {signal.get('name')}\n"
        f"动作: {signal.get('action')}\n"
        f"置信度: {signal.get('confidence')}\n"
        f"原因: {signal.get('reason')}\n"
        f"特征: {json.dumps(signal.get('features', {}), ensure_ascii=False)}\n\n"
        f"请用不超过200字解释这个信号的逻辑和风险。"
    )
    return analyze_with_llm("strategy_explanation", prompt)
