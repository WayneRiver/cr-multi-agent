"""
src/graph/nodes.py — 工作流节点函数

工作流中的 8 个节点，每个节点是一个 async 函数：
    cache_check → plan → early_stop → run_layer1 → run_layer2
    → run_layer3 → degradation_handler → aggregate → cache_write

每个节点遵循 LangGraph 节点签名：
    输入: state (ReviewState 字典)
    输出: dict — 要更新的 state 字段（LangGraph 自动合并回 state）

依赖注入采用工厂函数模式：
    外层函数接收依赖（llm、redis、mcp_client 等），
    返回内层真正的节点函数。
    这样在 workflow.py 组装图时注入依赖，节点内部不需要 import 全局变量。
"""

from src.graph.state import ReviewState
from src.cache.redis_client import RedisCache
from src.utils.logger import logger


# 节点 1：cache_check — Redis 缓存检查
def create_cache_check_node(redis_cache: RedisCache):
    """
    缓存检查节点的工厂函数

    参数：
        redis_cache: RedisCache 实例（在 main.py lifespan 中创建）

    返回：
        async 节点函数
    """

    async def cache_check(state: ReviewState) -> dict:
        """
        缓存检查节点

        这是工作流的第一个节点。
        如果缓存命中，后续的 after_cache 条件边会直接跳到 END，
        跳过所有 Agent 执行。

        返回：
            dict — {"cache_hit": bool, "cached_result": ReviewResult | None}
        """
        review_input = state["input"]
        commit_hash = review_input.commit_hash
        if not commit_hash:
            logger.debug("[cache_check] 无 commit_hash，跳过缓存检查")
            return {
                "cache_hit": False,
                "cached_result": None,
            }

        # 查 Redis 缓存
        cached = await redis_cache.get(commit_hash)

        if cached is not None:
            logger.info(f"[cache_check] 缓存命中 | commit: {commit_hash[:8]}...")
            return {
                "cache_hit": True,
                "cached_result": cached,
            }
        else:
            logger.debug(f"[cache_check] 缓存未命中 | commit: {commit_hash[:8]}...")
            return {
                "cache_hit": False,
                "cached_result": None,
            }

    return cache_check

from src.agents.planner import Planner

# 节点 2：plan — Planner 双层决策
def create_plan_node(planner: Planner):
    """
    Planner 决策节点的工厂函数

    参数：
        planner: Planner 实例（在 main.py lifespan 中创建）

    返回：
        async 节点函数
    """

    async def plan(state: ReviewState) -> dict:
        """
        Planner 决策节点

        返回：
            dict — {"enabled_agents": ["style", "security", ...]}
        """
        review_input = state["input"]
        enabled = planner.decide(review_input)

        logger.info(
            f"[plan] 决策完成 | "
            f"语言: {review_input.language.value} | "
            f"启用 Agent ({len(enabled)}): {enabled}"
        )

        return {"enabled_agents": enabled}

    return plan

import asyncio
from src.agents.security_checker import SecurityChecker
from src.agents.style_checker import StyleChecker
from src.models.review_result import ReviewResult, ReviewStatus
from src.models.finding import Severity
from src.agents.aggregator import Aggregator

# 节点 3&4：early_stop — 格式严重问题提前驳回 + 安全员并行
def create_early_stop_node(style_checker: StyleChecker, security_checker: SecurityChecker):
    """
    提前终止判断 + 第一层并行执行

    参数：
        style_checker:    StyleChecker 实例
        security_checker: SecurityChecker 实例

    返回：
        async 节点函数
    """
    async def early_stop(state: ReviewState) -> dict:
        review_input = state["input"]
        enabled_agents = state.get("enabled_agents", [])
        code = review_input.code
        language = review_input.language.value

        # 准备并行任务
        tasks = {}  # {name: coroutine}

        # 规范员
        if "style" in enabled_agents:
            tasks["style"] = style_checker.check(code=code, language=language)

        # 安全员
        if "security" in enabled_agents:
            tasks["security"] = security_checker.run({
                "code": code,
                "language": language,
            })

        if not tasks:
            logger.debug("[early_stop] 没有需要执行的 Agent")
            return {"style_findings": [], "security_findings": []}

        # ---------- 并行执行 ----------
        logger.info(f"[early_stop] 并行启动: {list(tasks.keys())}")
       
        coroutines = list(tasks.values())
        names = list(tasks.keys())

        results = await asyncio.gather(*coroutines, return_exceptions=True)

        # ---------- 收集结果 ----------
        style_findings = []
        security_findings = []
        errors = state.get("errors", [])

        for name, result in zip(names, results):
            if isinstance(result, Exception):
                logger.error(
                    f"[early_stop] {name} 异常: {type(result).__name__}: {result}"
                )
                errors.append(f"{name} 异常: {result}")
                if name == "style":
                    style_findings = None
                elif name == "security":
                    security_findings = None
            else:
                result_list = result if isinstance(result, list) else []
                logger.info(f"[early_stop] {name} 完成，发现 {len(result_list)} 个问题")
                if name == "style":
                    style_findings = result_list
                elif name == "security":
                    security_findings = result_list

        # ---------- 提前终止判断 ----------
        high_plus_count = sum(
            1 for f in (style_findings or [])
            if f.severity in (Severity.CRITICAL, Severity.HIGH)
        )

        if high_plus_count > 3:
            logger.warning(
                f"[early_stop] 严重格式问题 {high_plus_count} > 3，提前驳回"
            )
            result = ReviewResult(
                status=ReviewStatus.REJECT,
                findings=Aggregator.merge({"style_checker": style_findings}),
                skipped_agents=[a for a in enabled_agents if a != "style"],
                degraded=False,
                total_duration_ms=0,
            )
            return {
                "style_findings": style_findings,
                "security_findings": security_findings,
                "result": result,
                "errors": errors,
            }

        # 正常继续
        return {
            "style_findings": style_findings,
            "security_findings": security_findings,
            "errors": errors,
        }

    return early_stop

from src.agents.logic_checker import LogicChecker

# 节点 5：run_layer2 — 逻辑员串行执行
def create_run_layer2_node(logic_checker: LogicChecker):
    """
    逻辑员串行执行

    参数：
        logic_checker: LogicChecker 实例

    返回：
        async 节点函数
    """
    async def run_layer2(state: ReviewState) -> dict:
        enabled_agents = state.get("enabled_agents", [])
        errors = state.get("errors", [])

        if "logic" not in enabled_agents:
            logger.debug("[run_layer2] 逻辑员未启用，跳过")
            return {"logic_findings": []}

        review_input = state["input"]
        security_findings = state.get("security_findings", []) or []

        logger.info(
            f"[run_layer2] 启动逻辑员 | "
            f"安全发现: {len(security_findings)} 条"
        )

        try:
            logic_findings = await logic_checker.run({
                "code": review_input.code,
                "language": review_input.language.value,
                "security_findings": security_findings,
            })

            result_list = logic_findings if isinstance(logic_findings, list) else []
            logger.info(f"[run_layer2] 逻辑员完成，发现 {len(result_list)} 个问题")
            return {"logic_findings": result_list}

        except Exception as e:
            logger.error(f"[run_layer2] 逻辑员异常: {type(e).__name__}: {e}")
            return {
                "logic_findings": None,
                "errors": errors + [f"logic_checker 异常: {e}"],
            }

    return run_layer2

from src.agents.readability_checker import ReadabilityChecker

# 节点 6：run_layer3 — 可读员串行执行
def create_run_layer3_node(readability_checker: ReadabilityChecker):
    """
    可读员串行执行

    参数：
        readability_checker: ReadabilityChecker 实例

    返回：
        async 节点函数
    """
    async def run_layer3(state: ReviewState) -> dict:
        enabled_agents = state.get("enabled_agents", [])
        errors = state.get("errors", [])

        if "readability" not in enabled_agents:
            logger.debug("[run_layer3] 可读员未启用，跳过")
            return {"readability_findings": []}

        review_input = state["input"]

        # 合并所有前置 Agent 的发现，传给可读员避免重复检查
        all_prior_findings = (
            (state.get("style_findings", []) or [])
            + (state.get("security_findings", []) or [])
            + (state.get("logic_findings", []) or [])
        )

        logger.info(
            f"[run_layer3] 启动可读员 | "
            f"前置发现: {len(all_prior_findings)} 条"
        )

        try:
            readability_findings = await readability_checker.run({
                "code": review_input.code,
                "language": review_input.language.value,
                "all_findings": all_prior_findings,
            })

            result_list = (
                readability_findings if isinstance(readability_findings, list) else []
            )
            logger.info(
                f"[run_layer3] 可读员完成，发现 {len(result_list)} 个问题"
            )
            return {"readability_findings": result_list}

        except Exception as e:
            logger.error(f"[run_layer3] 可读员异常: {type(e).__name__}: {e}")
            return {
                "readability_findings": None,
                "errors": errors + [f"readability_checker 异常: {e}"],
            }

    return run_layer3

import time
from src.degradation.handler import degradation_handler

# 节点 7：degradation_handler — 重试 + 降级决策
def create_degradation_handler_node(style_checker, security_checker, logic_checker,
                                     readability_checker, llm):
    """
    降级总调度节点的工厂函数

    参数：
        style_checker:        StyleChecker 实例（用于重试）
        security_checker:     SecurityChecker 实例（用于重试）
        logic_checker:        LogicChecker 实例（用于重试）
        readability_checker:  ReadabilityChecker 实例（用于重试）
        llm:                  ChatOpenAI 实例（传给 degradation_handler 做 fallback）

    返回：
        async 节点函数

    节点逻辑：
        1. 从 state 收集四个 Agent 的执行结果
        2. 对失败的 Agent 进行一次重试
        3. 调用 degradation_handler() 决策：
           全部成功 → 直接 merge
           部分失败 → 跳过失败，merge 成功的
           大面积失败（成功 < 2）→ fallback 单 LLM 兜底
    """
    # Agent 名称 → 运行函数 + 所需输入的映射
    _AGENT_CONFIG = {
        "style": {
            "checker": lambda: style_checker,
            "input_keys": ["code", "language"],
        },
        "security": {
            "checker": lambda: security_checker,
            "input_keys": ["code", "language"],
        },
        "logic": {
            "checker": lambda: logic_checker,
            "input_keys": ["code", "language", "security_findings"],
        },
        "readability": {
            "checker": lambda: readability_checker,
            "input_keys": ["code", "language", "all_findings"],
        },
    }

    async def _retry_agent(agent_name: str, state: ReviewState) -> tuple[list | None, bool]:
        """
        重试一个失败的 Agent

        参数：
            agent_name: Agent 名称（"style"/"security"/"logic"/"readability"）

        返回：
            (findings | None, retried: bool)
            — findings 不为 None 表示重试成功
            — retried 为 True 表示确实执行了重试操作
        """
        config = _AGENT_CONFIG.get(agent_name)
        if config is None:
            return None, False

        checker = config["checker"]()
        review_input = state["input"]

        # 构造该 Agent 需要的输入
        input_kwargs = {}
        if "code" in config["input_keys"]:
            input_kwargs["code"] = review_input.code
        if "language" in config["input_keys"]:
            input_kwargs["language"] = review_input.language.value
        if "security_findings" in config["input_keys"]:
            input_kwargs["security_findings"] = state.get("security_findings", []) or []
        if "all_findings" in config["input_keys"]:
            input_kwargs["all_findings"] = (
                (state.get("style_findings", []) or [])
                + (state.get("security_findings", []) or [])
                + (state.get("logic_findings", []) or [])
            )

        logger.info(f"[degradation_handler] 重试 {agent_name} ...")

        try:
            if agent_name == "style":
                findings = await checker.check(
                    code=input_kwargs["code"],
                    language=input_kwargs.get("language", "python"),
                )
            else:
                findings = await checker.run(
                    input_kwargs
                )
            result_list = (
                findings if isinstance(findings, list) else []
            )
            logger.info(f"[degradation_handler] {agent_name} 重试成功，发现 {len(result_list)} 个问题")
            return result_list, True

        except Exception as e:
            logger.warning(f"[degradation_handler] {agent_name} 重试失败: {type(e).__name__}: {e}")
            return None, True

    async def degradation_handler_node(state: ReviewState) -> dict:
        """
        降级总调度节点

        返回：
            dict — {
                "final_findings": list[Finding],
                "failed_agents": list[str],
                "degraded": bool,
                "result": ReviewResult | None,      # fallback 模式时写入
                "errors": list[str],
            }
        """
        enabled_agents = state.get("enabled_agents", [])
        review_input = state["input"]
        errors = state.get("errors", [])

        # 收集所有 Agent 的执行结果
        findings_map: dict[str, list | None] = {}
        errors_map: dict[str, str | None] = {}

        agent_finding_keys = {
            "style": "style_findings",
            "security": "security_findings",
            "logic": "logic_findings",
            "readability": "readability_findings",
        }

        for agent_name in enabled_agents:
            key = agent_finding_keys.get(agent_name)
            if key is None:
                continue

            findings = state.get(key, [])
            if findings is None or isinstance(findings, Exception):
                # Agent 异常或未执行
                findings_map[agent_name] = None
                errors_map[agent_name] = str(findings) if findings else "未执行"
            elif isinstance(findings, list):
                # Agent 成功执行
                findings_map[agent_name] = findings
                errors_map[agent_name] = None
            else:
                # 意外类型
                findings_map[agent_name] = None
                errors_map[agent_name] = f"意外返回类型: {type(findings).__name__}"
        
        # 重试失败的 Agent
        retried_map: dict[str, bool] = {}

        for agent_name in enabled_agents:
            if findings_map.get(agent_name) is None:
                retried_findings, retried = await _retry_agent(agent_name, state)
                if retried_findings is not None:
                    findings_map[agent_name] = retried_findings
                    errors_map[agent_name] = None
                    # 更新 state 中对应的 findings 字段
                    key = agent_finding_keys[agent_name]
                    state[key] = retried_findings
                retried_map[agent_name] = retried
            else:
                retried_map[agent_name] = False

        # 调用降级策略
        decision = await degradation_handler(
            enabled_agents=enabled_agents,
            findings_by_agent=findings_map,
            errors_by_agent=errors_map,
            retried_by_agent=retried_map,
            review_input=review_input,
            llm=llm,
        )

        logger.info(
            f"[degradation_handler] 降级调度完成 | "
            f"失败: {decision['failed_agents']} | "
            f"降级: {decision['degraded']} | "
            f"最终 findings: {len(decision['final_findings'])} 条"
        )

        return {
            "final_findings": decision["final_findings"],
            "failed_agents": decision["failed_agents"],
            "degraded": decision["degraded"],
            "errors": errors,
        }

    return degradation_handler_node

from src.models.review_result import ReviewResult, ReviewStatus  

# 节点 8：aggregate — 结果汇总
def create_aggregate_node():
    """
    结果汇总节点的工厂函数

    不依赖外部实例（Aggregator 都是静态方法），所以不需要参数。

    返回：
        async 节点函数

    节点逻辑：
        1. 如果 state 中已有 result（early_stop 驳回 / fallback 写入）→ 跳过
        2. 否则从 state 收集所有 Agent 的 findings → 调用 Aggregator.merge()
        3. 根据 findings 最高 severity 确定 ReviewStatus
        4. 构造 ReviewResult
    """
    async def aggregate(state: ReviewState) -> dict:

        # 缓存命中：直接把缓存的 ReviewResult 作为 result
        cached = state.get("cached_result")
        if cached is not None:
            logger.info(f"[aggregate] 使用缓存结果 | findings: {cached.findings_count} 条")
            return {"result": cached}

        if state.get("result") is not None:
            logger.debug("[aggregate] result 已存在，跳过汇总")
            return {}

        enabled_agents = state.get("enabled_agents", [])
        agent_finding_keys = {
            "style": "style_findings",
            "security": "security_findings",
            "logic": "logic_findings",
            "readability": "readability_findings",
        }

        # 收集成功 Agent 的 findings
        findings_by_agent: dict[str, list] = {}
        for agent_name in enabled_agents:
            key = agent_finding_keys.get(agent_name)
            if key is None:
                continue
            f = state.get(key, [])
            if isinstance(f, list):
                findings_by_agent[agent_name] = f

        final_findings = state.get("final_findings", [])
        if not final_findings and findings_by_agent:
            final_findings = Aggregator.merge(findings_by_agent)

        # 确定 status：取 findings 中最高 severity
        status = _determine_status(final_findings)

        skipped_agents = state.get("failed_agents", [])
        degraded = state.get("degraded", False)

        result = ReviewResult(
            status=status,
            findings=final_findings,
            skipped_agents=skipped_agents,
            degraded=degraded,
            total_duration_ms=0,  # 由 API 层计算（工作流开始到结束）
        )

        logger.info(
            f"[aggregate] 汇总完成 | status: {status.value} | "
            f"findings: {len(final_findings)} 条 | "
            f"跳过: {skipped_agents} | 降级: {degraded}"
        )

        return {"result": result}

    return aggregate

def _determine_status(findings: list) -> ReviewStatus:
    """
    根据 finding 列表的最高 severity 确定评审状态

    规则：
        critical → REJECT（有致命问题，必须修复）
        high     → REJECT（有严重问题，应修复）
        medium   → PARTIAL_PASS（有问题但不致命）
        low/info → PASS（没有实质性问题）
        空列表   → PASS
    """
    if not findings:
        return ReviewStatus.PASS

    severities = [f.severity for f in findings]

    # 有 critical 或 high → 驳回
    if any(s in (Severity.CRITICAL, Severity.HIGH) for s in severities):
        return ReviewStatus.REJECT

    # 有 medium → 部分通过
    if any(s == Severity.MEDIUM for s in severities):
        return ReviewStatus.PARTIAL_PASS

    # 只有 low / info → 通过
    return ReviewStatus.PASS

# 节点 9：cache_write — 缓存写入
def create_cache_write_node(redis_cache: RedisCache):
    """
    缓存写入节点的工厂函数

    参数：
        redis_cache: RedisCache 实例

    返回：
        async 节点函数

    节点逻辑：
        1. 从 state 取 commit_hash，为空则跳过
        2. 从 state 取 result，为 None 则跳过
        3. 将 result 写入 Redis，TTL 默认 7 天
    """
    async def cache_write(state: ReviewState) -> dict:
        review_input = state["input"]
        commit_hash = review_input.commit_hash
        result = state.get("result")

        if not commit_hash:
            logger.debug("[cache_write] 无 commit_hash，跳过缓存写入")
            return {}
        if result is None:
            logger.debug("[cache_write] 无 result，跳过缓存写入")
            return {}

        # 写入 Redis
        try:
            await redis_cache.set(commit_hash, result)
            logger.info(f"[cache_write] 缓存写入成功 | commit: {commit_hash[:8]}...")
        except Exception as e:
            # 写入失败不阻塞，结果已经返回给用户了
            logger.warning(f"[cache_write] 缓存写入失败: {e}")

        return {}

    return cache_write






