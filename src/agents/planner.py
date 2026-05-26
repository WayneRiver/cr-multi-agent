"""
src/agents/planner.py - Planner 决策器

Planner 负责决定启用哪些 Agent。
双层决策架构：
    第一层：_rule_based() — 纯规则判断，80% 场景，毫秒级
    第二层：_needs_llm() + _llm_enhance() — LLM 增强，20% 边界场景
"""

from langchain_openai import ChatOpenAI
from src.models.review_input import ReviewInput, Language
from src.utils.logger import logger
import json

class Planner:
    """
    Planner 决策器

    用法：
        planner = Planner()
        agents = planner.decide(review_input)
        # → ["style", "security", "logic", "readability"]
    """

    # 语言 → 默认 Agent 列表的映射表
    _LANGUAGE_AGENT_MAP = {
        Language.PYTHON:     ["style", "security", "logic", "readability"],
        Language.JAVA:       ["style", "security", "logic", "readability"],
        Language.GO:         ["style", "logic", "readability"],
        Language.JAVASCRIPT: ["style", "security", "readability"],
        Language.TYPESCRIPT: ["style", "security", "readability"],
    }

    # PR 描述中触发"精简模式"的关键词
    # 如果 PR 描述包含这些词，说明这次变更很简单，不需要所有 Agent
    _REDUCE_KEYWORDS = ["格式化", "注释修改", "修改注释", "typo", "format", "comment"]

    # 精简模式下只保留的 Agent
    _REDUCED_AGENTS = ["style", "readability"]

    # 触发 LLM 增强的关键词
    _LLM_TRIGGER_KEYWORDS = {
        "database", "sql", "数据库",
        "并发", "多线程", "thread", "concurrency",
    }

    def __init__(self, llm: ChatOpenAI | None = None):
        """
        初始化 Planner

        参数：
            llm: ChatOpenAI 实例，用于第二层 LLM 增强决策。
                 如果传 None，则只走规则判断（适用于纯规则场景和单元测试）。
        """
        self.llm = llm

    def decide(self, input: ReviewInput) -> list[str]:
        """
        执行完整的两层决策，返回最终启用的 Agent 列表

        参数：
            input: ReviewInput 实例

        返回：
            list[str] — 启用的 Agent 名称列表

        执行流程：
            1. 第一层：_rule_based() — 纯规则判断
            2. 第二层（可选）：_needs_llm() 检查 → _llm_enhance() 调整
        """
        # 第一层决策
        enabled = self._rule_based(input)
        logger.info(
            f"[Planner] 规则判断完成 | "
            f"语言: {input.language.value} | "
            f"初始Agent: {enabled}"
        )

        # 第二层决策
        if self._needs_llm(input):
            logger.info("[Planner] 触发 LLM 增强决策")
            enabled = self._llm_enhance(input, enabled)
        else:
            logger.info("[Planner] 规则判断已足够，跳过 LLM 增强")

        return enabled

    def _rule_based(self, input: ReviewInput) -> list[str]:
        """
        第一层决策: 纯规则判断，80% 场景，毫秒级
        """

        agents = self._LANGUAGE_AGENT_MAP.get(
            input.language,
            ["style", "readability"],
        )

        pr_desc = input.pr_description.lower() or ""

        if any(keyword in pr_desc for keyword in self._REDUCE_KEYWORDS):
            agents = self._REDUCED_AGENTS

        return agents

    def _needs_llm(self, input: ReviewInput) -> bool:
        """
        第二层决策: 判断是否需要 LLM 增强，20% 边界场景
        """
        pr_desc = input.pr_description.lower() or ""

        # 检查是否包含触发关键词
        for keyword in self._LLM_TRIGGER_KEYWORDS:
            if keyword in pr_desc:
                return True

        return False

    def _llm_enhance(self, input: ReviewInput, enabled: list[str]) -> list[str]:
        """
        第二层决策: 启用 LLM 增强的 Agent
        """

        if not self.llm:
            return enabled

        # 构造 prompt
        prompt = f"""你是一个代码评审调度专家。请根据以下信息，决定当前代码评审需要启用哪些检查Agent。

当前已启用的Agent：{json.dumps(enabled, ensure_ascii=False)}
编程语言：{input.language.value}
PR描述：{input.pr_description or "无"}

Agent能力说明：
- style：检查代码格式（缩进、空格、命名规范等），轻量快速
- security：检查安全漏洞（SQL注入、XSS、命令注入、硬编码密钥等），消耗LLM资源
- logic：检查逻辑缺陷（空指针、死锁、资源泄漏、边界条件等），消耗LLM资源
- readability：检查可读性（命名规范、函数长度、嵌套深度、魔法数字等），消耗LLM资源

决策原则：
1. 如果PR描述包含"数据库"、"SQL"等词，考虑添加 security
2. 如果PR描述包含"并发"、"多线程"等词，考虑添加 logic
3. 如果变更规模很大（如涉及20个以上文件），考虑减少非必要的Agent
4. 优先相信规则判断的结果，只在你确信需要调整时才调整
5. 不要轻易移除 style，因为它是轻量的格式检查

请以 JSON 格式输出你的决策，不要包含其他文字：
{{"add": ["要添加的Agent名称列表"], "remove": ["要移除的Agent名称列表"]}}
如果不需要调整，返回 {{"add": [], "remove": []}}"""

        try:
            response = self.llm.invoke(prompt)
            raw = response.content if hasattr(response, "content") else str(response)
                       
            data = json.loads(raw.strip())

            add_agents = data.get("add", [])
            for agent in add_agents:
                if agent not in enabled:
                    enabled.append(agent)

            remove_agents = data.get("remove", [])
            for agent in remove_agents:
                if agent in enabled:
                    enabled.remove(agent)

            logger.info(
                f"[Planner] LLM 增强完成 | "
                f"添加: {add_agents} | 移除: {remove_agents} | "
                f"最终: {enabled}"
            )

        except (json.JSONDecodeError, KeyError, AttributeError) as e:
            # LLM 返回格式不对或解析失败时，不阻塞流程
            # 直接返回规则判断的结果，以"可用"优先
            logger.warning(f"[Planner] LLM 增强解析失败: {e}，使用规则判断结果")

        return enabled


if __name__ == "__main__":
    from config import settings
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=0.1,
    )

    planner = Planner(llm=llm)

    # 随便换语言和描述，看输出
    result = planner.decide(
        ReviewInput(
            code="def foo(): pass",
            language="python",
            pr_description="修复了一个数据库 SQL 注入漏洞，涉及并发连接池",
        )
    )

    print(result)

            