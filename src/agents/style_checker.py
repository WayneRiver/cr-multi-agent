"""
src/agents/style_checker.py - 规范员

规范员负责检查代码格式和风格问题。
不经过 LLM，而是通过 MCP 协议直接调用 Ruff 工具，
轻量、快速
"""

from typing import Optional

from src.tools.mcp_client import MCPClient
from src.models.finding import Finding, Severity, FindingType
from src.utils.logger import logger


class StyleChecker:
    """
    规范员 —— 通过 MCP 调用 Ruff 检查代码格式

    使用示例：
        client = MCPClient()
        await client.connect()
        checker = StyleChecker(mcp_client=client)
        findings = await checker.check("def add(a,b):return a+b", "python")
        # → [Finding(severity=INFO, type=STYLE, title="缺少空格", ...)]
    """
    
    _RUFF_SEVERITY = Severity.INFO
    _RUFF_FINDING_TYPE = FindingType.STYLE
    
    SUPPORTED_LANGUAGES = {"python"}

    def __init__(self, mcp_client: MCPClient, timeout: int = 10):
        self._mcp_client = mcp_client
        self._timeout = timeout

    async def check(self, code: str, language: str) -> Optional[list[Finding]]:
        """
        执行一次代码格式检查

        参数：
            code:     待检查的 Python 代码字符串
            language: 编程语言，只支持 "python"
                      其他语言返回空列表（Ruff 只支持 Python）
        """
        if language not in self.SUPPORTED_LANGUAGES:
            logger.info(f"[StyleChecker] 不支持的语言: {language}，跳过检查")
            return []

        try:
            result = await self._mcp_client.call_tool(
                tool_name="ruff-check",
                arguments={"code": code},
                timeout=self._timeout,
            )

            raw_output = self._extract_text(result)
            if not raw_output:
                return []

            return self._parse(raw_output)

        except Exception as e:
            logger.error(f"[StyleChecker] Ruff 检查异常: {e}")
            return []

    def _extract_text(self, result: dict) -> Optional[str]:
        """
        从 MCP 调用结果中提取文本内容
        """
        content = result.get("content", [])
        texts = []
        for item in content:
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(texts)

    def _parse(self, raw: str) -> Optional[list[Finding]]:
        """
        解析 Ruff 输出为 Finding 列表
        """
        import json

        _SEVERITY_MAP = {
            "fatal": Severity.CRITICAL,    # 代码无法解析，如 SyntaxError
            "error": Severity.HIGH,        # 可能引起 bug，如未定义变量 F821
            "warning": Severity.MEDIUM,    # 风格警告，如行过长 E501
            "info": Severity.LOW,          # 代码风格建议
            "hint": Severity.INFO,         # 提示性信息，如可用简写
        }

        findings = []

        texts = raw.strip().split("\n")
        for text in texts:
            text = text.strip()
            if not text:
                continue

            try:
                data = json.loads(text)
                if isinstance(data, list):
                    continue

                issues = data.get("issues", [])
                if not issues:
                    continue

                for issue in issues:
                    # 提取字段
                    rule = issue.get("rule", "")
                    message = issue.get("message", "")
                    line_start = issue.get("line", 1)
                    line_end = issue.get("end_line", line_start)

                    raw_severity = issue.get("severity", "warning")
                    severity = _SEVERITY_MAP.get(raw_severity, Severity.INFO)

                    fixable = issue.get("fixable", False)
                    suggestion = "Ruff 可自动修复" if fixable else ""

                    title = f"{rule}: {message}" if rule else message

                    finding = Finding(
                        severity=severity,
                        type=self._RUFF_FINDING_TYPE,  # FindingType.STYLE
                        file="",
                        line_start=line_start,
                        line_end=line_end,
                        title=title,
                        description=message,
                        suggestion=suggestion,
                    )
                    findings.append(finding)

            except json.JSONDecodeError:
                continue
    
        logger.info(f"[StyleChecker] Ruff 检查完成，发现 {len(findings)} 个格式问题")
        return findings
        
        
       
