"""
src/integrations/github.py — GitHub API 客户端

功能：
1. 将代码评审结果（Finding 列表）格式化为 Markdown 表格
2. 调用 GitHub API (Issues Comments) 将评论发布到指定 PR 页面

使用方式：
    from src.integrations.github import GitHubClient

    client = GitHubClient(token=settings.github_token)
    await client.post_pr_comment(
        owner="my-org",
        repo="my-project",
        pr_number=42,
        findings=result.findings,
    )
"""

import httpx
from src.models.finding import Finding, Severity
from src.utils.logger import logger

# GitHub API 基础 URL
GITHUB_API_BASE = "https://api.github.com"


def _severity_emoji(severity: Severity) -> str:
    """
    severity 到 emoji 图标映射

    参数：
        severity: 问题的严重程度枚举值

    返回：
        对应的 emoji 字符

    映射关系：
        CRITICAL → 🔴   致命（如 SQL 注入、硬编码密钥）
        HIGH     → 🟠   高危（如 XSS、路径遍历）
        MEDIUM   → 🟡   中危（如资源泄漏、类型错误）
        LOW      → 🟢   低危（如命名不规范）
        INFO     → 🔵   信息（如注释建议）
    """
    emoji_map = {
        Severity.CRITICAL: "🔴",
        Severity.HIGH: "🟠",
        Severity.MEDIUM: "🟡",
        Severity.LOW: "🟢",
        Severity.INFO: "🔵",
    }
    return emoji_map.get(severity, "⚪")


def _build_comment_body(findings: list[Finding]) -> str:
    """
    将 Finding 列表转换为 Markdown 格式的评论正文

    参数：
        findings: Finding 实例列表（已按 severity 排序）

    返回：
        Markdown 字符串，包含：
        - 总览统计（各级别问题数量）
        - 按 severity 分组的问题详情表格
        - 每条 finding：文件位置、标题、描述、修复建议

    输出示例：

        ## 🤖 代码评审报告

        ### 总览
        | 级别 | 数量 |
        |------|------|
        | 🔴 致命 | 1 |
        | 🟠 高危 | 0 |
        ...

        ### 🔴 致命问题
        | 位置 | 标题 | 说明 | 建议 |
        |------|------|------|------|
        | src/auth.py:L42-L45 | SQL 注入风险 | ... | ... |
    """
    if not findings:
        return (
            "## 🤖 代码评审报告\n\n"
            "✅ **未发现问题**，代码评审通过。\n"
        )

    # ---------- 统计各级别数量 ----------
    # 使用一个固定顺序遍历 severity 等级，确保表格输出稳定
    severity_order = [
        (Severity.CRITICAL, "🔴 致命"),
        (Severity.HIGH, "🟠 高危"),
        (Severity.MEDIUM, "🟡 中危"),
        (Severity.LOW, "🟢 低危"),
        (Severity.INFO, "🔵 信息"),
    ]

    # {Severity.CRITICAL: [f1, f2], Severity.HIGH: [], ...}
    grouped: dict[Severity, list[Finding]] = {
        sev: [] for sev, _ in severity_order
    }
    for f in findings:
        if f.severity in grouped:
            grouped[f.severity].append(f)

    # ---------- 构建 Markdown ----------
    lines: list[str] = []
    lines.append("## 🤖 代码评审报告")
    lines.append("")

    # 总览表格（第一段）
    lines.append("### 总览")
    lines.append("")
    lines.append("| 级别 | 数量 |")
    lines.append("|------|------|")
    for sev, label in severity_order:
        count = len(grouped[sev])
        if count > 0:
            lines.append(f"| {label} | **{count}** |")
    lines.append("")

    # 按 severity 分组展示详情（第二段）
    for sev, label in severity_order:
        group = grouped[sev]
        if not group:
            continue

        lines.append(f"### {label}问题")
        lines.append("")
        lines.append("| 位置 | 标题 | 说明 | 建议 |")
        lines.append("|------|------|------|------|")

        for f_item in group:
            # 文件位置格式化：path:Lstart-Lend 或仅 path:Line
            if f_item.line_end and f_item.line_end != f_item.line_start:
                location = (
                    f"`{f_item.file}` L{f_item.line_start}-L{f_item.line_end}"
                )
            else:
                location = f"`{f_item.file}` L{f_item.line_start}"

            # 转义表格中的管道符，防止 Markdown 表格被截断
            title = f_item.title.replace("|", "\\|")
            description = (f_item.description or "").replace("|", "\\|")
            suggestion = (f_item.suggestion or "-").replace("|", "\\|")

            lines.append(
                f"| {location} | {title} | {description} | {suggestion} |"
            )

        lines.append("")

    # 页脚
    lines.append("---")
    lines.append("*本报告由多智能体代码评审系统自动生成*")

    return "\n".join(lines)


class GitHubClient:
    """
    GitHub API 异步客户端

    只做一件事：给指定 PR 发布评审评论。

    参数：
        token: GitHub Personal Access Token
               需要 repo 权限（私有仓库）或 public_repo 权限（公开仓库）
    """

    def __init__(self, token: str):
        """
        初始化 GitHub 客户端

        参数：
            token: GitHub Personal Access Token
        """
        self._token = token
        # 创建一个可复用的 httpx 异步客户端
        # base_url 固定为 GitHub API 地址
        self._client = httpx.AsyncClient(
            base_url=GITHUB_API_BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                # GitHub API 要求显式指定 API 版本
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def post_pr_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        findings: list[Finding],
    ) -> bool:
        """
        向指定 PR 发布代码评审结果评论

        参数：
            owner:     GitHub 仓库所有者（组织名或用户名）
            repo:      GitHub 仓库名称
            pr_number: PR 编号（整数，如 42）
            findings:  Finding 列表，由 Aggregator.merge() 产出

        返回：
            True  — 评论发布成功
            False — 发布失败（token 无效、仓库不存在、网络错误等）

        实现原理：
            调 GitHub REST API：
            POST /repos/{owner}/{repo}/issues/{pr_number}/comments

            GitHub 中 PR 也是 Issue，
            所以 comments API 路径用的是 /issues/而非 /pulls/
        """
        # 步骤 1：构建评论正文
        comment_body = _build_comment_body(findings)

        url = f"/repos/{owner}/{repo}/issues/{pr_number}/comments"
        payload = {"body": comment_body}

        logger.info(
            f"[GitHub] 发布 PR 评论 | owner={owner} repo={repo} "
            f"PR=#{pr_number} | {len(findings)} 条 finding"
        )

        # 步骤 2：发 POST 请求
        try:
            response = await self._client.post(url, json=payload)

            # GitHub 返回 201 Created 表示评论创建成功
            if response.status_code == 201:
                comment_data = response.json()
                comment_id = comment_data.get("id")
                logger.info(
                    f"[GitHub] 评论发布成功 | "
                    f"PR=#{pr_number} comment_id={comment_id}"
                )
                return True

            # 权限不足（token 没给 repo 权限）
            elif response.status_code == 401:
                logger.error(
                    f"[GitHub] 认证失败 | PR=#{pr_number} "
                    f"请检查 GITHUB_TOKEN 是否有效"
                )
                return False

            # 仓库不存在 或 没有权限访问
            elif response.status_code == 404:
                logger.error(
                    f"[GitHub] 仓库或 PR 不存在 | "
                    f"owner={owner} repo={repo} PR=#{pr_number}"
                )
                return False

            # 其他错误
            else:
                logger.error(
                    f"[GitHub] 评论发布失败 | "
                    f"HTTP {response.status_code} | {response.text[:200]}"
                )
                return False

        except httpx.TimeoutException:
            # 网络超时（httpx 默认 30s）
            logger.error(
                f"[GitHub] 请求超时 | "
                f"owner={owner} repo={repo} PR=#{pr_number}"
            )
            return False

        except Exception as e:
            # 网络不可达、DNS 解析失败等
            logger.error(
                f"[GitHub] 请求异常 | PR=#{pr_number} "
                f"| {type(e).__name__}: {e}"
            )
            return False

    async def close(self):
        """
        关闭 httpx 客户端，释放连接资源

        应该在 FastAPI lifespan 关闭阶段调用
        """
        await self._client.aclose()

