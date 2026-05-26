"""
ReviewResult 数据模型

系统的出口数据模型，
汇总了一次代码评审的完整结果：
所有 Agent 的 Finding 列表、被跳过的 Agent、是否触发了降级、总耗时等。
"""

from enum import Enum
from typing import List
from pydantic import BaseModel, Field, computed_field
from src.models.finding import Finding

class ReviewStatus(str, Enum):
    """
    评审结果状态枚举

    PASS:         通过 — 没有发现问题，或只有 info 级别提示
    REJECT:       驳回 — 发现了 critical 级别问题，必须先修复再合并
    PARTIAL_PASS: 部分通过 — 有问题但不致命，可酌情处理
    """
    PASS = "pass"
    REJECT = "reject"
    PARTIAL_PASS = "partial_pass"

class ReviewResult(BaseModel):
    """
    一次代码评审的最终结果

    使用示例：
        result = ReviewResult(
            status=ReviewStatus.PASS,
            findings=[finding1, finding2],
            skipped_agents=[],
            degraded=False,
            total_duration_ms=1234,
        )

    字段说明：
        status:            评审最终状态（通过/驳回/部分通过）
        findings:          所有 Agent 发现的问题列表（已去重、排好序）
        skipped_agents:    被跳过的 Agent 名称列表（如 ["security_checker"]）
        degraded:          是否触发了降级策略
        total_duration_ms: 整个评审流程的总耗时（毫秒）
    """

    status: ReviewStatus = Field(
        default=ReviewStatus.PASS,
        description="评审最终状态: pass/reject/partial_pass"
    )

    findings: List[Finding] = Field(
        default_factory=list,
        description="所有 Agent 发现的问题列表，已由 Aggregator 去重和排序"
    )

    skipped_agents: List[str] = Field(
        default_factory=list,
        description="被跳过的 Agent 名称列表。空列表表示所有 Agent 都成功执行"
    )

    degraded: bool = Field(
        default=False,
        description="是否触发了降级策略"
    )

    total_duration_ms: int = Field(
        default=0,
        ge=0,
        description="整个评审流程的总耗时（毫秒）"
    )

    def is_passed(self) -> bool:
        """判断评审是否通过"""
        return self.status == ReviewStatus.PASS

    def has_critical_findings(self) -> bool:
        """判断是否有 critical 级别问题"""
        from src.models.finding import Severity
        return any(
            finding.severity == Severity.CRITICAL
            for finding in self.findings
        )

    @computed_field  # 函数名后不用加括号
    def findings_count(self) -> int:
        """返回所有 Agent 发现的问题总数"""
        return len(self.findings)