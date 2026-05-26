"""
src/api/routes.py - API 路由定义

定义所有 HTTP 端点的路由：
- GET  /api/v1/health  健康检查
- POST /api/v1/review  代码评审（核心端点）
- GET  /api/v1/metrics 监控指标
"""

import time

from fastapi import APIRouter, Request
from src.api.schemas import ReviewRequest, ReviewResponse
from src.models.review_input import ReviewInput
from src.models.review_result import ReviewStatus, ReviewResult
from src.utils.logger import logger


router = APIRouter(prefix="/api/v1", tags=["review"])

@router.get("/health", status_code=200)
def health_check():
    """
    健康检查端点
    
    用于：
    1. 负载均衡器检测服务是否存活
    2. Docker 健康检查
    3. 手动验证服务是否正常
    
    返回示例：
        {"status": "ok", "service": "cr-multi-agent"}
    """

    logger.debug("健康检查请求")
    return {
        "status": "ok",
        "service": "code-review-multi-agent",
        "version": "0.1.0"
    }

@router.post("/review", response_model=ReviewResponse, status_code=201)
async def create_review(request: ReviewRequest, req: Request):
    """
    代码评审端点（核心）

    接收代码内容，调用 LangGraph 多智能体工作流，
    返回结构化评审结果。

    请求体示例：
        {
            "code": "def add(a, b): return a + b",
            "language": "python",
            "pr_description": "添加加法函数",
            "commit_hash": "abc123"
        }

    响应示例：
        {
            "status": "pass",
            "findings": [...],
            "skipped_agents": [],
            "degraded": false,
            "total_duration_ms": 1234,
            "review_id": "a1b2c3d4",
            "errors": []
        }

    执行流程：
        1. 将 ReviewRequest 转换为 ReviewInput（领域模型）
        2. 调用 app.state.graph.ainvoke() 运行完整工作流
        3. 从工作流返回的 state 中提取结果
        4. 构建 ReviewResponse 并返回
    """
    # 记录开始时间
    start_time = time.time()

    # 记录评审计数（每次请求 +1）
    metrics_collector = req.app.state.metrics
    await metrics_collector.record_review()

    logger.info(
        f"收到评审请求 | 语言: {request.language.value} | "
        f"代码长度: {len(request.code)} 字符"
        + (f" | commit: {request.commit_hash[:8]}" if request.commit_hash else "")
    )

    # 构建 ReviewInput 实例
    review_input = ReviewInput(
        code=request.code,
        language=request.language,
        pr_description=request.pr_description,
        commit_hash=request.commit_hash,
    )

    # 调用 LangGraph 工作流
    graph = req.app.state.graph

    try:
        state = await graph.ainvoke({"input": review_input})
    except Exception as e:
        # 工作流层面的未预期异常（如 LLM 调用失败、网络错误等）
        # 记录完整异常栈，返回 500 错误
        logger.opt(exception=True).error(f"工作流执行异常: {e}")
        raise

    # 步骤 3：从工作流状态中提取结果

    # 情况 A：缓存命中 — aggregate 节点直接写入 cached_result
    if state.get("cache_hit") and state.get("cached_result"):
        cached: ReviewResult = state["cached_result"]
        # 记录缓存命中
        await metrics_collector.record_cache_hit()

        response = ReviewResponse(
            status=cached.status,
            findings=cached.findings,
            skipped_agents=cached.skipped_agents,
            degraded=False,
            total_duration_ms=int((time.time() - start_time) * 1000),
            errors=[],
        )
        logger.info(
            f"评审完成（来自缓存） | 状态: {response.status.value} "
            f"| Finding数量: {len(response.findings)}"
        )
        return response

    # 情况 B：正常流程 / 降级流程 — 从 state 中取各字段
    result: Optional[ReviewResult] = state.get("result")
    failed_agents: list[str] = state.get("failed_agents", [])
    degraded: bool = state.get("degraded", False)
    errors: list[str] = state.get("errors", [])

    # 如果触发了降级，记录降级事件
    if degraded:
        await metrics_collector.record_degradation()

    if result is None:
        result = ReviewResult(
            status=ReviewStatus.PASS,
            findings=[],
            skipped_agents=[],
            degraded=degraded,
            total_duration_ms=0,
        )

    # 步骤 4：构建 API 响应
    # 计算总执行时间（从收到请求到返回响应）
    total_ms = int((time.time() - start_time) * 1000)

    response = ReviewResponse(
        status=result.status,
        findings=result.findings,
        skipped_agents=result.skipped_agents + failed_agents,
        degraded=degraded,
        total_duration_ms=total_ms,
        errors=errors,
    )

    logger.info(
        f"评审完成 | 状态: {response.status.value} "
        f"| Finding数量: {response.findings_count} "
        f"| 耗时: {total_ms}ms "
        f"| 降级: {degraded} "
        f"| 跳过Agent: {response.skipped_agents}"
    )

    return response


@router.get("/metrics", status_code=200)
async def get_metrics(req: Request):
    """
    监控指标端点

    返回当前服务的运行指标，由 MetricsCollector 在内存中维护：
    - total_reviews:     总评审次数（进程启动以来的累计值）
    - cache_hit_rate:    缓存命中率（0.0 ~ 1.0）
    - degradation_count: 降级触发次数
    - agent_stats:       各 Agent 平均耗时（当前为空，后续可扩展）

    所有计数器在进程重启后清零。
    """

    metrics_collector = req.app.state.metrics

    # 获取当前统计数据
    return await metrics_collector.get_stats()