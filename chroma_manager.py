"""
ChromaDB 单例管理器 - 确保全局唯一的 PersistentClient 实例
防止多进程/多线程并发创建客户端导致 SQLite 锁死
"""
import threading
from filelock import FileLock
import chromadb

# 全局锁文件
CHROMA_LOCK_FILE = "./chroma_db/chroma_client.lock"
CHROMA_DB_PATH = "./chroma_db"

# 线程锁 + 文件锁双重保护
_thread_lock = threading.Lock()
_chroma_client = None


def get_chroma_client():
    """
    获取全局唯一的 ChromaDB PersistentClient 实例
    使用线程锁 + 文件锁双重保护，确保并发安全
    """
    global _chroma_client

    # 快速路径：如果已初始化，直接返回
    if _chroma_client is not None:
        return _chroma_client

    # 慢路径：加锁初始化
    with _thread_lock:
        # 双重检查
        if _chroma_client is not None:
            return _chroma_client

        # 使用文件锁保护初始化过程
        lock = FileLock(CHROMA_LOCK_FILE, timeout=10)
        try:
            with lock:
                _chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
                return _chroma_client
        except Exception as e:
            print(f"[ERROR] ChromaDB 客户端初始化失败: {e}")
            raise


def get_trading_memory_collection():
    """
    获取 trading_memory 集合（带文件锁保护）
    """
    client = get_chroma_client()
    lock = FileLock(CHROMA_LOCK_FILE, timeout=10)
    try:
        with lock:
            return client.get_or_create_collection(name="trading_memory")
    except Exception as e:
        print(f"[ERROR] 获取 trading_memory 集合失败: {e}")
        raise


def add_trading_experience(experience_data: dict, doc_id: str):
    """
    向 trading_memory 添加经验（带文件锁保护）

    Args:
        experience_data: 包含 documents, metadatas 的字典
        doc_id: 文档唯一 ID
    """
    collection = get_trading_memory_collection()
    lock = FileLock(CHROMA_LOCK_FILE, timeout=10)
    try:
        with lock:
            collection.add(
                documents=[experience_data["document"]],
                metadatas=[experience_data["metadata"]],
                ids=[doc_id],
            )
    except Exception as e:
        print(f"[ERROR] 添加交易经验失败: {e}")
        raise


def query_trading_experience(query_text: str, top_k: int = 2):
    """
    查询交易经验（带文件锁保护）

    Args:
        query_text: 查询文本
        top_k: 返回前 k 条结果

    Returns:
        查询结果字典
    """
    collection = get_trading_memory_collection()
    lock = FileLock(CHROMA_LOCK_FILE, timeout=10)
    try:
        with lock:
            return collection.query(
                query_texts=[query_text],
                n_results=top_k,
            )
    except Exception as e:
        print(f"[ERROR] 查询交易经验失败: {e}")
        return {"distances": [[]], "metadatas": [[]]}
