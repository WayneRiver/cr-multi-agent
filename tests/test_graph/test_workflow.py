"""
tests/test_graph/test_workflow.py — 工作流集成测试

单文件覆盖 4 种核心场景，Mock 所有外部依赖。
"""

from langchain_core.messages import AIMessage

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.base import BaseAgent
from src.graph.workflow import build_workflow
from src.models.review_input import ReviewInput, Language
from src.models.review_result import ReviewResult, ReviewStatus
from src.models.finding import Finding, Severity, FindingType


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sample_input():
    """一条正常的 Python 评审请求"""
    return ReviewInput(
        code="def add(a, b):\n    return a + b\n",
        language=Language.PYTHON,
        pr_description="",
        commit_hash="abc123def456",
    )


@pytest.fixture
def mock_llm():
    """
    create_agent 的 model 参数，只要求是一个对象。
    BaseAgent.run 会被 patch 掉，所以它不会被真正调用。
    """
    return MagicMock()


@pytest.fixture
def mock_redis():
    """模拟 Redis 缓存（fakeredis）"""
    import fakeredis.aioredis
    from src.cache.redis_client import RedisCache

    fake_redis = fakeredis.aioredis.FakeRedis()
    cache = RedisCache(redis_url="redis://fake")
    cache._redis = fake_redis
    return cache


@pytest.fixture
def mock_mcp():
    """模拟 MCP 客户端"""
    return AsyncMock()


# ============================================================
# 辅助函数
# ============================================================

def make_mcp_ruff_output(issues: list[dict]) -> dict:
    """构造 MCP Ruff 返回格式"""
    content_text = json.dumps({"issues": issues}, ensure_ascii=False)
    return {"content": [{"type": "text", "text": content_text}]}

def make_llm_response(findings: list[dict]) -> str:
    """把 dict 列表变成 LLM 返回的 JSON 字符串"""
    return json.dumps(findings, ensure_ascii=False)


# ============================================================
# 测试 1：正常流程（Python，4 Agent 全部成功）
# ============================================================

@pytest.mark.asyncio
async def test_full_flow_python_all_success(sample_input, mock_llm, mock_redis, mock_mcp):
    """
    场景：Python 代码，Planner 启用 4 个 Agent，全部成功
    """
    # MCP Ruff：返回一个 warning
    mock_mcp.call_tool.return_value = make_mcp_ruff_output([
        {"rule": "E302", "message": "expected 2 blank lines",
         "line": 1, "end_line": 1, "severity": "warning", "fixable": True}
    ])

    # Mock BaseAgent.run：所有 LLM Agent（安全、逻辑、可读）都返回空
    with patch.object(BaseAgent, 'run', new_callable=AsyncMock) as mock_run:
        mock_run.return_value = []

        graph = await build_workflow(
            llm=mock_llm,
            redis_cache=mock_redis,
            mcp_client=mock_mcp,
        )
        result_state = await graph.ainvoke({"input": sample_input})

    final = result_state["result"]
    assert isinstance(final, ReviewResult)
    assert final.status in (ReviewStatus.PASS, ReviewStatus.PARTIAL_PASS)
    assert final.skipped_agents == []
    assert final.degraded is False
    # LLM Agent 都被 mock 跳过，只有 Ruff 找到 1 条
    assert len(final.findings) == 1


# ============================================================
# 测试 2：缓存命中
# ============================================================

@pytest.mark.asyncio
async def test_cache_hit(sample_input, mock_llm, mock_redis, mock_mcp):
    """
    场景：同一 commit 之前评审过，Redis 有缓存
    """
    cached_result = ReviewResult(
        status=ReviewStatus.PASS,
        findings=[],
        skipped_agents=[],
        degraded=False,
        total_duration_ms=100,
    )
    await mock_redis.set(sample_input.commit_hash, cached_result)

    graph = await build_workflow(
        llm=mock_llm,
        redis_cache=mock_redis,
        mcp_client=mock_mcp,
    )
    result_state = await graph.ainvoke({"input": sample_input})

    assert result_state.get("cache_hit") is True
    assert result_state["result"] is not None
    assert result_state["result"].status == ReviewStatus.PASS
    assert result_state["result"].findings_count == 0


# ============================================================
# 测试 3：格式驳回（Ruff 报 > 3 个 error）
# ============================================================

@pytest.mark.asyncio
async def test_early_stop_reject(sample_input, mock_llm, mock_redis, mock_mcp):
    """
    场景：代码格式问题严重（Ruff 报 4 个 error）→ 直接驳回
    """
    # MCP Ruff 返回 4 个 error
    issues = [
        {"rule": f"F{i}", "message": f"error {i}",
         "line": i, "end_line": i, "severity": "error", "fixable": False}
        for i in range(1, 5)
    ]
    mock_mcp.call_tool.return_value = make_mcp_ruff_output(issues)

    graph = await build_workflow(
        llm=mock_llm,
        redis_cache=mock_redis,
        mcp_client=mock_mcp,
    )
    result_state = await graph.ainvoke({"input": sample_input})

    final = result_state["result"]
    assert final is not None
    assert final.status == ReviewStatus.REJECT
    assert len(final.findings) >= 4


# ============================================================
# 测试 4：Go 语言分支（无 security）
# ============================================================

@pytest.mark.asyncio
async def test_go_language_skips_security(mock_llm, mock_redis, mock_mcp):
    """
    场景：Go 语言 PR，Planner 只启用 style + logic + readability
    """
    go_input = ReviewInput(
        code="package main\n\nfunc main() {\n\tprintln(\"hello\")\n}\n",
        language=Language.GO,
        pr_description="初始化 Go 项目",
    )

    mock_mcp.call_tool.return_value = make_mcp_ruff_output([])

    with patch.object(BaseAgent, 'run', new_callable=AsyncMock) as mock_run:
        mock_run.return_value = []

        graph = await build_workflow(
            llm=mock_llm,
            redis_cache=mock_redis,
            mcp_client=mock_mcp,
        )
        result_state = await graph.ainvoke({"input": go_input})

    assert "security" not in result_state["enabled_agents"]
    assert result_state["result"] is not None
    assert result_state["result"].status == ReviewStatus.PASS

# ============================================================
# 测试 5：部分降级 — 安全员失败，逻辑员 + 可读员成功
# ============================================================

@pytest.mark.asyncio
async def test_partial_degradation_security_fails(sample_input, mock_llm, mock_redis, mock_mcp):
    """
    场景：安全员超时异常，逻辑员+可读员成功
    验证：degraded=True，failed_agents 包含 security，final_findings 应包含 style+logic+readability
    """
    mock_mcp.call_tool.return_value = make_mcp_ruff_output([
        {"rule": "E302", "message": "expected 2 blank lines",
         "line": 1, "end_line": 1, "severity": "warning", "fixable": True}
    ])

    with patch.object(BaseAgent, 'run', new_callable=AsyncMock) as mock_run:
        # 按 Agent 顺序：security→超时，logic→空，readability→空
        # 注意：security 在 early_stop 里先跑，logic 在 run_layer2，readability 在 run_layer3
        mock_run.side_effect = [
            Exception("安全员超时"),  # security_checker.run()
            [],                       # logic_checker.run()
            [],                       # readability_checker.run()
        ]

        graph = await build_workflow(
            llm=mock_llm,
            redis_cache=mock_redis,
            mcp_client=mock_mcp,
        )
        result_state = await graph.ainvoke({"input": sample_input})

    final = result_state["result"]
    assert result_state["degraded"] is True
    assert "security" in result_state["failed_agents"]
    assert isinstance(final, ReviewResult)
    assert final.degraded is True


# ============================================================
# 测试 6：全量 fallback — 3 个 LLM Agent 全部失败
# ============================================================

@pytest.mark.asyncio
async def test_full_fallback_all_agents_fail(sample_input, mock_llm, mock_redis, mock_mcp):
    """
    场景：安全员+逻辑员+可读员全部超时，成功数 < 2 → 触发 fallback
    验证：degraded=True，failed_agents 包含失败 Agent，fallback 返回 findings
    """
    mock_mcp.call_tool.return_value = make_mcp_ruff_output([])

    # 需要让 fallback 的 llm.ainvoke 也能正常返回
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content=make_llm_response([])))

    with patch.object(BaseAgent, 'run', new_callable=AsyncMock) as mock_run:

        mock_run.side_effect = [Exception("超时")] * 6

        graph = await build_workflow(
            llm=mock_llm,
            redis_cache=mock_redis,
            mcp_client=mock_mcp,
        )
        result_state = await graph.ainvoke({"input": sample_input})

    final = result_state["result"]
    assert final is not None
    assert result_state["degraded"] is True
    assert len(result_state["failed_agents"]) >= 2


# ============================================================
# 测试 7：Planner 精简模式（PR 含"格式化"）
# ============================================================

@pytest.mark.asyncio
async def test_planner_reduced_mode(mock_llm, mock_redis, mock_mcp):
    """
    场景：PR 描述含"格式化"，Planner 只启用 style + readability
    验证：enabled_agents == ["style", "readability"]
    """
    format_input = ReviewInput(
        code="def foo():\n    pass\n",
        language=Language.PYTHON,
        pr_description="代码格式化",
    )

    mock_mcp.call_tool.return_value = make_mcp_ruff_output([])

    graph = await build_workflow(
        llm=mock_llm,
        redis_cache=mock_redis,
        mcp_client=mock_mcp,
    )
    result_state = await graph.ainvoke({"input": format_input})

    assert result_state["enabled_agents"] == ["style", "readability"]
    assert result_state["result"] is not None


# ============================================================
# 测试 8：JavaScript 分支 — 无 logic
# ============================================================

@pytest.mark.asyncio
async def test_javascript_skips_logic(mock_llm, mock_redis, mock_mcp):
    """
    场景：JS 语言，Planner 只启用 style + security + readability，无 logic
    验证：enabled_agents == ["style", "security", "readability"]
    """
    js_input = ReviewInput(
        code="function add(a, b) { return a + b }",
        language=Language.JAVASCRIPT,
    )

    mock_mcp.call_tool.return_value = make_mcp_ruff_output([])

    with patch.object(BaseAgent, 'run', new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = [[], []]  # security + readability

        graph = await build_workflow(
            llm=mock_llm,
            redis_cache=mock_redis,
            mcp_client=mock_mcp,
        )
        result_state = await graph.ainvoke({"input": js_input})

    assert "logic" not in result_state["enabled_agents"]
    assert result_state["enabled_agents"] == ["style", "security", "readability"]
    assert result_state["result"] is not None

