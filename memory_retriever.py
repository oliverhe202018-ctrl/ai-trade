"""
向量记忆检索器 - 从 ChromaDB 中检索历史高分交易经验
供 ai_client.py 在打分前进行 RAG 动态记忆注入
"""
from chroma_manager import get_trading_memory_collection, query_trading_experience


def get_relevant_experience(latest_news: str, top_k: int = 2) -> str:
    """
    根据当日新闻从 ChromaDB 检索最相关的高分交易经验

    Args:
        latest_news: 当日最新新闻文本，用作查询向量
        top_k: 返回最相关的前 k 条经验

    Returns:
        格式化拼接的历史经验字符串，无结果时返回 "暂无"
    """
    try:
        collection = get_trading_memory_collection()
        if collection is None or collection.count() == 0:
            return "暂无"

        results = query_trading_experience(latest_news, top_k)

        memories = []
        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]

        for dist, meta in zip(distances, metadatas):
            if dist < 1.5:
                exp = meta.get("trading_experience", "")
                score = meta.get("score", "?")
                if exp:
                    memories.append(f"- [评分{score}/distance{dist:.2f}] {exp}")

        return "\n".join(memories) if memories else "暂无"
    except Exception as e:
        print(f"[WARN] ChromaDB 检索失败: {e}")
        return "暂无"
