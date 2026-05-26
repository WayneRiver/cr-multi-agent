"""
tests/test_agents/test_logic_checker.py - 逻辑员单元测试

测试策略：
    和 SecurityChecker 一样 Mock LLM，
    额外验证 security_findings 被正确注入到消息中。
"""

import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from langchain_core.messages import AIMessage

from src.agents.logic_checker import LogicChecker
from src.models.finding import Finding, Severity, FindingType


class TestLogicChecker:
    """逻辑员的单元测试"""

    @patch("src.agents.base.create_agent")
    def test_detect_key_error_risk(self, mock_create_agent):
        """
        LLM 发现 KeyError 风险 → 正确解析为 Finding
        """
        llm_output = json.dumps([
            {
                "severity": "high",
                "type": "logic",
                "file": "",
                "line_start": 2,
                "line_end": 2,
                "title": "字典访问可能引发 KeyError",
                "description": "直接使用 d[k] 访问字典，k 可能不存在",
                "suggestion": "使用 d.get(k) 或 try/except 包裹",
            }
        ])

        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={
            "messages": [AIMessage(content=llm_output)]
        })
        mock_create_agent.return_value = mock_agent

        checker = LogicChecker(llm=MagicMock())
        findings = asyncio.run(checker.run({
            "code": "def get_val(d, k): return d[k]",
            "language": "python",
        }))

        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH
        assert findings[0].type == FindingType.LOGIC
        assert "KeyError" in findings[0].title

    @patch("src.agents.base.create_agent")
    def test_injects_security_hotspots(self, mock_create_agent):
        """
        security_findings 中的 high/critical 行号被注入到 LLM 消息中
        """
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={
            "messages": [AIMessage(content="[]")]
        })
        mock_create_agent.return_value = mock_agent

        checker = LogicChecker(llm=MagicMock())

        # 传入模拟的安全发现
        hot_finding = Finding(
            severity=Severity.CRITICAL,
            type=FindingType.SECURITY,
            line_start=5,
            line_end=5,
            title="SQL注入",
        )
        input_data = {
            "code": "def foo(): pass",
            "language": "python",
            "security_findings": [hot_finding],
        }

        asyncio.run(checker.run(input_data))

        # 验证 agent.ainvoke 收到的消息包含热点行号
        call_args = mock_agent.ainvoke.call_args[0][0]
        messages = call_args["messages"]
        content = messages[0].content
        assert "安全热点" in content
        assert "5" in content.split("安全热点")[1].split("\n")[0]
