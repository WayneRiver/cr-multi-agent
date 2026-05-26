"""
tests/test_agents/test_readability_checker.py - 可读员单元测试

测试策略：
    Mock LLM 输出，验证可读性问题解析。
    额外验证 all_findings 被正确注入到消息中。
"""

import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import AIMessage

from src.agents.readability_checker import ReadabilityChecker
from src.models.finding import Finding, Severity, FindingType


class TestReadabilityChecker:
    """可读员的单元测试"""

    @patch("src.agents.base.create_agent")
    def test_detect_magic_number(self, mock_create_agent):
        """
        LLM 发现魔法数字 → 正确解析为 Finding
        """
        llm_output = json.dumps([
            {
                "severity": "low",
                "type": "readability",
                "file": "",
                "line_start": 3,
                "line_end": 3,
                "title": "检测到魔法数字 86400",
                "description": "直接使用数字常量 86400，应定义为命名常量",
                "suggestion": "提取为常量：SECONDS_PER_DAY = 86400",
            }
        ])

        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={
            "messages": [AIMessage(content=llm_output)]
        })
        mock_create_agent.return_value = mock_agent

        checker = ReadabilityChecker(llm=MagicMock())
        findings = asyncio.run(checker.run({
            "code": "timeout = 86400",
            "language": "python",
        }))

        assert len(findings) == 1
        assert findings[0].severity == Severity.LOW
        assert findings[0].type == FindingType.READABILITY
        assert "魔法数字" in findings[0].title or "86400" in findings[0].title

    @patch("src.agents.base.create_agent")
    def test_includes_existing_findings(self, mock_create_agent):
        """
        all_findings 中的已报问题被注入到消息中避免重复
        """
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={
            "messages": [AIMessage(content="[]")]
        })
        mock_create_agent.return_value = mock_agent

        checker = ReadabilityChecker(llm=MagicMock())

        existing = Finding(
            severity=Severity.HIGH,
            type=FindingType.SECURITY,
            line_start=5,
            line_end=5,
            title="SQL注入",
        )
        input_data = {
            "code": "def foo(): pass",
            "language": "python",
            "all_findings": [existing],
        }

        asyncio.run(checker.run(input_data))

        # 验证消息中包含已报问题列表
        call_args = mock_agent.ainvoke.call_args[0][0]
        messages = call_args["messages"]
        content = messages[0].content
        assert "已报问题" in content
        assert "SQL注入" in content

