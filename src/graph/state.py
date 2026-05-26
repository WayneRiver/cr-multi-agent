"""
src/graph/state.py — 工作流状态定义

定义 ReviewState（TypedDict），
它是 LangGraph 图中所有节点之间共享的状态字典。
每个节点读取 state 中的字段、处理后返回要更新的字段，
LangGraph 自动将返回值合并回 state。
"""

from typing import TypedDict, Optional
from src.models.review_input import ReviewInput
from src.models.review_result import ReviewResult
from src.models.finding import Finding

class ReviewState(TypedDict, total=False):
    """
    代码评审工作流的全局状态

    每个字段对应工作流中的一个数据项，
    节点函数通过 state["字段名"] 读取，通过 return {"字段名": 新值} 写入。
    """
    # 原始评审请求，由外部调用方（API 路由）在启动工作流时传入
    input: ReviewInput

    # 是否命中 Redis 缓存（True 表示已有缓存结果，可直接返回）
    cache_hit: bool

    # 缓存结果（如果命中缓存）
    cached_result: Optional[ReviewResult]
    
    # 已启用的 Agent 名称列表
    enabled_agents: list[str]

    # 各个 Agent 发现的问题列表
    style_findings: list[Finding]
    security_findings: list[Finding]
    logic_findings: list[Finding]
    readability_findings: list[Finding]

    # 最终确定的问题列表（可能经过降级、fallback 处理）
    final_findings: list[Finding]

    # 执行失败的 Agent 名称列表
    failed_agents: list[str]

    # 是否触发了降级策略
    degraded: bool

    # 最终评审结果
    result: Optional[ReviewResult]

    # 评审过程中收集的错误信息（供 API 响应使用）
    errors: list[str]
