"""
大模型自闭环调优引擎 (Auto Tuner)
基于每日报告，调用本地大模型进行参数反思与动态调整
"""
import json
import os
import re
import requests
from datetime import datetime

# 路径常量
CACHE_DIR = "data_cache"
HYPERPARAMS_PATH = os.path.join(CACHE_DIR, "hyperparams.json")
DEFAULT_HYPERPARAMS = {
    "atr_period": 14,
    "risk_per_trade": 0.01,
    "stop_loss_pct": -0.05,
}

# 本地大模型 API 地址
LLM_API_URL = "http://127.0.0.1:8080/v1/chat/completions"

# System Prompt：强制模型扮演量化调优师，只输出纯 JSON
SYSTEM_PROMPT = """你是一个量化调优系统。请根据下述报告，输出最新的风控参数。你必须且只能输出合法的 JSON 格式，绝对禁止任何其他文字解释。

JSON 模板样例：
{"atr_period": 14, "risk_per_trade": 0.01, "stop_loss_pct": -0.05}

参数说明：
- atr_period: ATR 周期 (整数)
- risk_per_trade: 单笔交易风险比例 (浮点数，0-1之间)
- stop_loss_pct: 止损比例 (浮点数，负数)

请基于报告中的盈亏表现、回撤情况、持仓周期等指标进行调整。
"""


def _load_hyperparams():
    """加载当前超参数，文件不存在时返回默认值"""
    if not os.path.exists(HYPERPARAMS_PATH):
        return dict(DEFAULT_HYPERPARAMS)
    try:
        with open(HYPERPARAMS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return dict(DEFAULT_HYPERPARAMS)


def _save_hyperparams(params):
    """原子化保存超参数到 JSON 文件"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    temp_path = HYPERPARAMS_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2, ensure_ascii=False)
    # 原子替换
    os.replace(temp_path, HYPERPARAMS_PATH)
    print(f"[调优引擎] 参数已更新: {params}")


def _extract_json_from_response(response_text):
    """
    从大模型返回的文本中提取 JSON 数据
    支持：纯 JSON、Markdown 代码块包裹的 JSON
    强化正则提取：使用 re.DOTALL 匹配跨行的花括号内容
    """
    # 尝试直接解析
    try:
        return json.loads(response_text.strip())
    except json.JSONDecodeError:
        pass

    # 尝试从 Markdown 代码块中提取
    # 匹配 ```json ... ``` 或 ``` ... ```
    pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
    matches = re.findall(pattern, response_text, re.DOTALL)
    for match in matches:
        try:
            return json.loads(match.strip())
        except json.JSONDecodeError:
            continue

    # 强化正则提取：使用 re.DOTALL 匹配跨行的花括号内容
    # 匹配最外层的 { ... } 块
    match = re.search(r'\{.*\}', response_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


def run_daily_reflection(report_path):
    """
    执行每日反思调优：
    1. 读取日报内容
    2. 调用本地大模型 API 进行参数反思
    3. 解析返回的 JSON 并更新超参数文件

    Args:
        report_path: 日报文件路径 (daily_report_YYYYMMDD.md)
    """
    print(f"\n{'='*60}")
    print(f"[调优引擎] 开始每日反思: {report_path}")
    print(f"{'='*60}")

    # 1. 读取日报内容
    if not os.path.exists(report_path):
        print(f"[调优引擎] 警告: 日报文件不存在 {report_path}，跳过调优")
        return

    with open(report_path, "r", encoding="utf-8") as f:
        report_content = f.read()

    if not report_content.strip():
        print(f"[调优引擎] 警告: 日报内容为空，跳过调优")
        return

    # 预检拦截：无交易且无持仓时跳过大模型调用
    no_buys = "- **买入笔数**: 0" in report_content
    no_sells = "- **卖出笔数**: 0" in report_content
    no_positions = "- **持仓数量**: 0 只" in report_content

    if no_buys and no_sells and no_positions:
        print(f"[调优引擎] 拦截：今日无交易记录且无持仓，跳过大模型参数寻优。")
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
        "max_tokens": 500
    }

    # 3. 调用大模型 API
    try:
        print(f"[调优引擎] 调用大模型 API: {LLM_API_URL}")
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
            print(f"[调优引擎] 警告: 大模型返回内容为空")
            return

        print(f"[调优引擎] 大模型返回:\n{assistant_message}")

        # 4. 解析 JSON
        new_params = _extract_json_from_response(assistant_message)

        if new_params is None:
            print(f"[调优引擎] 警告: 无法从大模型返回中解析 JSON，跳过参数更新")
            print(f"[调优引擎] 原始返回内容:\n{assistant_message}")
            return

        # 5. 验证参数完整性
        required_keys = {"atr_period", "risk_per_trade", "stop_loss_pct"}
        if not required_keys.issubset(new_params.keys()):
            missing = required_keys - set(new_params.keys())
            print(f"[调优引擎] 警告: 大模型返回缺少必要字段 {missing}，跳过参数更新")
            return

        # 6. 类型校验与转换
        try:
            new_params["atr_period"] = int(new_params["atr_period"])
            new_params["risk_per_trade"] = float(new_params["risk_per_trade"])
            new_params["stop_loss_pct"] = float(new_params["stop_loss_pct"])
        except (ValueError, TypeError) as e:
            print(f"[调优引擎] 警告: 参数类型转换失败 {e}，跳过参数更新")
            return

        # 7. 原子化保存
        _save_hyperparams(new_params)
        print(f"[调优引擎] 每日反思完成，参数已更新")

    except requests.exceptions.RequestException as e:
        print(f"[调优引擎] 错误: 调用大模型 API 失败 - {e}")
    except json.JSONDecodeError as e:
        print(f"[调优引擎] 错误: 解析 API 响应失败 - {e}")
    except Exception as e:
        print(f"[调优引擎] 错误: 每日反思异常 - {e}")


if __name__ == "__main__":
    # 测试入口：手动触发调优
    today_str = datetime.now().strftime("%Y%m%d")
    test_report_path = os.path.join(CACHE_DIR, f"daily_report_{today_str}.md")
    run_daily_reflection(test_report_path)
