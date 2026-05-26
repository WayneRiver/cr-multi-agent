"""
Finding 数据模型

Finding 是代码评审系统中最核心的数据模型。
每个 Agent（规范员、安全员、逻辑员、可读员）检查完代码后，
都会输出一个 Finding 列表。

一条 Finding 代表代码中发现的一个问题，
包含问题的位置、严重程度、类型、描述和修复建议。
"""

import uuid
from enum import Enum
from pydantic import BaseModel, Field, model_validator

class Severity(str, Enum):
    """
    严重程度枚举

    critical: 必须立即修复的问题（如安全漏洞、可导致生产事故）
    high:     严重问题，应在合并前修复
    medium:   中等问题，建议修复
    low:      轻微问题，可选修复
    info:     提示性信息，无需修复
    """
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

class FindingType(str, Enum):
    """
    问题类型枚举

    对应系统中的四个 Agent：
    style         → 规范员发现的问题（格式、命名规范等）
    security      → 安全员发现的问题（SQL注入、XSS等）
    logic         → 逻辑员发现的问题（空指针、死循环等）
    readability   → 可读员发现的问题（命名不清、嵌套过深等）
    """
    STYLE = "style"
    SECURITY = "security"
    LOGIC = "logic"
    READABILITY = "readability"

class Finding(BaseModel):
    """
    Finding 数据模型 —— 代码中发现的一个问题

    每个 Agent 输出一个 Finding 列表，
    Aggregator 负责合并、去重、排序所有 Agent 的 Finding。

    使用示例：
        finding = Finding(
            severity=Severity.HIGH,
            type=FindingType.SECURITY,
            file="src/app.py",
            line_start=42,
            line_end=42,
            title="检测到 SQL 注入风险",
            description="直接拼接用户输入构造 SQL 查询",
            suggestion="使用参数化查询代替字符串拼接",
        )
    """

    # 唯一标识符
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="唯一标识符, 自动生成 UUID"
    )

    # 严重程度
    severity: Severity = Field(
        ...,
        description="问题的严重程度: critical, high, medium, low, info"
    )

    # 问题类型
    type: FindingType = Field(
        ...,
        description="问题的类型: style, security, logic, readability"
    )

    # 问题所在文件
    file: str = Field(
        default="",
        description="问题所在文件路径"
    )

    # 问题所在行号
    line_start: int = Field(
        ...,
        ge=1,
        description="问题开始的行号"
    )
    line_end: int = Field(
        ...,
        ge=1,
        description="问题结束的行号"
    )

    # 问题标题
    title: str = Field(
        ...,
        min_length=1,
        description="问题的标题, 一句话概括"
    )

    # 问题描述
    description: str = Field(
        default="",
        description="问题的详细描述"
    )

    # 修复建议
    suggestion: str = Field(
        default="",
        description="修复问题的建议"
    )

    @model_validator(mode="after")
    def validate_line_range(self):
        """
        跨字段校验：确保 line_end >= line_start

        使用 mode='after' 表示在所有字段解析完成后执行，
        此时 self 已经是完整的 Finding 实例。

        field_validator 只能访问单个字段的值。
        """
        if self.line_end < self.line_start:
            raise ValueError(
                f"line_end（{self.line_end}）不能小于 line_start（{self.line_start}）"
            )
        return self

