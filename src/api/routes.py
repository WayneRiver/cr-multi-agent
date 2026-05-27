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
from src.models.review_input import ReviewInput, Language
from src.models.review_result import ReviewStatus, ReviewResult
from src.utils.logger import logger

from src.integrations.webhook import (
    verify_signature,
    parse_event,
    fetch_pr_diff,
    detect_language,
    GITHUB_SIGNATURE_HEADER,
    GITHUB_EVENT_HEADER,
)
from src.integrations.github import GitHubClient


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

@router.post("/webhook", status_code=200)
async def github_webhook(req: Request):
    """
    GitHub Webhook 接收端点

    这是阶段 11（GitHub 集成）的核心端点。
    GitHub 在 PR 创建或更新时会向此端点发 POST 请求。

    完整处理流程：
    1. 读取原始请求体（bytes）
    2. HMAC-SHA256 验签 → 不通过返回 403
    3. 解析事件类型 → 非 PR 事件返回 200（ack 但不处理）
    4. 提取 PR 信息（owner、repo、pr_number、commit_hash 等）
    5. 从 GitHub 拉取 PR diff 文本
    6. 推测编程语言
    7. 构造 ReviewInput → 调 graph.ainvoke() 走完整评审流程
    8. 将评审结果发布为 PR 评论

    返回：
        200 — 处理完成（无论结果如何都返回 200，
              GitHub 如果收到非 200 会重试）
        400 — 请求体为空
        403 — 签名验证失败

    GitHub Webhook 文档：
        https://docs.github.com/en/webhooks/about-webhooks
    """
    # 步骤 1：读取原始请求体
    # 必须用 bytes，因为 HMAC 验签以原始字节为输入
    payload_body = await req.body()
    if not payload_body:
        return {"error": "empty body"}

    # 步骤 2：HMAC-SHA256 签名验证
    webhook_secret = req.app.state.settings.github_webhook_secret
    if webhook_secret:
        signature = req.headers.get(GITHUB_SIGNATURE_HEADER, "")
        if not verify_signature(payload_body, signature, webhook_secret):
            return {"error": "invalid signature"}
    else:
        # 没有配置 secret 时记录警告（开发环境可接受，生产环境不安全）
        logger.warning("[Webhook] 未配置 GITHUB_WEBHOOK_SECRET，跳过签名验证")

    # 步骤 3：获取事件类型
    event_type = req.headers.get(GITHUB_EVENT_HEADER, "")

    # 步骤 4：解析 JSON body
    try:
        import json
        payload = json.loads(payload_body)
    except json.JSONDecodeError:
        return {"error": "invalid json"}

    # 步骤 5：提取 PR 关键信息
    pr_info = parse_event(event_type, payload)
    if pr_info is None:
        # 不是我们关心的事件类型（如 push、issue 等），
        # 返回 200 告知 GitHub 已收到，不处理
        return {"status": "ignored", "reason": f"event type '{event_type}' not handled"}

    # 步骤 6：拉取 PR diff
    github_token = req.app.state.settings.github_token
    if not github_token:
        logger.warning("[Webhook] 未配置 GITHUB_TOKEN，跳过 diff 拉取")
        return {"status": "skipped", "reason": "github_token not configured"}

    diff_text = await fetch_pr_diff(pr_info["diff_url"], github_token)
    if not diff_text:
        logger.warning(
            f"[Webhook] diff 为空 | "
            f"{pr_info['owner']}/{pr_info['repo']}#{pr_info['pr_number']}"
        )
        return {
            "status": "skipped",
            "reason": "empty diff (可能是空 PR 或二进制文件变更)",
        }

    # 步骤 7：推测编程语言
    # 从 diff 文本中提取所有变更的文件路径
    file_paths = _extract_file_paths_from_diff(diff_text)
    lang_str = detect_language(file_paths)
    language = Language(lang_str)

    # 步骤 8：构造 ReviewInput
    review_input = ReviewInput(
        code=diff_text,
        language=language,
        pr_description=pr_info.get("pr_body") or pr_info.get("pr_title", ""),
        commit_hash=pr_info["commit_hash"],
    )

    logger.info(
        f"[Webhook] 开始评审 | "
        f"{pr_info['owner']}/{pr_info['repo']}#{pr_info['pr_number']} | "
        f"语言: {lang_str} | "
        f"diff 大小: {len(diff_text)} 字符"
    )

    # 步骤 9：调用 LangGraph 工作流执行完整评审
    import time
    start_time = time.time()
    graph = req.app.state.graph

    try:
        state = await graph.ainvoke({"input": review_input})
    except Exception as e:
        logger.opt(exception=True).error(f"[Webhook] 工作流执行异常: {e}")
        return {"status": "error", "reason": str(e)}

    # 步骤 10：提取评审结果
    result = state.get("result")
    if result is None:
        return {"status": "completed", "findings_count": 0}

    total_ms = int((time.time() - start_time) * 1000)
    logger.info(
        f"[Webhook] 评审完成 | {len(result.findings)} 条 finding | "
        f"耗时: {total_ms}ms | status: {result.status.value}"
    )

    # 步骤 11：发布 PR 评论
    github_client: GitHubClient | None = getattr(
        req.app.state, "github_client", None
    )
    if github_client is not None:
        posted = await github_client.post_pr_comment(
            owner=pr_info["owner"],
            repo=pr_info["repo"],
            pr_number=pr_info["pr_number"],
            findings=result.findings,
        )
        if not posted:
            logger.warning("[Webhook] PR 评论发布失败（评审结果已生成）")
    else:
        logger.warning("[Webhook] github_client 未初始化，跳过评论发布")

    return {
        "status": "completed",
        "findings_count": result.findings_count,
        "review_status": result.status.value,
        "duration_ms": total_ms,
    }


def _extract_file_paths_from_diff(diff_text: str) -> list[str]:
    """
    从 unified diff 文本中提取变更的文件路径列表

    参数：
        diff_text: 完整的 unified diff 文本

    返回：
        文件路径列表，如 ["src/main.py", "tests/test_main.py"]

    unified diff 格式规则：
        每个文件的 diff 块以 diff --git a/<path> b/<path> 开头
        后续的 --- a/<path> 和 +++ b/<path> 也包含路径信息

    实现：
        用正则匹配 diff --git a/(.+) b/(.+) 行，提取文件路径。
        这个格式在每个文件的 diff 块开头，比 ---/+++ 行更可靠。
    """
    import re

    # 匹配 diff --git a/path/to/file b/path/to/file
    # a/ 和 b/ 是 Git 的前缀约定，不是实际路径的一部分
    pattern = r"^diff --git a/(.+?) b/(.+?)$"

    paths: list[str] = []
    for line in diff_text.split("\n"):
        match = re.match(pattern, line)
        if match:
            # match.group(1) 和 match.group(2) 通常是同一个文件路径
            # 取 a/ 后面的路径即可
            paths.append(match.group(1))

    return paths