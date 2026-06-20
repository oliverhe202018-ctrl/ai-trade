"""
实盘状态持久化模块 (Live State Persistence)
原子写入保证断电安全：先写临时文件再 rename，绝不直接覆盖。
并发保护：使用 FileLock 防止多进程读写冲突。
"""
import os
import json
import tempfile
import time
from filelock import FileLock
import atomicwrites  # noqa: F401  # 可选依赖，回退到手动原子写入

from core.logger_config import logger

DEFAULT_FILEPATH = "data_cache/live_portfolio.json"
LOCK_TIMEOUT = 10  # 锁超时时间（秒）

# TTL 内存缓存（60秒）
_cache = {}
_cache_lock = FileLock(DEFAULT_FILEPATH + ".cache.lock", timeout=LOCK_TIMEOUT)


def _get_cached(filepath: str) -> dict | None:
    """从内存缓存读取（带 TTL）"""
    with _cache_lock:
        if filepath in _cache:
            cached_time, data = _cache[filepath]
            if time.time() - cached_time < 60:  # 60秒 TTL
                return data
    return None


def _set_cached(filepath: str, data: dict):
    """写入内存缓存"""
    with _cache_lock:
        _cache[filepath] = (time.time(), data)


def _invalidate_cache(filepath: str):
    """清除缓存"""
    with _cache_lock:
        _cache.pop(filepath, None)


def save_portfolio(portfolio, filepath=DEFAULT_FILEPATH):
    """
    原子写入资产快照（带文件锁）。
    流程: 获取锁 -> 写入同目录临时文件 -> fsync -> os.replace (原子替换) -> 清除缓存。
    即使写入中途断电，旧文件仍完好，绝不会清零。
    """
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    lock_path = filepath + ".lock"
    lock = FileLock(lock_path, timeout=LOCK_TIMEOUT)
    
    with lock:
        payload = json.dumps(portfolio, ensure_ascii=False, indent=2)

        # 手动原子写入：临时文件 + fsync + os.replace
        dir_name = os.path.dirname(filepath) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
        except Exception:
            # 写入失败时清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    
    # 写入成功后清除缓存
    _invalidate_cache(filepath)


def load_portfolio(filepath=DEFAULT_FILEPATH):
    """
    加载资产快照（带文件锁 + TTL 缓存）。
    文件不存在时返回 None。
    调用方应检查返回值，若为 None 则视为首次启动。
    """
    # 先尝试从缓存读取
    cached = _get_cached(filepath)
    if cached is not None:
        return cached
    
    if not os.path.exists(filepath):
        return None
    
    lock_path = filepath + ".lock"
    lock = FileLock(lock_path, timeout=LOCK_TIMEOUT)
    
    with lock:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 写入缓存
                _set_cached(filepath, data)
                return data
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"[STATE WARN] 资产文件损坏，返回 None: {e}")
            return None
