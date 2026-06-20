import os
import sys
import time

# 将项目根目录 ai-trader 加入 sys.path，确保核心模块可被引用
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from feeds.market_data import get_realtime_quotes
from core.logger_config import logger

def run_speed_test():
    logger.info(f"========== 行情并发获取性能测试 ==========")
    logger.info(f"正在拉取系统默认标的池 (Top80活跃股 + 自选股) ...")
    
    start_time = time.time()
    
    try:
        # 修复点：去掉了 test_universe 参数，直接调用无参原函数
        quotes = get_realtime_quotes()
        end_time = time.time()
        
        total_time = end_time - start_time
        success_count = len(quotes) if quotes else 0
        
        logger.info(f"获取成功: {success_count} 只")
        logger.info(f"总计耗时: {total_time:.2f} 秒")
        
        if success_count > 0:
            logger.info(f"平均单只耗时: {total_time/success_count:.3f} 秒")
            
        # 性能评估
        if total_time < 15:
            logger.info("[结果] ✅ 性能极佳！并发获取与令牌桶限流工作正常。")
        elif total_time < 30:
            logger.info("[结果] ⚠️ 性能及格，部分数据节点可能触发了新浪兜底重试。")
        else:
            logger.info("[结果] ❌ 耗时过长！并发未生效，或者当前网络被严重限流。")
            
    except Exception as e:
        logger.exception(f"[错误] 测试崩溃: {e}")

if __name__ == "__main__":
    run_speed_test()