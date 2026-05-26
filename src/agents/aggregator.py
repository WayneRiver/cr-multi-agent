"""
src/agents/aggregator.py - 结果汇总器

纯函数模块，不调用 LLM。
负责把多个 Agent 的 Finding 列表合并、去重、冲突解决、排序。
"""

from src.models.finding import Finding, Severity

class Aggregator:
    """
    结果汇总器 —— 合并多个 Agent 的 Finding 列表
    """
    # severity → 排序权重
    _SEVERITY_ORDER = {
        Severity.CRITICAL: 0,
        Severity.HIGH: 1,
        Severity.MEDIUM: 2,
        Severity.LOW: 3,
        Severity.INFO: 4,
    }

    @staticmethod
    def merge(findings_by_agent: dict[str, list[Finding]]) -> list[Finding]:
        """
        合并、去重、排序所有 Agent 的 Finding

        参数：
            findings_by_agent: key 是 Agent 名称（如 "security_checker"），
                              value 是该 Agent 返回的 Finding 列表

        返回：
            list[Finding] — 合并去重排序后的 Finding 列表
        """

        # 1. 合并所有 Agent 的 Finding 列表
        all_findings: list[Finding] = []
        for agent_name, findings in findings_by_agent.items():
            all_findings.extend(findings)

        if not all_findings:
            return []

        # 2. 去重: 相同位置 + 相同类型
        # 按 severity 排序，让高严重度的先处理，去重时保留先遇到的（更严重的）
        all_findings.sort(
            key=lambda f: Aggregator._SEVERITY_ORDER.get(f.severity, 99)
        )

        seen: set[tuple] = set()
        deduped: list[Finding] = []

        for finding in all_findings:
            key = (
                finding.file,
                finding.line_start,
                finding.line_end,
                finding.type.value,
            )
            if key not in seen:
                seen.add(key)
                deduped.append(finding)

        # 3. 冲突解决：同一行不同判断 → 取 severity 更高的
        location_groups: dict[tuple, list[Finding]] = {}
        for finding in deduped:
            loc_key = (
                finding.file,
                finding.line_start,
                finding.line_end,
            )
            if loc_key not in location_groups:
                location_groups[loc_key] = []
            location_groups[loc_key].append(finding)

        resolved: list[Finding] = []
        for loc_key, group in location_groups.items():
            if len(group) == 1:
                resolved.append(group[0])
            else:
                best = max(
                    group,
                    key=lambda f: Aggregator._SEVERITY_ORDER.get(f.severity, 99),
                )
                resolved.append(best)

        # 4. 排序：先 severity 再行号
        resolved.sort(
            key=lambda f: (
                Aggregator._SEVERITY_ORDER.get(f.severity, 99),  # 主排序：severity
                f.line_start,                                      # 次排序：行号
            )
        )

        return resolved


