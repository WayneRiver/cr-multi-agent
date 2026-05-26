"""
src/utils/metrics.py — 内存指标收集器

在 FastAPI 进程内用简单的计数器追踪核心指标：
- 总评审次数
- 缓存命中次数 → 计算缓存命中率
- 降级触发次数

所有计数器在进程内有效，重启后清零。
适合阶段10的量级，后续可以替换为 Redis 持久化计数器。

线程安全说明：
    FastAPI 基于 asyncio 单线程事件循环，不存在真正的多线程竞态。
    但为了代码健壮性和未来可能的 Workers 扩展，仍然使用 asyncio.Lock。
"""

import asyncio
from src.utils.logger import logger

class MetricsCollector:
    """
    内存指标收集器

    使用方式：
        metrics = MetricsCollector()

        # 在 /review 路由中记录
        metrics.record_review()
        metrics.record_cache_hit()
        metrics.record_degradation()

        # 在 /metrics 路由中读取
        stats = metrics.get_stats()
    """
    def __init__(self):
        """初始化所有计数器为 0"""

        self._total_reviews: int = 0
        self._cache_hits: int = 0
        self._degradation_count: int = 0

        # asyncio.Lock 保证并发安全
        self._lock = asyncio.Lock()

    async def record_review(self) -> None:
        """
        记录一次评审请求（无论成功还是失败）

        在 /review 路由开始时调用。
        用于计算总评审次数。
        """
        async with self._lock:
            self._total_reviews += 1
            logger.debug(
                f"[Metrics] 评审计数 +1 | 当前总数: {self._total_reviews}"
            )

    async def record_cache_hit(self) -> None:
        """
        记录一次缓存命中

        在 /review 路由中，当 state["cache_hit"] 为 True 时调用。
        缓存命中率 = cache_hits / total_reviews
        """
        async with self._lock:
            self._cache_hits += 1
            logger.debug(
                f"[Metrics] 缓存命中 +1 | 当前命中数: {self._cache_hits}"
            )

    async def record_degradation(self) -> None:
        """
        记录一次降级事件

        在 /review 路由中，当 state["degraded"] 为 True 时调用。
        """
        async with self._lock:
            self._degradation_count += 1
            logger.debug(
                f"[Metrics] 降级事件 +1 | 当前降级次数: {self._degradation_count}"
            )

    async def get_stats(self) -> dict:
        """
        返回当前所有统计指标的字典

        返回值示例：
            {
                "total_reviews": 42,
                "cache_hit_rate": 0.25,
                "degradation_count": 3,
                "agent_stats": {}
            }
        """
        async with self._lock:
            # 计算缓存命中率（防止除零）
            if self._total_reviews > 0:
                cache_hit_rate = round(
                    self._cache_hits / self._total_reviews, 4
                )
            else:
                cache_hit_rate = 0.0

            return {
                "total_reviews": self._total_reviews,
                "cache_hit_rate": cache_hit_rate,
                "degradation_count": self._degradation_count,
                "agent_stats": {}, 
            }
