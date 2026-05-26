"""
tests/test_api.py - API 路由的单元测试

覆盖场景：
1. GET  /health      —— 健康检查正常返回
2. POST /review      —— 正常流程（graph 返回完整结果）
3. POST /review      —— 缓存命中场景
4. POST /review      —— 降级场景（部分 Agent 失败）
5. POST /review      —— 请求校验失败（空 code → 422）
6. POST /review      —— 工作流异常（graph 抛异常 → 500）
7. GET  /metrics     —— 监控指标端点
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import router
from src.api.middleware import LoggingMiddleware, ExceptionHandlingMiddleware
from src.models.finding import Finding, Severity, FindingType
from src.models.review_result import ReviewResult, ReviewStatus


# ============================================================
# 测试辅助：创建不依赖外部服务的 FastAPI 应用
# ============================================================

@asynccontextmanager
async def _test_lifespan(app: FastAPI):
    """
    测试用的最小生命周期管理器

    不做任何外部连接（不连 LLM、Redis、MCP），
    只是把控制权交给测试，由测试来设置 app.state 上的 mock 对象。
    """
    yield  # 空的：启动和关闭都不做实际操作


def _make_test_app() -> FastAPI:
    """
    创建用于测试的 FastAPI 应用实例

    使用 test_lifespan 替换真实的 lifespan，
    挂载与生产环境相同的路由和中间件。
    """
    app = FastAPI(lifespan=_test_lifespan)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(ExceptionHandlingMiddleware)
    app.include_router(router)
    return app


def _make_sample_finding(**kwargs) -> Finding:
    """快速创建一个示例 Finding，用于构建 mock 返回数据"""
    defaults = {
        "severity": Severity.MEDIUM,
        "type": FindingType.STYLE,
        "file": "main.py",
        "line_start": 10,
        "line_end": 10,
        "title": "测试发现",
    }
    defaults.update(kwargs)
    return Finding(**defaults)


def _make_sample_result(**kwargs) -> ReviewResult:
    """快速创建一个示例 ReviewResult"""
    defaults = {
        "status": ReviewStatus.PASS,
        "findings": [_make_sample_finding()],
        "total_duration_ms": 1500,
    }
    defaults.update(kwargs)
    return ReviewResult(**defaults)


# ============================================================
# 测试类：API 路由
# ============================================================

class TestHealthEndpoint:
    """GET /api/v1/health 的测试"""

    def test_health_returns_ok(self):
        """健康检查端点应返回 200 和正确的 JSON 结构"""
        app = _make_test_app()
        client = TestClient(app)

        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "service" in data
        assert "version" in data


class TestReviewEndpoint:
    """POST /api/v1/review 的测试"""

    @pytest.fixture
    def mock_graph(self):
        """
        创建一个 mock 的 LangGraph CompiledStateGraph

        ainvoke() 默认返回一个正常流程的结果，
        各测试用例可以覆盖 return_value 来模拟不同场景。
        """
        graph = MagicMock()
        graph.ainvoke = AsyncMock(return_value={
            "cache_hit": False,
            "result": _make_sample_result(),
            "failed_agents": [],
            "degraded": False,
            "errors": [],
        })
        return graph

    def _make_client_with_graph(self, mock_graph) -> TestClient:
        """
        创建测试客户端，并把 mock_graph 注入到 app.state 中

        这样路由函数中的 req.app.state.graph 就能拿到被 mock 的 graph。
        """
        app = _make_test_app()
        # 在 lifespan 启动后、请求发送前，把 mock 对象放到 app.state 上
        app.state.graph = mock_graph
        return TestClient(app)

    # ---------- 正常流程 ----------

    def test_review_success(self, mock_graph):
        """正常评审流程：graph 返回完整结果，应返回 201 和 ReviewResponse"""
        client = self._make_client_with_graph(mock_graph)

        response = client.post("/api/v1/review", json={
            "code": "def add(a, b): return a + b",
            "language": "python",
        })

        assert response.status_code == 201
        data = response.json()

        # 验证响应包含了所有预期字段
        assert "status" in data
        assert "findings" in data
        assert "skipped_agents" in data
        assert "degraded" in data
        assert "total_duration_ms" in data
        assert "review_id" in data
        assert "created_at" in data
        assert "errors" in data

        # 验证 graph.ainvoke 被正确调用了
        mock_graph.ainvoke.assert_called_once()
        call_args = mock_graph.ainvoke.call_args[0][0]
        assert "input" in call_args
        assert call_args["input"].code == "def add(a, b): return a + b"
        assert call_args["input"].language.value == "python"

    def test_review_with_all_optional_fields(self, mock_graph):
        """所有可选字段（pr_description、commit_hash）都传入时，正常处理"""
        client = self._make_client_with_graph(mock_graph)

        response = client.post("/api/v1/review", json={
            "code": "def add(a, b): return a + b",
            "language": "java",
            "pr_description": "修复了并发安全问题",
            "commit_hash": "abc123def456",
        })

        assert response.status_code == 201
        # 验证 commit_hash 传到了 graph 调用参数中
        call_args = mock_graph.ainvoke.call_args[0][0]
        assert call_args["input"].commit_hash == "abc123def456"
        assert call_args["input"].pr_description == "修复了并发安全问题"

    # ---------- 缓存命中 ----------

    def test_review_cache_hit(self, mock_graph):
        """缓存命中时：graph 返回 cached_result，应直接返回缓存结果"""
        cached_result = _make_sample_result(
            status=ReviewStatus.PASS,
            findings=[_make_sample_finding(severity=Severity.LOW, title="缓存的问题")],
        )

        mock_graph.ainvoke.return_value = {
            "cache_hit": True,
            "cached_result": cached_result,
        }

        client = self._make_client_with_graph(mock_graph)

        response = client.post("/api/v1/review", json={
            "code": "print('hello')",
            "language": "python",
            "commit_hash": "abc123",
        })

        assert response.status_code == 201
        data = response.json()
        # 缓存命中时 findings 应该来自缓存
        assert data["findings"][0]["title"] == "缓存的问题"
        assert data["degraded"] is False

    # ---------- 降级场景 ----------

    def test_review_degraded(self, mock_graph):
        """部分 Agent 失败时：响应中 degraded=True，包含 failed_agents 信息"""
        mock_graph.ainvoke.return_value = {
            "cache_hit": False,
            "result": _make_sample_result(status=ReviewStatus.PARTIAL_PASS),
            "failed_agents": ["security_checker", "logic_checker"],
            "degraded": True,
            "errors": ["security_checker 超时", "logic_checker 超时"],
        }

        client = self._make_client_with_graph(mock_graph)

        response = client.post("/api/v1/review", json={
            "code": "def foo(): pass",
            "language": "python",
        })

        assert response.status_code == 201
        data = response.json()
        assert data["degraded"] is True
        assert "security_checker" in data["skipped_agents"]
        assert "logic_checker" in data["skipped_agents"]
        assert len(data["errors"]) == 2

    def test_review_reject_status(self, mock_graph):
        """严重问题导致 REJECT 状态时，响应正确反映"""
        mock_graph.ainvoke.return_value = {
            "cache_hit": False,
            "result": _make_sample_result(
                status=ReviewStatus.REJECT,
                findings=[
                    _make_sample_finding(
                        severity=Severity.CRITICAL,
                        type=FindingType.SECURITY,
                        title="SQL 注入漏洞",
                    ),
                ],
            ),
            "failed_agents": [],
            "degraded": False,
            "errors": [],
        }

        client = self._make_client_with_graph(mock_graph)

        response = client.post("/api/v1/review", json={
            "code": "query = 'SELECT * FROM users WHERE id=' + user_input",
            "language": "python",
        })

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "reject"
        assert len(data["findings"]) == 1
        assert data["findings"][0]["severity"] == "critical"

    # ---------- 请求校验失败 ----------

    def test_review_missing_code(self):
        """缺少必填字段 code 时，FastAPI 返回 422"""
        app = _make_test_app()
        client = TestClient(app)

        response = client.post("/api/v1/review", json={
            "language": "python",
        })

        assert response.status_code == 422

    def test_review_empty_code(self):
        """code 为空字符串时，Pydantic 校验失败，返回 422"""
        app = _make_test_app()
        client = TestClient(app)

        response = client.post("/api/v1/review", json={
            "code": "",
            "language": "python",
        })

        assert response.status_code == 422

    def test_review_invalid_language(self):
        """language 为不支持的值时，返回 422"""
        app = _make_test_app()
        client = TestClient(app)

        response = client.post("/api/v1/review", json={
            "code": "fn main() {}",
            "language": "rust",  # 不支持的语言
        })

        assert response.status_code == 422

    def test_review_missing_language(self):
        """缺少 language 字段时，返回 422"""
        app = _make_test_app()
        client = TestClient(app)

        response = client.post("/api/v1/review", json={
            "code": "def foo(): pass",
        })

        assert response.status_code == 422

    # ---------- 工作流异常 ----------

    def test_review_graph_raises_exception(self, mock_graph):
        """graph.ainvoke 抛出异常时，ExceptionHandlingMiddleware 捕获并返回 500

        注意：这里测试的是 graph 层面的未预期异常（如 LLM API 网络故障），
        工作流内部的降级（Agent 超时等）在 degradation_handler 中处理，
        不会抛出异常。
        """
        mock_graph.ainvoke.side_effect = RuntimeError("LLM API 连接超时")

        client = self._make_client_with_graph(mock_graph)

        response = client.post("/api/v1/review", json={
            "code": "def foo(): pass",
            "language": "python",
        })

        assert response.status_code == 500
        data = response.json()
        assert "detail" in data

    def test_review_graph_returns_none_result(self, mock_graph):
        """graph 返回的 state 中 result 为 None（极端情况），应返回空的占位结果"""
        mock_graph.ainvoke.return_value = {
            "cache_hit": False,
            "result": None,
            "failed_agents": [],
            "degraded": False,
            "errors": [],
        }

        client = self._make_client_with_graph(mock_graph)

        response = client.post("/api/v1/review", json={
            "code": "def foo(): pass",
            "language": "python",
        })

        # 应该正常返回（不报 500），只是 findings 为空
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "pass"
        assert data["findings"] == []


class TestMetricsEndpoint:
    """GET /api/v1/metrics 的测试"""

    def test_metrics_returns_ok(self):
        """监控指标端点应返回 200 和基本结构"""
        app = _make_test_app()
        client = TestClient(app)

        response = client.get("/api/v1/metrics")

        assert response.status_code == 200
        data = response.json()
        assert "cache_hit_rate" in data
        assert "agent_stats" in data
        assert "degradation_count" in data
        assert "total_reviews" in data

