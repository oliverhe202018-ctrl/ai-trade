"""
记忆检索模块 - 从 ChromaDB 检索相关交易经验
供 ai_client.py 在打分前调用，实现 RAG (Retrieval-Augmented Generation)
"""
from core.chroma_manager import query_trading_experience
from core.logger_config import logger


def get_relevant_experience(query_text: str, top_k: int = 3) -> str:
    """
    检索与当前查询最相关的交易经验
    
    Args:
        query_text: 查询文本（通常是当前市场新闻或盘面描述）
        top_k: 返回前 k 条最相似的经验
    
    Returns:
        格式化后的经验文本，供 LLM 上下文使用
    """
    try:
        results = query_trading_experience(query_text, top_k=top_k)
        
        if not results or not results.get("metadatas") or not results["metadatas"][0]:
            return "暂无相关历史经验"
        
        experiences = []
        for metadata in results["metadatas"][0]:
            experience = metadata.get("trading_experience", "")
            score = metadata.get("score", 0)
            date = metadata.get("date", "未知日期")
            
            if experience:
                experiences.append(f"[{date} 评分{score}/10] {experience}")
        
        if experiences:
            return "\n".join(experiences)
        else:
            return "暂无相关历史经验"
            
    except Exception as e:
        logger.exception(f"[MEMORY RETRIEVER] 检索失败: {e}")
        return "历史经验检索失败"
