"""
src/degradation/monitor.py - 降级监控器

负责追踪每个 Agent 的执行状态（成功/失败/超时/重试），
为 degradation_handler 提供决策依据。
不参与业务流程，只做状态收集和查询。
"""

from typing import Optional
from src.models.finding import Finding
from src.utils.logger import get_degradation_logger

class DegradationMonitor:
    """
    降级监控器 —— 追踪每个 Agent 的执行状态

    就像一个记分牌：
    - record() 写入每个 Agent 的"成绩"（成功/失败/错误原因）
    - get_failed() 查询谁挂了
    - success_count() 查询有几个及格的
    - get_events() 输出结构化的降级事件日志

    """

    def __init__(self, enabled_agents: list[str]):
        """
        初始化监控器，记录 Planner 决策出的 Agent 启用列表

        参数：
            enabled_agents: Planner.decide() 返回的 Agent 名称列表

        内部数据结构 _records：
            {
                "security_checker": {
                    "findings": [Finding, ...] | None,   # None 表示该 Agent 失败
                    "error": str | None,                  # 错误原因（成功时为 None）
                    "retried": bool,                      # 是否已重试过
                },
                ...
            }
        """
        self.enabled_agents = enabled_agents
        self._records: dict[str, dict] = {}

        self._degradation_logger = get_degradation_logger()

    def record(
        self,
        agent_name: str,
        findings: Optional[list[Finding]] = None,
        error: Optional[str] = None,
        retried: bool = False,
    ) -> None:
        """
        记录一个 Agent 的执行状态

        参数：
            agent_name: Agent 名称
            findings: 找到的 Finding 列表（成功时为 None）
            error: 错误原因（成功时为 None）
            retried: 是否已重试过（默认 False）
        """
        # 区分成功和失败
        is_success = error is None and findings is not None

        # 写入记录
        self._records[agent_name] = {
            "findings": findings,
            "error": error,
            "retried": retried,
        }

        # 记录降级日志（写入 degradation 专用日志文件）
        if is_success:
            finding_count = len(findings) if findings else 0
            retried_tag = "（重试后成功）" if retried else ""
            self._degradation_logger.info(
                f"[{agent_name}] 执行成功{retried_tag}，发现 {finding_count} 个问题"
            )
        else:
            retried_tag = "（重试后仍失败）" if retried else ""
            self._degradation_logger.warning(
                f"[{agent_name}] 执行失败{retried_tag}，原因: {error}"
            )

    def get_failed(self) -> list[str]:
        """
        返回执行失败的 Agent 名称列表

        判定失败的标准（满足任一即为失败）：
            1. findings 为 None（record() 传了 error，标记为失败）
            2. Agent 从未被 record() 调用过（在 enabled_agents 中但不在 _records 中）

        返回：
            list[str] — 失败 Agent 的名称列表，如 ["security_checker", "logic_checker"]
                        全部成功则返回空列表 []

        """
        failed = []

        for agent_name in self.enabled_agents:
            # 情况 1：被 record() 记录过，但标记为失败（findings 为 None）
            if agent_name in self._records:
                if self._records[agent_name]["findings"] is None:
                    failed.append(agent_name)
            # 情况 2：根本没有被 record() 调用过
            else:
                failed.append(agent_name)

                self._degradation_logger.warning(
                    f"[{agent_name}] 未被记录（可能未执行或调用前异常）"
                )

        return failed

    def success_count(self) -> int:
        """
        返回执行成功的 Agent 数量

        返回：
            int — 成功 Agent 数量，如 3
        """
        count = 0
        for record in self._records.values():
            if record["findings"] is not None:
                count += 1
        return count

    def get_events(self) -> list[dict]:
        """
        返回结构化的降级事件列表，供最终输出和 Streamlit 面板使用

        返回格式：
            [
                {
                    "agent": "security_checker",      # Agent 名称
                    "status": "success" | "failed",   # 最终状态
                    "error": "超时（10秒）" | None,     # 失败原因
                    "retried": true | false,           # 是否触发过重试
                },
                ...
            ]
        """
        events = []
        for agent_name, record in self._records.items():
            is_success = record["findings"] is not None
            events.append({
                "agent": agent_name,
                "status": "success" if is_success else "failed",
                "error": record["error"] if not is_success else None,
                "retried": record["retried"],
            })
        return events
