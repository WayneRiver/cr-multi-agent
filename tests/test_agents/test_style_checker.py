"""
tests/test_agents/test_style_checker.py - 规范员单元测试

测试策略（重要）：
    规范员不调 LLM，而是通过 MCP 调 Ruff。
    我们的测试不启动真实的 mcp-server-analyzer 进程，
    而是 Mock MCPClient.call_tool() 的返回值。

    这样做的好处：
        1. 测试不依赖外部环境（服务器进程、网络）
        2. 测试跑得快（毫秒级）
        3. 可以模拟各种 Ruff 输出（正常、异常、空结果）
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from src.agents.style_checker import StyleChecker
from src.models.finding import Severity, FindingType
from src.tools.mcp_client import MCPClient


class TestStyleChecker:
    """规范员的单元测试"""

    def test_check_with_ruff_violations(self):
        """
        Ruff 发现格式问题 → 正确解析为 Finding 列表

        模拟场景：
            mcp-server-analyzer 返回 Ruff 的 JSON lines 输出，
            包含两条违规：
            - F401: `os` imported but unused（第 1 行）
            - E501: Line too long（第 42 行）

        预期：
            - 返回 2 条 Finding
            - 类型都是 STYLE
            - 严重程度都是 INFO
            - 行号对应 Ruff 的输出来源错
        """
        # 模拟 Ruff 输出（每行一个 JSON 对象，Ruff 的 JSON lines 格式）
        ruff_output = (
            '{"issues":['
            '{"line":1,"column":8,"end_line":1,"end_column":10,'
            '"rule":"F401","message":"`os` imported but unused",'
            '"severity":"error","fixable":true},'
            '{"line":42,"column":0,"end_line":42,"end_column":110,'
            '"rule":"E501","message":"Line too long (110 > 88)",'
            '"severity":"warning","fixable":false}'
            '],"total_issues":2,"fixable_issues":1}'
        )

        # 模拟 MCPClient
        mock_client = MagicMock(spec=MCPClient)

        # AsyncMock 模拟异步方法 call_tool
        mock_client.call_tool = AsyncMock(return_value={
            "content": [
                {"type": "text", "text": ruff_output}
            ]
        })

        # 执行检查
        checker = StyleChecker(mcp_client=mock_client)
        import asyncio
        findings = asyncio.run(checker.check("import os\n\ndef foo():\n    pass\n", "python"))

        # 验证：返回 2 条 Finding
        assert len(findings) == 2

        # 验证第一条：F401（severity="error" → MEDIUM）
        assert findings[0].severity == Severity.MEDIUM   
        assert findings[0].type == FindingType.STYLE
        assert findings[0].line_start == 1
        assert findings[0].line_end == 1
        assert findings[0].description == "`os` imported but unused"
        assert "F401" in findings[0].title
        assert "unused" in findings[0].title
        assert findings[0].suggestion == "Ruff 可自动修复"  # fixable=true

        # 验证第二条：E501（severity="warning" → INFO）
        assert findings[1].severity == Severity.INFO   
        assert findings[1].type == FindingType.STYLE
        assert findings[1].line_start == 42
        assert findings[1].line_end == 42
        assert "E501" in findings[1].title
        assert findings[1].suggestion == ""              # fixable=false

