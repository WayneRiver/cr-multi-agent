"""
src/degradation/fallback.py - 单 Agent 兜底

当大面积 Agent 失败（成功数 < 2）时，
不再尝试逐个 Agent 精细化检查，
而是用一次 LLM 调用覆盖全部检查维度。
不经过 ReAct 循环，不经过 Agent 框架，就是一次 prompt → 一次回复。
"""

import json
from typing import Optional

from langchain_openai import ChatOpenAI
from src.models.finding import Finding, Severity, FindingType
from src.models.review_input import ReviewInput
from src.utils.logger import logger, get_degradation_logger

async def single_agent_fallback(
    input: ReviewInput,
    llm: ChatOpenAI,
) -> list[Finding]:
    """
    单 Agent 兜底 —— 一次 LLM 调用覆盖全部检查维度

    触发条件（由 degradation_handler 判断）：
        成功执行的 Agent 少于 2 个

    参数：
        input: 评审请求（包含 code、language、pr_description）
        llm:   ChatOpenAI 实例

    返回：
        list[Finding] — 发现的问题列表。调用失败或解析失败时返回空列表。

    """
    degradation_logger = get_degradation_logger()
    degradation_logger.warning(
        f"触发单 Agent 兜底模式 | 语言: {input.language.value} | "
        f"代码长度: {len(input.code)} 字符"
    )

    # 1. 构建"全科医生" prompt
    # 同时覆盖四个维度：规范、安全、逻辑、可读
    prompt = f"""你是一位资深的代码审查专家，需要全方面审查以下代码。

编程语言：{input.language.value}
PR描述：{input.pr_description or "无"}

请从以下四个维度全面审查代码：

## 1. 代码规范
- 命名是否规范（变量、函数、类名）
- 缩进和空格是否符合语言惯例
- 是否遵循行业最佳实践

## 2. 安全漏洞（OWASP Top 10）
- SQL 注入、XSS、命令注入
- 路径遍历、硬编码密钥/密码
- 不安全的反序列化、敏感信息泄露

## 3. 逻辑缺陷
- 空指针/空引用、数组越界
- 死锁/竞态条件、资源泄漏
- 类型错误、边界条件、死循环
- 错误的异常处理

## 4. 可读性问题
- 函数过长（超过50行）、嵌套过深（超过4层）
- 魔法数字、注释缺失
- 重复代码、参数过多

特别注意：
- 每条 finding 只报告一个独立问题
- severity 要准确：critical（必须立即修复的安全漏洞）> high（严重问题）> medium（中等问题）> low（轻微问题）> info（提示）
- type 取值为：style、security、logic、readability

待审查代码：
{input.code}

请以 JSON 数组格式输出，不要包含其他文字：
[
    {{
        "severity": "critical/high/medium/low/info",
        "type": "style/security/logic/readability",
        "file": "",
        "line_start": 行号,
        "line_end": 行号,
        "title": "问题简要标题",
        "description": "问题的详细描述",
        "suggestion": "具体的修复建议"
    }}
]

如果没有发现任何问题，输出空数组 []。"""

    # 2. 单次 LLM 调用
    try:
        response = await llm.ainvoke(prompt)
        raw = response.content if hasattr(response, "content") else str(response)

        logger.info(f"[Fallback] LLM 调用完成，输出长度: {len(raw)} 字符")

    except Exception as e:
        logger.error(f"[Fallback] LLM 调用失败: {type(e).__name__}: {e}")
        degradation_logger.error(f"兜底模式 LLM 调用失败: {e}")
        return []

    # 3. 解析输出为 Finding 列表
    findings = _parse_fallback_output(raw)

    degradation_logger.info(
        f"兜底模式完成 | 发现 {len(findings)} 个问题"
    )
    return findings

def _parse_fallback_output(raw: str) -> list[Finding]:
    """
    解析 Fallback LLM 的 JSON 输出

    参数：
        raw: LLM 输出的原始文本

    返回：
        list[Finding] — 解析成功的问题列表，全失败则返回空列表

    容错策略（和 BaseAgent._parse_output 类似）：
        1. 直接 JSON 解析
        2. 正则提取 JSON 数组
        3. 都失败 → 返回空列表
    """

    # 第 1 级：直接 JSON 解析
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

    except json.JSONDecodeError:
        pass  # 降级到第 2 级

    # 第 2 级：正则提取 JSON 数组（处理 LLM 包裹 Markdown 的情况）
    import re
    matches = re.findall(r'\[.*?\]', raw, re.DOTALL)
    if matches:
        longest = max(matches, key=len)
        try:
            data = json.loads(longest)
            if isinstance(data, list):
                findings = []
                for item in data:
                    try:
                        findings.append(Finding.model_validate(item))
                    except Exception:
                        pass
                if findings:
                    return findings
        except json.JSONDecodeError:
            pass

    # 两级全失败 → 返回空列表
    logger.warning(
        f"[Fallback] 输出解析失败 | 原始输出前200字符: {raw[:200]}"
    )
    return []