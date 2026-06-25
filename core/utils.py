import time
import functools
import traceback
from core.logger_config import logger

def retry_with_backoff(retries=4, backoff_in_seconds=(5, 15, 60, 300)):
    """
    指数退避重试装饰器。
    遇到异常时，按指定的秒数列表进行休眠并重试。
    如果在所有重试后仍然失败，抛出最后的异常。
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < retries:
                        sleep_time = backoff_in_seconds[attempt] if attempt < len(backoff_in_seconds) else backoff_in_seconds[-1]
                        logger.warning(
                            f"[RETRY_BACKOFF] 函数 {func.__name__} 执行失败: {str(e)} | "
                            f"第 {attempt + 1}/{retries} 次重试，休眠 {sleep_time}s... \n"
                            f"异常堆栈: {traceback.format_exc()}"
                        )
                        time.sleep(sleep_time)
                    else:
                        logger.error(
                            f"[RETRY_BACKOFF_EXHAUSTED] 函数 {func.__name__} 执行失败，已达到最大重试次数 {retries}。\n"
                            f"最终异常堆栈: {traceback.format_exc()}"
                        )
            raise last_exception
        return wrapper
    return decorator
