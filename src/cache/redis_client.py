"""
src/cache/redis_client.py - Redis 缓存层

负责评审结果的缓存读写，避免对同一 commit 重复评审。
Key 格式：cr:result:{commit_hash}
序列化方式：ReviewResult.model_dump_json() → JSON 字符串
过期时间：默认 7 天（由 config.py 的 cache_ttl_days 控制）

"""

from typing import Optional
from src.models.review_result import ReviewResult
from src.utils.logger import logger


class RedisCache:
    """
    Redis 缓存客户端 —— 专为缓存 ReviewResult 设计

    封装了 redis.asyncio.Redis 的常用操作：
    - get()：根据 commit_hash 取缓存
    - set()：将评审结果缓存到 Redis
    - close()：关闭连接池

    如果 Redis 未配置（redis_url 为 None），所有方法静默跳过。
    这样主流程不需要判断 Redis 是否可用，直接调用即可。
    """

    # Redis key 的前缀
    _KEY_PREFIX = "cr:result:"

    def __init__(self, redis_url: Optional[str] = None):
        self._redis_url = redis_url
        self._redis = None

    async def _get_redis(self):
        """
        获取 Redis 连接实例（懒加载）

        首次调用时通过 redis_url 创建 redis.asyncio.Redis 实例，
        后续调用复用已有实例。

        如果 redis_url 为 None（Redis 未配置），返回 None，
        所有后续操作静默跳过。

        返回：
            redis.asyncio.Redis | None
        """
        if not self._redis_url:
            return None
        
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.Redis.from_url(
                self._redis_url,
                decode_responses=True, # 自动将 bytes 解码为 str
            )
            logger.info("[RedisCache] 连接已建立")

        return self._redis

    async def get(self, commit_hash: str) -> Optional[ReviewResult]:
        """
        根据 commit_hash 从 Redis 获取缓存的评审结果

        参数：
            commit_hash: commit 的 SHA 值

        返回：
            ReviewResult | None
            — 缓存命中返回 ReviewResult 实例
            — 未命中返回 None
            — Redis 不可用或发生异常也返回 None（不影响主流程）

        执行流程：
            _get_redis() → 拼 key → redis.get(key) → model_validate_json
        """
        redis = await self._get_redis()
        if redis is None:
            return None

        key = f"{self._KEY_PREFIX}{commit_hash}"

        try:
            data = await redis.get(key)
            if data is None:
                logger.debug(f"[RedisCache] 缓存未命中 | key: {key}")
                return None

            result = ReviewResult.model_validate_json(data)
            logger.info(f"[RedisCache] 缓存命中 | key: {key}")
            return result

        except Exception as e:
            # 捕获所有异常（连接超时、解析失败等），
            # 确保缓存异常不会传播到上层业务流程
            logger.warning(f"[RedisCache] 读取缓存异常: {e}")
            return None

    async def set(
        self,
        commit_hash: str,
        result: ReviewResult,
        ttl_days: int = 7,
    ) -> None:
        """
        将评审结果缓存到 Redis

        参数：
            commit_hash: commit 的 SHA 值，用作 key 的一部分
            result:      评审结果（ReviewResult 实例）
            ttl_days:    过期时间（天），默认 7 天
                         到期后 Redis 自动删除该 key

        存储命令：
            Redis SETEX key seconds value
            — 原子操作：设置值和过期时间同时完成
            — 避免分开 SET + EXPIRE 可能出现的竞态
        """
        redis = await self._get_redis()
        if redis is None:
            return

        key = f"{self._KEY_PREFIX}{commit_hash}"
        ttl_seconds = ttl_days * 24 * 3600

        try:
            # model_dump_json() 将 Pydantic 模型序列化为 JSON 字符串
            serialized = result.model_dump_json()

            await redis.setex(key, ttl_seconds, serialized)
            logger.info(
                f"[RedisCache] 缓存写入成功 | key: {key} | "
                f"TTL: {ttl_days}天 | 大小: {len(serialized)} 字节"
            )

        except Exception as e:
            # 写入失败不抛异常，只记录日志
            # 下次同一 commit 评审时会尝试重新写入
            logger.warning(f"[RedisCache] 写入缓存异常: {e}")

    async def close(self) -> None:
        """
        关闭 Redis 连接池

        释放与 Redis 服务器的所有连接。

        调用 close() 后，Redis 连接池被释放，
        但实例仍可继续调用 get()/set() — 下次调用时会自动重建连接。
        """
        if self._redis is not None:
            try:
                await self._redis.aclose()
                logger.info("[RedisCache] 连接已关闭")
            except Exception as e:
                logger.warning(f"[RedisCache] 关闭连接异常: {e}")
            finally:
                self._redis = None


                
        
