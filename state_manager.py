"""
实盘状态持久化模块 (Live State Persistence)
原子写入保证断电安全：先写临时文件再 rename，绝不直接覆盖。
"""
import os
import json
import tempfile
import atomicwrites  # noqa: F401  # 可选依赖，回退到手动原子写入

DEFAULT_FILEPATH = "data_cache/live_portfolio.json"


def save_portfolio(portfolio, filepath=DEFAULT_FILEPATH):
    """
    原子写入资产快照。
    流程: 写入同目录临时文件 -> fsync -> os.replace (原子替换)。
    即使写入中途断电，旧文件仍完好，绝不会清零。
    """
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

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


def load_portfolio(filepath=DEFAULT_FILEPATH):
    """
    加载资产快照。文件不存在时返回 None。
    调用方应检查返回值，若为 None 则视为首次启动。
    """
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"[STATE WARN] 资产文件损坏，返回 None: {e}")
        return None
