"""
src/degradation/handler.py - 降级总调度

编排 Monitor、Fallback、Aggregator 三个模块，
根据 Agent 执行结果判断局势，选择处理策略。
给 LangGraph 阶段 8 的 degradation_handler 节点直接调用。
"""

from src.degradation.monitor import DegradationMonitor
from src.degradation.fallback import single_agent_fallback
from src.agents.aggregator import Aggregator
from src.models.finding import Finding
from src.utils.logger import get_degradation_logger

async def degradation_handler(
    enabled_agents: list[str],
    findings_by_agent: dict[str, list[Finding] | None],
    errors_by_agent: dict[str, str | None],
    retried_by_agent: dict[str, bool],
    review_input,       # ReviewInput，大面积失败时传给 Fallback
    llm,                # ChatOpenAI，大面积失败时传给 Fallback
) -> dict:
    """
    降级总调度 —— 给 LangGraph 节点用的顶层函数

    
    返回：
        dict — 包含以下字段：
            {
                "final_findings": list[Finding],    # 最终的问题列表
                "failed_agents":  list[str],         # 失败的 Agent 名称列表
                "degraded":       bool,              # 是否触发了降级
                "events":         list[dict],         # 降级事件日志
                "retried_agents": list[str],         # 被重试过的 Agent 列表
            }

    决策树（三种情况）：
        success_count < 2  → 大面积失败 → Fallback 单 LLM 兜底
        0 < failed < total → 部分降级   → 跳过失败，成功的结果 merge
        failed == 0        → 全部成功   → 直接 merge
    """

    degradation_logger = get_degradation_logger()

    # 1. 创建 Monitor 记分牌，记录所有 Agent 的执行状态
    monitor = DegradationMonitor(enabled_agents)

    for agent_name in enabled_agents:
        findings = findings_by_agent.get(agent_name)
        error = errors_by_agent.get(agent_name)
        retried = retried_by_agent.get(agent_name, False)
        monitor.record(agent_name, findings=findings, error=error, retried=retried)

    total = len(enabled_agents)
    failed = monitor.get_failed()
    success = monitor.success_count()

    degradation_logger.warning(
        f"降级调度 | 总计: {total} | 成功: {success} | 失败: {failed}"
    )

    # 2. 决策树: 三种情况的分流

    # 情况 A：大面积失败（成功数 < 2）→ Fallback 兜底
    if success < 2:
        degradation_logger.warning(
            f"触发大面积降级：成功 Agent 数 {success} < 2，启用单 Agent 兜底"
        )

        fallback_findings = await single_agent_fallback(review_input, llm)

        return {
            "final_findings": fallback_findings,
            "failed_agents": failed,
            "degraded": True,
            "events": monitor.get_events(),
            "retried_agents": [
                name for name, retried in retried_by_agent.items() if retried
            ],
        }

    # 情况 B：部分 Agent 失败 → 跳过失败的，成功的结果 merge
    if failed and len(failed) < total:
        degradation_logger.info(f"部分降级：跳过 {len(failed)} 个 Agent ，使用成功结果继续")

        # 只取成功 Agent 的 finding 列表
        success_findings: dict[str, list[Finding]] = {}
        for agent_name in enabled_agents:
            f = findings_by_agent.get(agent_name)
            if f is not None:
                success_findings[agent_name] = f

        merged = Aggregator.merge(success_findings)

        return {
            "final_findings": merged,
            "failed_agents": failed,
            "degraded": True,
            "events": monitor.get_events(),
            "retried_agents": [
                name for name, retried in retried_by_agent.items() if retried
            ],
        }

    # 情况 C：全部成功 → 直接 merge
    all_findings: dict[str, list[Finding]] = {}
    for agent_name in enabled_agents:
        f = findings_by_agent.get(agent_name)
        if f is not None:
            all_findings[agent_name] = f

    merged = Aggregator.merge(all_findings)

    return {
        "final_findings": merged,
        "failed_agents": [],
        "degraded": False,
        "events": monitor.get_events(),
        "retried_agents": [],
    }