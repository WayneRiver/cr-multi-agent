"""
tests/test_agents/test_security_checker.py - 安全员单元测试

测试策略：
    Mock create_agent 返回的 agent.ainvoke，
    模拟 LLM 返回各种 JSON 输出，验证解析逻辑。
"""

import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import AIMessage

from src.agents.security_checker import SecurityChecker
from src.models.finding import Severity, FindingType


class TestSecurityChecker:
    """安全员的单元测试"""

    @patch("src.agents.base.create_agent")
    def test_detect_sql_injection(self, mock_create_agent):
        """
        LLM 发现 SQL 注入 → 正确解析为 Finding

        模拟 LLM 返回含 SQL 注入的 JSON。
        """
        # 模拟 LLM 的 JSON 输出
        llm_output = json.dumps([
            {
                "severity": "critical",
                "type": "security",
                "file": "",
                "line_start": 3,
                "line_end": 3,
                "title": "检测到 SQL 注入风险",
                "description": "直接拼接用户输入构造 SQL 查询",
                "suggestion": "使用参数化查询：cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))",
            }
        ])

        # Mock agent.ainvoke 返回值
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={
            "messages": [AIMessage(content=llm_output)]
        })
        mock_create_agent.return_value = mock_agent

        # 执行检查
        checker = SecurityChecker(llm=MagicMock())
        findings = asyncio.run(checker.run({
            "code": "def get_user(user_id):\n    query = f'SELECT * FROM users WHERE id = {user_id}'\n    cursor.execute(query)",
            "language": "python",
        }))

        # 验证
        assert len(findings) == 1
        assert findings[0].severity == Severity.CRITICAL
        assert findings[0].type == FindingType.SECURITY
        assert findings[0].line_start == 3
        assert "SQL" in findings[0].title

    @patch("src.agents.base.create_agent")
    def test_clean_code_no_findings(self, mock_create_agent):
        """
        安全代码 → LLM 返回空数组 → 返回空列表
        """
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={
            "messages": [AIMessage(content="[]")]
        })
        mock_create_agent.return_value = mock_agent

        checker = SecurityChecker(llm=MagicMock())
        findings = asyncio.run(checker.run({
            "code": "def add(a, b): return a + b",
            "language": "python",
        }))

        assert len(findings) == 0

