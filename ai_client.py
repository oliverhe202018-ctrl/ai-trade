"""
AI 决策通信模块 - 负责本地大模型交互、分批切片轮询与异常解析
注入宏观新闻 + 主力资金面 + 板块排名，供 Qwen 35B 深度打分
带 Evaluator 自我修复机制
"""
import json
import re
import requests
from advanced_factors import get_macro_news
from memory_retriever import get_relevant_experience


def _repair_json_with_evaluator(broken_str):
    """Evaluator 节点：专职修复破损的 JSON"""
    print("  [EVALUATOR] 检测到 JSON 结构崩塌，正在触发 LLM 自我修复机制...")
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个严格的 JSON 代码修复机器。请修复用户提交的破损 JSON 字符串。"
                "补全缺失的逗号、括号，并将所有值内部的双引号转为单引号。"
                "注意：只能输出纯 JSON，绝对禁止输出 Markdown 标记（如 ```json）、"
                "分析过程或任何其他多余文本！"
            ),
        },
        {
            "role": "user",
            "content": f"请修复以下破损的 JSON:\n{broken_str}",
        },
    ]
    try:
        response = requests.post(
            "http://127.0.0.1:8080/v1/chat/completions",
            json={
                "model": "local-model",
                "messages": messages,
                "temperature": 0.0,
                "max_tokens": 1024,
            },
            timeout=300,
        )
        content = response.json()["choices"][0]["message"]["content"].strip()

        # 微创切片剥离外壳
        start_idx, end_idx = content.find("{"), content.rfind("}")
        if start_idx != -1 and end_idx != -1:
            clean_json = content[start_idx : end_idx + 1]
        else:
            clean_json = content

        return json.loads(clean_json)
    except Exception as e:
        print(f"  [EVALUATOR] 抢救彻底失败: {e}")
        return None


def _compress_context(long_text: str) -> str:
    """
    信息密度压缩：将冗长的宏观新闻与历史经验压缩为高密度核心信号。
    仅在文本长度 > 2000 字符时触发大模型压缩。
    """
    if len(long_text) <= 2000:
        return long_text

    print(f"  [COMPRESS] 上下文长度 {len(long_text)} 字符，触发信息密度压缩...")
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个量化信息提炼机。请将以下冗长的宏观新闻与历史经验，"
                "压缩提炼为高密度的核心主线与交易信号。"
                "剔除废话，保留数据，严格控制在 800 字以内。"
            ),
        },
        {
            "role": "user",
            "content": long_text,
        },
    ]
    try:
        response = requests.post(
            "http://127.0.0.1:8080/v1/chat/completions",
            json={
                "model": "local-model",
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 1000,
            },
            timeout=300,
        )
        if response.status_code == 200:
            compressed = response.json()["choices"][0]["message"]["content"].strip()
            print(f"  [COMPRESS] 压缩完成: {len(long_text)} → {len(compressed)} 字符")
            return compressed
        else:
            print(f"  [COMPRESS] 压缩失败(状态码 {response.status_code})，回退原始文本")
            return long_text
    except Exception as e:
        print(f"  [COMPRESS] 压缩异常: {e}，回退原始文本")
        return long_text


def get_ai_scoring_batch(candidates, config=None):
    """
    调用本地大模型对候选股票进行打分与深度过滤

    严格限流：单次调用 candidates 数量不得超过 5，
    超出则抛出 ValueError，由调用方负责分批。

    Args:
        candidates: 候选股票列表 (来自 market_data)，长度 <= 5
        config: 系统配置字典

    Returns:
        过滤后的候选股票列表, 每个元素增加 score 和 ai_reason 字段
    """
    if not candidates:
        return candidates

    # 严格限流：从底层杜绝超长列表撑爆上下文
    if len(candidates) > 5:
        raise ValueError("批处理数量超过上限 5，请在调用方执行分批逻辑！")

    total_stocks = len(candidates)

    # 抓取全局宏观新闻
    latest_news = get_macro_news()
    print(f"  [DEBUG] 市场最新电报: {latest_news[:50]}...")

    # 触发 RAG 记忆检索
    retrieved_memories = get_relevant_experience(latest_news)

    # 信息密度压缩：合并新闻与记忆，超标时调用大模型压缩
    combined_context = f"【宏观新闻】{latest_news}\n【历史经验】{retrieved_memories}"
    compressed_context = _compress_context(combined_context)

    try:
        # 将 main_fund (主力净流入) + sector_rank (板块排名) 加入组装字典
        stock_list_str = json.dumps(
            [
                {
                    "code": c.get("code"),
                    "name": c.get("name"),
                    "sector": c.get("sector"),
                    "change": c.get("change_pct"),
                    "vol_ratio": c.get("volume_ratio"),
                    "turnover": c.get("turnover_rate"),
                    "macd": c.get("macd_trend", "无"),
                    "ma5": c.get("ma5_trend", "无"),
                    "main_fund_10k": c.get("main_fund", 0),  # 主力资金(万)
                    "sector_rank": c.get("sector_rank", 99),  # 全市场板块排名
                }
                for c in candidates
            ],
            ensure_ascii=False,
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个量化打分器。请直接输出结果，包含在 JSON 对象中，格式为：\n"
                    '{"stock_picks": [{"code": "代码", "score": 分数, "reason": "理由"}]}\n\n'
                    "你是专业的A股量化操盘手。请对股票打分(0-100)。\n"
                    f"【浓缩上下文（宏观+经验）】：{compressed_context}\n"
                    "【选股铁律】：\n"
                    "1. 顺势而为：若个股所属板块与宏观消息面利好共振，予以加分。\n"
                    "2. 技术面底线：MACD金叉或红柱放大，且站上5日线，给予高分。若死叉或跌破5日线直接淘汰！\n"
                    "3. 资金面印证：若 main_fund_10k 为大额正数，强力加分；若流出坚决回避。\n"
                    "4. 赛道共振(最重要)：注意 sector_rank（板块全市场排名）。若该股所在板块排名前 10，说明是当前绝对主线风口，请给予极高权重加分！若排名靠后(>50)，说明资金在流出该赛道，果断降分。\n"
                    "5. 经验传承：必须参考【浓缩上下文】中的历史实战经验，避免重复踩坑，复用成功模式。\n"
                    "6. 理由字段内使用单引号(')代替双引号(\")，避免JSON解析错误。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"请结合消息面、技术形态、主力资金与板块风口，对以下候选股票进行评分：\n{stock_list_str}\n\n"
                    "请直接输出 JSON 对象。"
                ),
            },
        ]

        response = requests.post(
            "http://127.0.0.1:8080/v1/chat/completions",
            json={
                "model": "local-model",
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 8192,
            },
            timeout=600,
        )

        # 异常拦截：非 200 状态码直接返回空
        if response.status_code != 200:
            print(f"  [API ERROR] 状态码: {response.status_code}, 详情: {response.text}")
            return []

        # 提取原始报文
        raw_json = response.json()
        message = raw_json["choices"][0]["message"]

        # 提取原始报文并强制截断，防止超长推理链耗尽内存
        reasoning_content = message.get("reasoning_content", "")
        content = message.get("content", "")
        
        # P1-2 修复：长度硬限制
        reasoning_content = reasoning_content[:10000]  # 截断前1万字符
        content = content[:50000]  # 截断前5万字符
        
        # 文本全量合并
        raw_text = reasoning_content + content
        
        # 暴露黑盒：强制打印日志，便于排查截断问题
        print(f"[DEBUG] LLM 返回总长度: {len(raw_text)}")
        print(f"[DEBUG] 报文尾部片段: {raw_text[-300:]}")

        # 多重正则提取器（层层兜底）
        json_str = None
        
        # 第一级：尝试匹配标准 Markdown 格式的 JSON 块 (```json ... ```)
        match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', raw_text)
        if match:
            json_str = match.group(1)
            print("[DEBUG] 第一级正则匹配成功：Markdown JSON 块")
        else:
            # 第二级：尝试匹配普通 JSON 对象 ({...})
            match = re.search(r'\{[\s\S]*\}', raw_text)
            if match:
                json_str = match.group(0)
                print("[DEBUG] 第二级正则匹配成功：普通 JSON 对象")
            else:
                # 第三级：尝试匹配包含 stock_picks 的片段
                match = re.search(r'stock_picks[\s\S]*?\]', raw_text)
                if match:
                    # 尝试从匹配位置向前找到 {
                    start_pos = raw_text.rfind('{', 0, match.start())
                    if start_pos != -1:
                        json_str = raw_text[start_pos:match.end() + 1]
                        print("[DEBUG] 第三级正则匹配成功：stock_picks 片段")
        
        # 所有正则都失败
        if not json_str:
            print(f"[WARN] 多重正则提取全部失败，已安全跳过")
            print(f"[DEBUG] raw_text 前 200 字符: {raw_text[:200]}")
            print(f"[DEBUG] raw_text 后 200 字符: {raw_text[-200:]}")
            return []

        # 空内容检查
        if not json_str.strip():
            print(f"[WARN] 提取的 JSON 字符串为空，已安全跳过")
            print(f"[DEBUG] raw_text 前 200 字符: {raw_text[:200]}")
            print(f"[DEBUG] raw_text 后 200 字符: {raw_text[-200:]}")
            return []

        # Evaluator 修复管线：优先尝试直接解析，失败则触发 LLM 自我修复
        try:
            business_data = json.loads(json_str)
        except json.JSONDecodeError:
            business_data = _repair_json_with_evaluator(json_str)
            if not business_data:
                return []

        all_ai_score_map = {
            item.get("code"): item for item in business_data.get("stock_picks", [])
        }

    except Exception as e:
        print(f"[WARN] AI 批处理评分管线异常: {e}")
        return []

    filtered_candidates = []
    for candidate in candidates:
        code = candidate.get("code")
        if code in all_ai_score_map:
            score = all_ai_score_map[code].get("score", 0)
            if score >= 60:
                candidate["score"] = score
                candidate["ai_reason"] = all_ai_score_map[code].get("reason", "")
                filtered_candidates.append(candidate)

    filtered_candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    return filtered_candidates
