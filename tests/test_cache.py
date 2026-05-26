"""
tests/test_cache.py - Redis 缓存层单元测试

使用 fakeredis 模拟 Redis，不依赖真实 Redis 服务器。
覆盖：写后读、缓存未命中、Redis 未配置、close 后重建
"""

import pytest
from src.cache.redis_client import RedisCache
from src.models.review_result import ReviewResult, ReviewStatus
from src.models.finding import Finding, Severity, FindingType


class TestRedisCache:
    """RedisCache 的单元测试"""

    @pytest.fixture
    def sample_result(self):
        """创建一个示例评审结果"""
        return ReviewResult(
            status=ReviewStatus.PASS,
            findings=[
                Finding(
                    severity=Severity.INFO,
                    type=FindingType.STYLE,
                    file="main.py",
                    line_start=1,
                    line_end=1,
                    title="测试问题",
                )
            ],
            total_duration_ms=1234,
        )

    def _make_cache(self):
        """用 fakeredis 创建缓存实例（避免依赖真实 Redis）"""
        import fakeredis.aioredis

        cache = RedisCache(redis_url="redis://localhost:6379/0")
        cache._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        return cache

    @pytest.mark.asyncio
    async def test_set_and_get(self, sample_result):
        """写后读：set 写入，get 应返回相同内容"""
        cache = self._make_cache()
        await cache.set("abc123", sample_result)

        cached = await cache.get("abc123")
        assert cached is not None
        assert cached.status == sample_result.status
        assert cached.total_duration_ms == 1234
        assert len(cached.findings) == 1

    @pytest.mark.asyncio
    async def test_cache_miss(self):
        """缓存未命中：不存在的 hash 应返回 None"""
        cache = self._make_cache()
        result = await cache.get("nonexistent_hash")
        assert result is None

    @pytest.mark.asyncio
    async def test_redis_disabled(self, sample_result):
        """Redis 未配置时，set 静默跳过，get 返回 None"""
        cache = RedisCache(redis_url=None)

        await cache.set("abc123", sample_result)
        result = await cache.get("abc123")
        assert result is None

    @pytest.mark.asyncio
    async def test_close_and_reuse(self, sample_result):
        """close 后重建连接仍能正常工作"""
        cache = self._make_cache()

        await cache.set("abc123", sample_result)
        await cache.close()

        # 重建连接（模拟生产环境 lifespan 重启）
        import fakeredis.aioredis
        cache._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

        await cache.set("def456", sample_result)
        cached = await cache.get("def456")
        assert cached is not None

    @pytest.mark.asyncio
    async def test_multiple_findings_preserved(self):
        """多条 Finding 的完整结果应完整还原"""
        cache = self._make_cache()
        result = ReviewResult(
            status=ReviewStatus.REJECT,
            findings=[
                Finding(severity=Severity.HIGH, type=FindingType.SECURITY,
                        file="app.py", line_start=1, line_end=1, title="漏洞1"),
                Finding(severity=Severity.MEDIUM, type=FindingType.LOGIC,
                        file="app.py", line_start=5, line_end=5, title="逻辑问题"),
                Finding(severity=Severity.LOW, type=FindingType.READABILITY,
                        file="app.py", line_start=10, line_end=10, title="可读性问题"),
            ],
            total_duration_ms=5000,
            degraded=True,
        )

        await cache.set("multi", result)
        cached = await cache.get("multi")

        assert cached is not None
        assert cached.status == ReviewStatus.REJECT
        assert cached.degraded is True
        assert len(cached.findings) == 3
