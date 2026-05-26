"""
src/utils/logger.py - 日志配置

配置 loguru 日志系统：
1. 控制台输出（彩色，便于开发调试）
2. 文件输出（按天轮转，保留30天）
3. 降级事件专用日志（单独文件，便于追踪）
"""

import sys
from loguru import logger

logger.remove()

# ========== 1. 控制台输出配置 ==========
logger.add(
    sys.stdout,  
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO",  
    colorize=True, 
)

# ========== 2. 全部日志文件（按天轮转）==========
# 记录所有级别的日志，用于排查问题
logger.add(
    "logs/review_{time:YYYY-MM-DD}.log", 
    rotation="1 day",                     
    retention="30 days",         
    compression="gz",                  
    level="DEBUG",         
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
)

# ========== 3. 错误日志单独文件 ==========
# 只记录 WARNING 及以上级别，便于快速定位问题
logger.add(
    "logs/errors_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="30 days",
    level="WARNING",  # 只记录 WARNING、ERROR、CRITICAL
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
)

# ========== 4. 降级事件专用日志 ==========
# 通过 filter 筛选，只记录绑定了 event="degradation" 的日志
def degradation_filter(record):
    """过滤函数：只保留 event 字段为 'degradation' 的日志"""
    return record.get("extra", {}).get("event") == "degradation"


logger.add(
    "logs/degradation_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="30 days",
    level="INFO",
    filter=degradation_filter,  # 只记录降级事件
    format="{time:YYYY-MM-DD HH:mm:ss} | {message}",
)

# ========== 5. 创建带绑定信息的快捷函数 ==========
def get_degradation_logger():
    """
    获取一个预绑定了 event='degradation' 的 logger
    
    使用方式：
        from src.utils.logger import get_degradation_logger
        deg_logger = get_degradation_logger()
        deg_logger.warning("安全员超时，触发降级")
    
    这个日志会同时出现在：
    - 控制台（普通输出）
    - 全部日志文件
    - 降级专用文件（通过 filter 筛选）
    """
    return logger.bind(event="degradation")


__all__ = ["logger", "get_degradation_logger"]
