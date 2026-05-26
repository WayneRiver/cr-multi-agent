"""
src/agents/readability_checker.py - 可读员

继承 BaseAgent，通过 LLM 分析代码可读性问题。
接收所有前置 Agent（规范员、安全员、逻辑员）的发现，
避免报告重复问题。
"""

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from src.agents.base import BaseAgent

class ReadabilityChecker(BaseAgent):
    """
    可读员，通过 LLM 分析代码可读性问题。
    接收所有前置 Agent（规范员、安全员、逻辑员）的发现，
    避免报告重复问题。
    """
    def __init__(self, llm: ChatOpenAI, timeout: int = 10):
        """
        初始化可读员

        参数：
            llm:      ChatOpenAI 实例
            timeout:  超时时间（秒），默认 10s
        """
        super().__init__(
            llm=llm,
            tools=[],
            name="readability_checker",
            timeout=timeout,
        )

    def _build_system_prompt(self) -> str:
        """
        构造可读性审查的 system prompt

        覆盖常见的代码可读性问题类型。
        """

        return """你是一位资深的代码可读性审查专家。请审查以下代码的可读性。

需要覆盖以下可读性问题类型：

1. 命名不规范 —— 变量/函数/类名不能体现其用途（如 a、b、tmp）
2. 函数过长 —— 函数超过 50 行，应拆分为多个小函数
3. 嵌套过深 —— 缩进超过 4 层，应提前 return 或抽取方法
4. 魔法数字 —— 代码中直接使用数字常量，应定义为命名常量
5. 注释缺失 —— 复杂逻辑缺少必要注释，或注释已过时
6. 重复代码 —— 相同或相似的代码片段出现多次
7. 参数过多 —— 函数参数超过 4 个，应考虑封装为对象
8. 职责不单一 —— 一个函数做了多件不同的事
9. 违反单一返回原则 —— 函数有多个返回点导致可读性差
10. 不合理的数据结构选择 —— 用 list 做频繁查找等

特别注意：
- 不要报告其他 Agent（规范员、安全员、逻辑员）已经发现的问题
- 已报问题列表会在代码下方提供，请先浏览再检查
- 只关注可读性问题，不关注安全、逻辑或格式问题

输出要求：
只输出 JSON 数组，不要包含其他文字、Markdown 格式或代码块标记。

每条 Finding 的格式：
{
    "severity": "critical 或 high 或 medium 或 low 或 info",
    "type": "readability",
    "file": "",
    "line_start": 行号,
    "line_end": 行号,
    "title": "问题简要标题",
    "description": "问题的详细描述",
    "suggestion": "具体的改进建议，包括代码示例"
}

如果没有发现可读性问题，输出空数组 []。"""

    def _build_user_message(self, input: dict) -> HumanMessage:
        """
        构造用户消息，注入已报问题列表避免重复

            参数：
                input: 包含 "code" 和可选的 "all_findings" 字段

        返回：
            HumanMessage 实例
        """
        code = input.get("code", "")
        all_findings = input.get("all_findings", [])
        
        # 拼接代码
        message = f"请审查以下代码的可读性：\n\n{code}\n"

        # 拼接已报问题列表
        if all_findings:
            message += "\n已报问题列表（请不要重复报告这些问题）：\n"
            for i, f in enumerate(all_findings, 1):
                message += (
                    f"{i}. [{f.severity.value}] {f.type.value} — "
                    f"第{f.line_start}行: {f.title}\n"
                )
            message += "\n请只报告不在以上列表中的新问题。\n"

        return HumanMessage(content=message)
