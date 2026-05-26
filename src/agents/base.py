"""
src/agents/base.py - Agent 基类

BaseAgent 是所有 LLM 代码评审 Agent 的抽象基类（模板方法模式）。
封装了：Agent 构建（create_agent）、超时控制（asyncio.wait_for）、
输出解析（JSON → Pydantic → 正则兜底）。

子类只需覆写 _build_system_prompt() 即可。
"""

import asyncio
import json
import re
from abc import ABC, abstractmethod
from typing import List, Optional

from langchain.agents import create_agent         
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage 

from src.models.finding import Finding
from src.utils.logger import logger

class BaseAgent(ABC):
    """
    基础 Agent 类，定义了所有 LLM 代码评审 Agent 的通用行为。
    """

    def __init__(
        self,
        llm: ChatOpenAI,
        tools: list,
        name: str,
        timeout: int = 10,
    ):
        self.llm = llm
        self.tools = tools
        self.name = name
        self.timeout = timeout

        system_prompt = self._build_system_prompt()

        self.agent = create_agent(
            model=llm,
            tools=tools,
            system_prompt=system_prompt,
        )

    async def run(self, input: dict) -> list[Finding]:
        """
        运行 Agent 并返回 Finding 列表。

        参数：
            input: 包含代码等信息的字典，至少需要 "code" 字段
                   格式：{"code": "def add(a,b): return a+b", "language": "python", ...}
                   子类可以覆写 _build_user_message() 来定制传给 LLM 的消息内容

        返回：
            list[Finding] — Agent 发现的问题列表
                           超时返回空列表
                           解析失败返回空列表
        """
        
        user_message = self._build_user_message(input)

        try:
            result = await asyncio.wait_for(
                self.agent.ainvoke(
                    {"messages": [user_message]}
                ),
                timeout=self.timeout,
            )

            raw_output = self._extract_final_output(result)
            logger.info(f"[{self.name}] 执行完成，原始输出长度：{len(raw_output)} 字符")

            # 解析输出为 Finding 列表
            return self._parse_output(raw_output)

        except asyncio.TimeoutError:
            logger.warning(f"[{self.name}] 执行超时（{self.timeout}秒），返回空列表")
            return []

        except Exception as e:
            logger.error(f"[{self.name}] 执行异常：{type(e).__name__}: {e}")
            return []

    def _build_user_message(self, input: dict) -> HumanMessage:
        """
        根据输入构造发送给 LLM 的用户消息

        参数：
            input: 包含 "code" 字段的字典

        返回：
            HumanMessage 实例
        """
        code = input.get("code")
        return HumanMessage(content=f"请检查以下代码: \n\n{code}")

    def _extract_final_output(self, result: dict) -> str:
        """
        从 create_agent 返回的 state 字典中提取 LLM 最终输出文本

        参数：
            result: agent.ainvoke() 返回的 state dict，结构如下：
                    {
                        "messages": [
                            SystemMessage(...),   # system prompt
                            HumanMessage(...),    # 用户消息
                            AIMessage(...),       # LLM 第一次回复（可能含 tool call）
                            ...                   # 多轮对话
                            AIMessage(...),       # 最后一条 AI 消息 ← 最终答案
                        ]
                    }

        返回：
            最后一条 AIMessage 的 content 字符串
        """
        messages = result.get("messages", [])

        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                content = msg.content

                if isinstance(content, list):
                    content = content[0].get("text", "") if content else ""
                elif isinstance(content, str):
                    pass
                else:
                    content = str(content)

            return content

        # 如果没有找到任何 AIMessage，返回空字符串
        logger.warning(f"[{self.name}] 未在输出中找到 AIMessage")
        return ""

    def _parse_output(self, raw: str) -> list[Finding]:
        """
        三级容错解析：把 LLM 输出的原始文本解析为 Finding 列表

        参数：
            raw: LLM 输出的原始文本，理想情况是 JSON 数组字符串：
                 '[{"severity":"high","type":"security",...}]'

        返回：
            list[Finding] — 成功解析的问题列表，全失败则返回空列表
        """

        # 第一级解析：直接 JSON 解析
        findings = self._parse_as_json(raw)
        if findings is not None:
            return findings

        # 第二级解析：正则提取 JSON 数组
        findings = self._parse_by_regex_json_array(raw)
        if findings is not None:
            return findings

        # 第三级解析: 正则逐条提取
        findings = self._parse_by_regex_individual(raw)
        if findings is not None:
            return findings

        # 三级全失败 → 返回空列表
        logger.warning(f"[{self.name}] 输出解析失败（三级容错均未匹配），原始输出前200字符：{raw[:200]}")
        return []

    def _parse_as_json(self, raw: str) -> Optional[list[Finding]]:
        """
        第 1 级：直接 JSON 解析

        尝试把整个 raw 字符串作为 JSON 数组解析。
        也兼容 LLM 将数据包裹在对象中的情况（如 {"findings": [...]}）。

        返回 None 表示需要降级到下一级。
        """
        try:
            data = json.loads(raw.strip())
            
            if isinstance(data, list):
                findings = []
                for item in data:
                    try:
                        findings.append(Finding.model_validate(item))
                    except Exception:
                        pass
                if findings:
                    return findings
                return None # 数组为空，降级

            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, list):
                        findings = []
                        for item in value:
                            try:
                                findings.append(Finding.model_validate(item))
                            except Exception:
                                pass
                        if findings:
                            return findings
                return None # 没找到有效数组，降级

            return None # 既不是数组也不是对象，降级
        
        except json.JSONDecodeError:
            return None

    def _parse_by_regex_json_array(self, raw: str) -> Optional[list[Finding]]:
        """
        第 2 级：用正则提取 JSON 数组

        应对 LLM 输出被 Markdown 或文字包裹的情况，如：
            "以下是发现的问题：\n```json\n[{...}]\n```"

        用正则匹配 [...] 结构，提取最长的 JSON 数组并解析。
        """
        matches = re.findall(r'\[.*?\]', raw, re.DOTALL)
        if not matches:
            return None # 没有找到 JSON 数组，降级

        longest_match = max(matches, key=len)

        try:
            data = json.loads(longest_match.strip())
            if isinstance(data, list):
                findings = []
                for item in data:
                    try:
                        findings.append(Finding.model_validate(item))
                    except Exception:
                        pass
                if findings:
                    return findings
                return None # 数组为空，降级
            return None # 既不是数组也不是对象，降级
        except json.JSONDecodeError:
            return None # 无法解析 JSON 数组，降级

    def _parse_by_regex_individual(self, raw: str) -> Optional[list[Finding]]:
        """
        第 3 级：正则逐条提取 Finding 字段

        当 LLM 输出完全不遵守 JSON 格式时（如每行一个问题的自由文本），
        尝试用正则匹配 Finding 的核心字段（severity、type、title 等）。

        匹配逻辑：
            - severity: critical|high|medium|low|info
            - type:     style|security|logic|readability
            - title:    跟在 "title" 后面引号的内容
            - 其他字段用默认值填充
        """
        findings = []

        pattern = r'severity["\s:]*(["\']?\w+["\']?).*?'
        pattern += r'type["\s:]*(["\']?\w+["\']?).*?'
        pattern += r'title["\s:]*["\']([^"\']+)["\']'

        matches = re.findall(pattern, raw, re.IGNORECASE | re.DOTALL)
        if not matches:
            return None # 没有找到有效匹配，降级

        for sev, typ, title in matches:
            try:
                sev_clean = sev.strip("'\" ").lower()
                typ_clean = typ.strip("'\" ").lower()

                from src.models.finding import Severity, FindingType
                valid_severities = [s.value for s in Severity]
                valid_types = [t.value for t in FindingType]

                if sev_clean not in valid_severities or typ_clean not in valid_types:
                    continue

                finding = Finding(
                    severity=sev_clean,
                    type=typ_clean,
                    file="",
                    line_start=1,
                    line_end=1,
                    title=title,
                    description="（从非结构化输出中提取）",
                    suggestion="",
                )
                findings.append(finding)
            except Exception:
                continue

        return findings if findings else None

    @abstractmethod
    def _build_system_prompt(self) -> str:
        """
        子类必须实现，返回该 Agent 的 system prompt 字符串

        这是模板方法模式的核心：基类定义了执行骨架（__init__ → run → _parse_output），
        子类只填充这一个变量点。每个 Agent 的"个性"全在 prompt 里。
        """
        ...





