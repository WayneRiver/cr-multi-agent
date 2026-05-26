"""
FastAPI 应用入口

负责：
1. 创建 FastAPI 应用
2. 注册中间件
3. 注册路由
4. 管理应用生命周期（启动/关闭时的初始化）
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from langchain_openai import ChatOpenAI
from src.api.routes import router
from src.api.middleware import LoggingMiddleware, ExceptionHandlingMiddleware
from src.cache.redis_client import RedisCache
from src.graph.workflow import build_workflow
from src.tools.mcp_client import MCPClient
from src.utils.logger import logger
from src.utils.metrics import MetricsCollector
from config import settings, validate_required_settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理器

    启动时（yield 之前）：
        1. 验证必要配置（OPENAI_API_KEY 等）
        2. 创建 ChatOpenAI 实例（连接阿里云百炼/OpenAI 兼容 API）
        3. 创建 RedisCache 实例（评审结果缓存，可选）
        4. 创建 MCPClient 并连接（规范员通过 MCP 调用 Ruff）
        5. 编译 LangGraph 工作流图
        6. 将上述实例注入 app.state，供路由函数通过 request.app.state 访问

    关闭时（yield 之后）：
        1. 关闭 Redis 连接池
        2. 断开 MCP 客户端
    """
    # ========== 启动阶段 ==========
    logger.info("=" * 50)
    logger.info("Code Review Multi-Agent System 启动中...")
    logger.info("=" * 50)
    
    # 步骤 1：验证必要配置
    try:
        validate_required_settings()
        logger.info("✓ 配置验证通过")
    except ValueError as e:
        logger.error(f"✗ 配置验证失败: {e}")
        raise  # 配置错误，不让应用启动

    # 步骤 2：创建 ChatOpenAI 实例
    llm = ChatOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
    )
    logger.info(f"✓ LLM 已初始化 | 模型: {settings.llm_model} | 地址: {settings.openai_base_url}")

    # 步骤 3：创建 RedisCache 实例
    redis_cache = RedisCache(redis_url=settings.redis_url)
    if settings.redis_url:
        logger.info(f"✓ Redis 缓存已配置 | TTL: {settings.cache_ttl_days} 天")
    else:
        logger.info("⚠ Redis 未配置，缓存功能禁用")

    # 步骤 4：创建 MCPClient 实例
    mcp_client = MCPClient(timeout=settings.style_checker_timeout)
    try:
        await mcp_client.connect()
        logger.info("✓ MCP 客户端已连接（mcp-server-analyzer）")
    except Exception as e:
        logger.warning(f"⚠ MCP 客户端连接失败（规范员将不可用）: {e}")
    
    # 步骤 5：编译 LangGraph 工作流图
    logger.info("正在编译 LangGraph 工作流...")
    graph = await build_workflow(
        llm=llm,
        redis_cache=redis_cache,
        mcp_client=mcp_client,
    )
    logger.info("✓ LangGraph 工作流编译完成")

    # 步骤 6：注入 app.state
    # FastAPI 的 app.state 是一个通用的属性容器
    # 路由函数中可以通过 request.app.state.xxx 访问这些实例
    app.state.settings = settings
    app.state.llm = llm
    app.state.redis_cache = redis_cache
    app.state.mcp_client = mcp_client
    app.state.graph = graph
    app.state.metrics = MetricsCollector()
    
    logger.info("✓ 应用启动完成，等待请求...")
    logger.info("=" * 50)
    
    # yield 之前的代码是启动时执行
    yield
    
    # ========== 关闭阶段 ==========
    logger.info("Code Review Multi-Agent System 正在关闭...")
    
    # 关闭 Redis 连接池（释放所有 TCP 连接到 Redis 服务器）
    await redis_cache.close()
    logger.info("✓ Redis 连接池已关闭")

    # 关闭 MCP 客户端（终止 mcp-server-analyzer 子进程，释放资源）
    await mcp_client.close()
    logger.info("✓ MCP 客户端已断开")

    logger.info("✓ 应用已关闭")


# 创建 FastAPI 应用实例
app = FastAPI(
    title=settings.project_name,
    description="基于多智能体协作的代码评审系统",
    version="0.1.0",
    lifespan=lifespan,  # 注册生命周期管理器
)

# 注册中间件（注意顺序：后添加的先执行）
# ExceptionHandlingMiddleware 应该在最外层，所以最后添加
app.add_middleware(LoggingMiddleware)
app.add_middleware(ExceptionHandlingMiddleware)

# 注册路由
app.include_router(router)
