"""
全局日志配置模块
支持按天轮转的工业级 logging 系统
"""
import os
import logging
from logging.handlers import TimedRotatingFileHandler

# 确保 logs 目录存在
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# 日志格式
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

def setup_logger(name="ai_trader"):
    """
    配置并返回 logger 实例
    - 控制台输出 INFO 级别
    - 文件输出 DEBUG 级别，每天午夜轮转，保留 30 天
    """
    logger = logging.getLogger(name)
    
    # 避免重复添加 handler
    if logger.handlers:
        return logger
    
    logger.setLevel(logging.DEBUG)
    
    # 控制台 Handler (INFO 级别)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # 文件 Handler (DEBUG 级别，按天轮转)
    log_file = os.path.join(LOG_DIR, "system.log")
    file_handler = TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT)
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    return logger

# 全局 logger 实例
logger = setup_logger()
