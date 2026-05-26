"""
ReviewInput 数据模型

系统的入口数据模型，
定义一次代码评审请求需要提供的信息。
"""

from enum import Enum
from pydantic import BaseModel, Field, field_validator

class Language(str, Enum):
    """
    支持的编程语言枚举

    Planner 会根据语言决定启用哪些 Agent：
    - Python/Java → 全部 4 个 Agent
    - Go → 跳过安全员（3 个 Agent）
    - JavaScript/TypeScript → 跳过逻辑员（3 个 Agent）
    """
    PYTHON = "python"
    JAVA = "java"
    GO = "go"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"

class ReviewInput(BaseModel):
    """
    评审请求的输入数据模型

    使用示例：
        review_input = ReviewInput(
            code="def add(a, b): return a + b",
            language=Language.PYTHON,
            pr_description="添加加法函数",
            commit_hash="abc123def456",
        )

    字段说明：
        code:           必填，待评审的代码内容（经过 @field_validator 校验非空）
        language:       必填，编程语言（枚举值）
        pr_description: 可选，PR 描述，Planner 用它来做 LLM 增强决策
        commit_hash:    可选，commit 的 SHA 值，Redis 缓存以它为 key
    """

    code: str = Field(
        ...,
        min_length=1,
        description="待评审的代码内容, 不能为空"
    )

    language: Language = Field(
        ...,
        description="编程语言: python/java/go/javascript/typescript, 不能为空"
    )

    pr_description: str = Field(
        default="",
        description="PR 描述，可选。Planner 用它判断是否需要 LLM 增强决策"
    )

    commit_hash: str = Field(
        default="",
        description="commit 的 SHA 值，可选。用于 Redis 缓存去重"
    )

    @field_validator("code")
    @classmethod
    def validate_code_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("代码内容不能为空或仅包含空白字符")
        return v

