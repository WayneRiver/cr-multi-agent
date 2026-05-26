"""
src/models/__init__.py - 模型包导出入口

统一导出所有核心数据模型，
方便其他模块用简短的 import 路径：

    from src.models import Finding, ReviewInput, ReviewResult
    # 等价于：
    # from src.models.finding import Finding
    # from src.models.review_input import ReviewInput
    # from src.models.review_result import ReviewResult
"""

from src.models.finding import Finding, Severity, FindingType
from src.models.review_input import ReviewInput, Language
from src.models.review_result import ReviewResult, ReviewStatus

# 控制 from src.models import * 时导出哪些符号
__all__ = [
    "Finding",
    "Severity",
    "FindingType",
    "ReviewInput",
    "Language",
    "ReviewResult",
    "ReviewStatus",
]

