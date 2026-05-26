"""
src/agents/logic_checker.py - 逻辑员

继承 BaseAgent，通过 LLM 分析代码逻辑缺陷。
接收安全员的安全热点行号，重点关注这些行的逻辑正确性。
"""

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from src.agents.base import BaseAgent

class LogicChecker(BaseAgent):
    """
    逻辑员智能体，通过 LLM 分析代码逻辑缺陷。
    """

    def __init__(self, llm: ChatOpenAI, timeout: int = 15):
        """
        初始化逻辑员

        参数：
            llm:      ChatOpenAI 实例
            timeout:  超时时间（秒），默认 15s（逻辑推理比安全分析更复杂）
        """
        super().__init__(
            llm=llm,
            tools=[],
            name="logic_checker",
            timeout=timeout,
        )

    def _build_system_prompt(self) -> str:
        """
        构造逻辑审查的 system prompt

        覆盖常见的代码逻辑缺陷类型。
        """
        return """你是一位资深的代码逻辑审查专家。请逐行审查以下代码中的逻辑缺陷。

    需要覆盖以下逻辑问题类型：

    1. 空指针/空引用 —— 未判空就直接访问对象属性或方法
    2. 数组越界 —— 索引超出数组/列表长度范围
    3. 死锁/竞态条件 —— 多线程/异步环境下的资源竞争
    4. 资源泄漏 —— 文件、数据库连接、网络连接未正确关闭
    5. 类型错误 —— 类型转换不当或类型假设错误
    6. 边界条件 —— 循环边界、数组边界、数值溢出等
    7. 死循环 —— 循环终止条件错误或缺失
    8. 逻辑短路 —— 条件判断顺序错误导致某些分支不可达
    9. 错误的异常处理 —— 空 except、吞异常、不该捕获的异常
    10. 状态不一致 —— 修改了状态但未更新相关变量

    特别注意：
    - 检查安全热点标记的行（会在代码下方标注）
    - 一条 finding 只报告一个独立问题

    输出要求：
    只输出 JSON 数组，不要包含其他文字、Markdown 格式或代码块标记。

    每条 Finding 的格式：
    {
        "severity": "critical 或 high 或 medium 或 low 或 info",
        "type": "logic",
        "file": "",
        "line_start": 行号,
        "line_end": 行号,
        "title": "问题简要标题",
        "description": "问题的详细描述，说明为什么这是逻辑缺陷",
        "suggestion": "具体的修复建议，包括代码示例"
    }

    如果没有发现逻辑问题，输出空数组 []。"""

    def _build_user_message(self, input: dict) -> HumanMessage:
        """
        构造用户消息，注入安全热点行号

        参数：
            input: 包含 "code" 和可选的 "security_findings" 字段

        返回：
            HumanMessage 实例
        """
        code = input.get("code", "")
        security_findings = input.get("security_findings", [])

        # 从安全员发现中提取高风险（critical/high）行号
        hotspot_lines = set()
        for finding in security_findings:
            if finding.severity.value in ("critical", "high"):
                for line in range(finding.line_start, finding.line_end + 1):
                    hotspot_lines.add(line)

        message = f"请审查以下代码的逻辑正确性：\n\n{code}\n"

        if hotspot_lines:
            lines_str = ", ".join(str(l) for l in sorted(hotspot_lines))
            message += (
                f"\n安全热点行号：第 {lines_str} 行\n"
                f"安全员在这些行发现了高风险安全问题，请重点关注这些行的逻辑正确性。\n"
            )

        return HumanMessage(content=message)
