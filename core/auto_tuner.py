"""
大模型自闭环调优引擎 (Auto Tuner) — v2 统一配置源
基于每日报告，调用本地大模型进行参数反思与动态调整

配置源变更：
  v1: 独立读写 config/hyperparams.json（已废弃）
  v2: 通过 broker.load_config() / broker.save_config() 读写 config.yaml → hyperparams 节
"""
import json
import os
import re
import requests
from datetime import datetime

from core.logger_config import logger

# 对等默认值（与 config.yaml 中 hyperparams 节的默认值一致）
CACHE_DIR = "data_cache"
DEFAULT_HYPERPARAMS = {
    "atr_period": 14,
    "risk_per_trade": 0.008,
    "stop_loss_pct": -0.05,
    "max_single_pct": 25,
}

# 本地大模型 API 地址
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080")
LLM_API_URL = f"{LLM_BASE_URL}/v1/chat/completions"

# System Prompt：强制模型扮演首席量化风控官，只输出纯 JSON
SYSTEM_PROMPT = """你现在的角色是首席量化风控官。请根据下述报告，输出最新的风控参数与深度复盘。
你必须且只能输出合法的 JSON 格式，绝对禁止任何其他文字解释。

JSON 模板样例：
{"atr_period": 14, "risk_per_trade": 0.01, "stop_loss_pct": -0.05, "reflection": "..."}

参数说明：
- atr_period: ATR 周期 (整数)
- risk_per_trade: 单笔交易风险比例 (浮点数，0-1之间)
- stop_loss_pct: 止损比例 (浮点数，负数)
- reflection: 字符串类型。在 reflection 字段中，你必须根据今日战报，重点评估系统的『退场机制（止盈止损）』是否健康？是否有策略衰减迹象？用一段精炼的中文给出你的分析结论。

请基于报告中的盈亏表现、回撤情况、持仓周期等指标进行调整。
必须输出包含上述 4 个键的合法 JSON 对象格式，严禁包含任何 Markdown 代码块或额外说明。
"""


def _load_hyperparams():
    """加载统一超参数配置（来自 config.yaml → hyperparams 节）"""
    try:
        from core.broker import load_config
        config = load_config()
        hp = config.get("hyperparams", {})
        # 确保所有键存在，缺失键用默认值填充
        result = dict(DEFAULT_HYPERPARAMS)
        result.update(hp)
        return result
    except Exception as e:
        logger.warning(f"[调优引擎] 统一配置加载失败，使用默认值: {e}")
        return dict(DEFAULT_HYPERPARAMS)


def _save_hyperparams(params):
    """
    原子化保存超参数到 config.yaml → hyperparams 节（共享配置）。
    
    使用 broker.save_config() 方法，与 broker.py / risk_manager.py 共享同一配置源。
    """
    try:
        from core.broker import load_config, save_config
        config = load_config()
        config["hyperparams"] = params
        save_config(config)
        logger.info(f"[调优引擎] 参数已更新到统一配置: {params}")
    except Exception as e:
        logger.exception(f"[调优引擎] 统一配置保存失败: {e}，尝试旧式 json 落盘兜底")
        _save_hyperparams_fallback(params)


def _save_hyperparams_fallback(params):
    """兜底：保存超参数到 data_cache/hyperparams_fallback.json（当 save_config 不可用时）"""
    try:
        fallback_path = os.path.join(CACHE_DIR, "hyperparams_fallback.json")
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(fallback_path, "w", encoding="utf-8") as f:
            json.dump(params, f, indent=2, ensure_ascii=False)
        logger.info(f"[调优引擎] 参数已落盘到降级路径: {fallback_path}")
    except Exception as e2:
        logger.exception(f"[调优引擎] 降级落盘也失败: {e2}")


def run_daily_reflection(report_path):
    """
    执行每日反思调优：
    1. 读取日报内容
    2. 调用本地大模型 API 进行参数反思
    3. 解析返回的 JSON 并更新超参数文件

    Args:
        report_path: 日报文件路径 (daily_report_YYYYMMDD.md)
    """
    logger.info(f"\n{'='*60}")
    logger.info(f"[调优引擎] 开始每日反思: {report_path}")
    logger.info(f"{'='*60}")

    # 1. 读取日报内容
    if not os.path.exists(report_path):
        logger.info(f"[调优引擎] 警告: 日报文件不存在 {report_path}，跳过调优")
        return

    with open(report_path, "r", encoding="utf-8") as f:
        report_content = f.read()

    if not report_content.strip():
        logger.info(f"[调优引擎] 警告: 日报内容为空，跳过调优")
        return

    # 提取日期标识 YYYYMMDD
    date_match = re.search(r'(\d{8})', report_path)
    date_str = date_match.group(1) if date_match else datetime.now().strftime("%Y%m%d")
    feedback_path = os.path.join(CACHE_DIR, f"llm_feedback_{date_str}.md")

    # 预检拦截：无交易且无持仓时跳过大模型调用
    no_buys = "- **买入笔数**: 0" in report_content
    no_sells = "- **卖出笔数**: 0" in report_content
    no_positions = "- **持仓数量**: 0 只" in report_content

    if no_buys and no_sells and no_positions:
        logger.info(f"[调优引擎] 拦截：今日无交易记录且无持仓，跳过大模型参数寻优。")
        return

    # 2. 构造 Payload
    current_params = _load_hyperparams()
    user_prompt = f"""以下是今日交易报告，请基于表现调整超参数：

当前参数：
{json.dumps(current_params, indent=2)}

报告内容：
{report_content}

请输出调整后的参数（纯 JSON 格式）：
"""

    payload = {
        "model": "local-model",  # 本地模型标识
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.3,  # 较低温度，保证输出稳定
        "max_tokens": 500,
        "response_format": {"type": "json_object"}  # 强制 JSON 模式输出
    }

    # 3. 调用大模型 API
    try:
        logger.info(f"[调优引擎] 调用大模型 API: {LLM_API_URL}")
        response = requests.post(
            LLM_API_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        response.raise_for_status()

        result = response.json()
        # 提取模型返回内容
        assistant_message = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        if not assistant_message:
            logger.info(f"[调优引擎] 警告: 大模型返回内容为空")
            return

        logger.info(f"[调优引擎] 大模型返回:\n{assistant_message}")

        # 4. 直接解析 JSON（强制 json_object 模式下无需正则提取）
        try:
            new_params = json.loads(assistant_message.strip())
        except json.JSONDecodeError as e:
            logger.info(f"[调优引擎] 警告: JSON 解析失败 - {e}，跳过参数更新")
            logger.info(f"[调优引擎] 原始返回内容:\n{assistant_message}")
            # 解析失败时将报错信息追加写入反馈文件
            with open(feedback_path, "a", encoding="utf-8") as f:
                f.write(f"\n\n---\n**解析错误**: {e}\n")
            return

        # 5. 验证参数完整性
        required_keys = {"atr_period", "risk_per_trade", "stop_loss_pct", "reflection"}
        if not required_keys.issubset(new_params.keys()):
            missing = required_keys - set(new_params.keys())
            logger.info(f"[调优引擎] 警告: 大模型返回缺少必要字段 {missing}，跳过参数更新")
            return

        # 6. 类型校验与转换
        try:
            new_params["atr_period"] = int(new_params["atr_period"])
            new_params["risk_per_trade"] = float(new_params["risk_per_trade"])
            new_params["stop_loss_pct"] = float(new_params["stop_loss_pct"])
        except (ValueError, TypeError) as e:
            logger.info(f"[调优引擎] 警告: 参数类型转换失败 {e}，跳过参数更新")
            return

        # 7. 提取 reflection 并构造 Markdown 反馈内容
        reflection = new_params.get("reflection", "")
        markdown_content = f"""### Hermes 深度复盘 ({date_str})

{reflection}

---

**最终参数组合：**
- atr_period: {new_params['atr_period']}
- risk_per_trade: {new_params['risk_per_trade']}
- stop_loss_pct: {new_params['stop_loss_pct']}
"""

        # 8. 格式化反馈落盘
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(feedback_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)
        logger.info(f"[调优引擎] 格式化反馈已落盘: {feedback_path}")

        # 9. 原子化保存参数
        _save_hyperparams(new_params)
        logger.info(f"[调优引擎] 每日反思完成，参数已更新")

    except requests.exceptions.RequestException as e:
        logger.info(f"[调优引擎] 错误: 调用大模型 API 失败 - {e}")
    except json.JSONDecodeError as e:
        logger.info(f"[调优引擎] 错误: 解析 API 响应失败 - {e}")
    except Exception as e:
        logger.exception(f"[调优引擎] 错误: 每日反思异常 - {e}")


if __name__ == "__main__":
    # 测试入口：手动触发调优
    today_str = datetime.now().strftime("%Y%m%d")
    test_report_path = os.path.join(CACHE_DIR, f"daily_report_{today_str}.md")
    run_daily_reflection(test_report_path)
