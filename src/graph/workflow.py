"""
src/graph/workflow.py — LangGraph 工作流组装

用 StateGraph 把 8 个节点 + 2 条条件边拼成完整工作流，
提供 build_workflow() 函数，在 main.py lifespan 中调用一次。

使用方式：
    graph = await build_workflow(
        llm=llm,
        redis_cache=redis_cache,
        mcp_client=mcp_client,
    )
    result = await graph.ainvoke({"input": review_input})
"""

from langgraph.graph import StateGraph, START, END

from src.graph.state import ReviewState
from src.graph.nodes import (
    create_cache_check_node,
    create_plan_node,
    create_early_stop_node,
    create_run_layer2_node,
    create_run_layer3_node,
    create_degradation_handler_node,
    create_aggregate_node,
    create_cache_write_node,
)
from src.graph.edges import after_cache, after_early_stop

from src.agents.planner import Planner
from src.agents.style_checker import StyleChecker
from src.agents.security_checker import SecurityChecker
from src.agents.logic_checker import LogicChecker
from src.agents.readability_checker import ReadabilityChecker
from src.cache.redis_client import RedisCache
from src.tools.mcp_client import MCPClient
from src.utils.logger import logger

async def build_workflow(
    llm,
    redis_cache: RedisCache,
    mcp_client: MCPClient,
) -> StateGraph:
    """
    组装并编译 LangGraph 工作流

    参数：
        llm:          ChatOpenAI 实例
        redis_cache:  RedisCache 实例
        mcp_client:   MCPClient 实例（已连接）

    返回：
        编译后的 CompiledStateGraph，可调用 .ainvoke()

    调用约定（LangGraph）：
        - 节点函数的签名：(state: ReviewState) -> dict
        - 返回的 dict 中的 key 会合并回 state（浅合并）
        - 条件边的签名：(ReviewState) -> str，返回值是下一个节点名
    """

    # 1. 创建 Graph 实例
    workflow = StateGraph(ReviewState)

    # 2. 创建各 Agent 实例
    planner = Planner(llm=llm)

    style_checker = StyleChecker(
        mcp_client=mcp_client,
        timeout=15,
    )

    security_checker = SecurityChecker(
        llm=llm,
        timeout=15,
    )

    logic_checker = LogicChecker(
        llm=llm,
        timeout=20,
    )

    readability_checker = ReadabilityChecker(
        llm=llm,
        timeout=15,
    )

    # 3. 注册节点
    workflow.add_node(
        "cache_check",
        create_cache_check_node(redis_cache),
    )
    workflow.add_node(
        "plan",
        create_plan_node(planner),
    )
    workflow.add_node(
        "early_stop",
        create_early_stop_node(style_checker, security_checker),
    )
    workflow.add_node(
        "run_layer2",
        create_run_layer2_node(logic_checker),
    )
    workflow.add_node(
        "run_layer3",
        create_run_layer3_node(readability_checker),
    )
    workflow.add_node(
        "degradation_handler",
        create_degradation_handler_node(
            style_checker=style_checker,
            security_checker=security_checker,
            logic_checker=logic_checker,
            readability_checker=readability_checker,
            llm=llm,
        ),
    )
    workflow.add_node(
        "aggregate",
        create_aggregate_node(),
    )
    workflow.add_node(
        "cache_write",
        create_cache_write_node(redis_cache),
    )

    # 4. 注册边

    # 固定边：无条件地从 A 走到 B
    workflow.add_edge(START, "cache_check")

    # 条件边 1：cache_check 之后
    workflow.add_conditional_edges(
        "cache_check",
        after_cache,
        {
            "plan": "plan",
            "aggregate": "aggregate",
        },
    )

    # 固定边：plan 之后一定到 early_stop
    workflow.add_edge("plan", "early_stop")

    # 条件边 2：early_stop 之后
    workflow.add_conditional_edges(
        "early_stop",
        after_early_stop,
        {
            "run_layer2": "run_layer2",
            "cache_write": "cache_write",
        },
    )

    # 主流程固定边：三层串联
    workflow.add_edge("run_layer2", "run_layer3")
    workflow.add_edge("run_layer3", "degradation_handler")
    workflow.add_edge("degradation_handler", "aggregate")
    workflow.add_edge("aggregate", "cache_write")

    # 最终固定边：缓存写入后结束
    workflow.add_edge("cache_write", END)

    # 5. 编译工作流
    logger.info("[workflow] 图编译完成")
    return workflow.compile()


