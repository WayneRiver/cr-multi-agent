"""
API 请求和响应的数据模型
"""

from typing import Optional, List
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from src.models.review_input import ReviewInput
from src.models.review_result import ReviewResult


class ReviewRequest(ReviewInput):
    """
    代码评审请求体 —— 继承自 ReviewInput

    字段完全来自 ReviewInput（code、language、pr_description、commit_hash），
    ReviewInput 已有的校验（code 非空等）自动生效。

    使用示例（JSON 请求体）：
        {
            "code": "def add(a, b): return a + b",
            "language": "python",
            "pr_description": "添加加法函数",
            "commit_hash": "abc123def456"
        }
    """

    pass

class ReviewResponse(ReviewResult):
    """
    代码评审响应体 —— 继承自 ReviewResult

    在 ReviewResult 的基础上，增加 API 层的元信息字段。

    使用示例（JSON 响应体）：
        {
            "status": "pass",
            "findings": [...],
            "skipped_agents": [],
            "degraded": false,
            "total_duration_ms": 1234,
            "created_at": "2025-01-15T10:30:00",
            "review_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
            "errors": []
        }
    """

    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="评审完成的时间戳（ISO 8601 格式，UTC 时间）"
    )

    review_id: str = Field(
        default_factory=lambda: ReviewResponse._generate_review_id(),
        description="本次评审的唯一 ID，用于追踪和日志关联",
    )

    errors: List[str] = Field(
        default_factory=list,
        description="评审过程中收集的错误信息列表。空列表表示无错误",
    )

    @staticmethod
    def _generate_review_id() -> str:
        """
        生成一个唯一的评审 ID，用于追踪和日志关联。
        """
        import uuid
        return str(uuid.uuid4())[:8]