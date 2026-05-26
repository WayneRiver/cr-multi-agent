"""
src/agents/security_checker.py - 安全员

继承 BaseAgent，通过 LLM 分析代码安全漏洞。
覆盖 OWASP Top 10 安全风险。
纯 LLM 分析，不需要额外工具。
"""

from langchain_openai import ChatOpenAI
from src.agents.base import BaseAgent

class SecurityChecker(BaseAgent):
    """
    安全员智能体，通过 LLM 分析代码安全漏洞。
    """
    def __init__(self, llm: ChatOpenAI, timeout: int = 10):
        super().__init__(
            llm=llm,
            tools=[],                # 纯 LLM 分析，不需要工具
            name="security_checker",
            timeout=timeout,
        )

    def _build_system_prompt(self) -> str:
        """
        构造安全审查的 system prompt

        覆盖 OWASP Top 10 安全风险类别，
        要求 LLM 以 JSON 数组格式输出。

        返回：
            str — system prompt 文本
        """
        return """你是一位资深的代码安全审查专家。请逐行审查以下代码中的安全漏洞。

需要覆盖以下 OWASP Top 10 安全风险类别：

1. SQL注入 —— 直接拼接用户输入构造 SQL 查询
2. 跨站脚本攻击（XSS）—— 未转义的用户输入直接输出到页面
3. 命令注入 —— 直接拼接用户输入执行系统命令
4. 路径遍历 —— 未校验用户输入的文件路径
5. 硬编码密钥/密码 —— 代码中直接写入密钥、密码、Token
6. 不安全的反序列化 —— 直接反序列化用户提供的数据
7. 敏感信息泄露 —— 日志或错误信息中暴露密码、密钥等
8. 不安全的直接对象引用 —— 未校验用户是否有权访问资源
9. 安全配置错误 —— 使用了不安全的默认配置
10. 使用已知漏洞的组件 —— 依赖了有已知漏洞的库

输出要求：
只输出 JSON 数组，不要包含其他文字、Markdown 格式或代码块标记。

每条 Finding 的格式：
{
    "severity": "critical 或 high 或 medium 或 low 或 info",
    "type": "security",
    "file": "",
    "line_start": 行号,
    "line_end": 行号,
    "title": "问题简要标题",
    "description": "问题的详细描述，说明为什么这是安全风险",
    "suggestion": "具体的修复建议，包括代码示例"
}

如果没有发现安全问题，输出空数组 []。"""
