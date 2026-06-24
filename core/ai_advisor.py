import json

LLAMA_ADVISOR_PROMPT_TEMPLATE = {
  "system_instruction": "你是一个量化交易评估引擎。必须严格输出JSON格式。无解释，无废话。",
  "input_data": {
    "symbol": "{symbol}",
    "rule_signal": "{rule_signal}",
    "market_summary": "{market_summary}"
  },
  "expected_output_format": {
    "symbol": "string",
    "action": "BUY | SELL | HOLD",
    "stop_loss_pct": "float",
    "sector_exposure": "string",
    "ai_veto": "boolean (是否否决规则引擎信号)"
  }
}

def generate_prompt(symbol, rule_signal, market_summary):
    prompt_obj = LLAMA_ADVISOR_PROMPT_TEMPLATE.copy()
    prompt_obj["input_data"] = {
        "symbol": symbol,
        "rule_signal": rule_signal,
        "market_summary": market_summary
    }
    return json.dumps(prompt_obj, ensure_ascii=False)
