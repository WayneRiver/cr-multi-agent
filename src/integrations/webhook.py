"""
src/integrations/webhook.py — GitHub Webhook 处理

功能：
1. HMAC-SHA256 签名验证 —— 防止恶意请求冒充 GitHub
2. 解析 webhook 事件 —— 只处理 pull_request.opened / synchronize
3. 提取 PR 关键信息 —— owner、repo、pr_number、diff、commit_hash、描述
4. 执行评审 + 发布评论 —— 调 LangGraph 工作流 → GitHub API 评论

使用方式：
    在 api/routes.py 的 POST /webhook 端点中组装调用
"""

import hashlib
import hmac

import httpx
from src.models.review_input import ReviewInput, Language
from src.models.finding import Finding
from src.utils.logger import logger

# GitHub 触发 webhook 时会在请求头里带上这个字段
GITHUB_SIGNATURE_HEADER = "X-Hub-Signature-256"
# 事件类型也在请求头里
GITHUB_EVENT_HEADER = "X-GitHub-Event"


def verify_signature(payload_body: bytes, signature_header: str, secret: str) -> bool:
    """
    验证 GitHub Webhook 的 HMAC-SHA256 签名

    这是安全的第一道防线：只有持有相同 secret 的 GitHub 服务器
    才能生成匹配的签名。

    参数：
        payload_body:    HTTP 请求的原始 body（bytes，不能是 dict）
                        必须是原始字节，因为 HMAC 在字节上计算
        signature_header: X-Hub-Signature-256 请求头的值
                         格式为 "sha256=<hex_digest>"
        secret:          在 GitHub Webhook 设置中配置的密钥字符串

    返回：
        True  — 签名有效，可以信任此请求
        False — 签名无效或格式错误，应丢弃请求（返回 403）

    HMAC-SHA256 原理：
        1. 发送方（GitHub）用 secret 对 body 做 HMAC-SHA256 摘要
        2. 接收方（我们）用同一个 secret 对同一 body 再算一次
        3. 两者匹配 → 消息未被篡改且来源可信

    hmac.compare_digest 的作用：
        用常量时间比较两个十六进制字符串，防止时序攻击。
        普通 == 比较在第一个不同字符处就返回，攻击者可以通过
        测量响应时间来逐字节猜解签名。compare_digest 消除这个差异。
    """
    # 步骤 1：检查签名头是否存在
    if not signature_header:
        logger.warning("[Webhook] 缺少 X-Hub-Signature-256 头")
        return False

    # 步骤 2：解析签名头，提取算法和摘要值
    # 格式："sha256=abcdef1234567890..."
    try:
        algo, signature = signature_header.split("=", 1)
        if algo != "sha256":
            logger.warning(f"[Webhook] 不支持的签名算法: {algo}")
            return False
    except ValueError:
        logger.warning("[Webhook] 签名头格式错误（缺少 = 分隔符）")
        return False

    # 步骤 3：用 secret 对 body 计算 HMAC-SHA256
    # hmac.new(key, msg, digestmod) 返回一个 hmac 对象
    computed = hmac.new(
        key=secret.encode("utf-8"),   # HMAC key 必须是 bytes
        msg=payload_body,              # 消息就是原始请求体
        digestmod=hashlib.sha256,      # 摘要算法
    ).hexdigest()                      # 转为十六进制字符串

    # 步骤 4：常量时间比较（防时序攻击）
    if not hmac.compare_digest(computed, signature):
        logger.warning(
            f"[Webhook] 签名不匹配 | "
            f"expected={signature[:8]}... "
            f"computed={computed[:8]}..."
        )
        return False

    logger.debug("[Webhook] 签名验证通过")
    return True


def parse_event(event_type: str, payload: dict) -> dict | None:
    """
    解析 webhook 事件，只处理我们关心的事件类型

    参数：
        event_type: X-GitHub-Event 请求头的值
                    如 "pull_request"、"push"、"issues"
        payload:    webhook 请求体（已解析为 dict）

    返回：
        None  — 事件类型不关心（如 push 到非 PR 分支），跳过处理
        dict  — 提取出的 PR 关键信息，包含：
                {
                    "action": str,         # "opened" | "synchronize"
                    "owner": str,          # 仓库所有者
                    "repo": str,           # 仓库名称
                    "pr_number": int,      # PR 编号
                    "pr_title": str,       # PR 标题
                    "pr_body": str | None, # PR 描述（即 pr_description）
                    "commit_hash": str,    # PR head 的最新 commit SHA
                    "diff_url": str,       # 用于获取代码 diff 的 URL
                    "base_ref": str,       # 目标分支（如 "main"）
                    "head_ref": str,       # 来源分支
                }

    仅处理的事件：
        pull_request.opened        — PR 首次创建
        pull_request.synchronize   — PR 有新 commit 推送
    """
    # 只处理 pull_request 事件
    if event_type != "pull_request":
        logger.debug(f"[Webhook] 忽略事件类型: {event_type}")
        return None

    action = payload.get("action", "")

    # 只关心 opened 和 synchronize
    # opened:       PR 刚创建时触发
    # synchronize:  往 PR 分支 push 新 commit 时触发
    if action not in ("opened", "synchronize"):
        logger.debug(f"[Webhook] 忽略 PR action: {action}")
        return None

    # ---------- 提取字段 ----------
    pr_data = payload.get("pull_request", {})
    repo_data = payload.get("repository", {})

    if not pr_data or not repo_data:
        logger.error("[Webhook] payload 缺少 pull_request 或 repository 字段")
        return None

    # 仓库信息
    owner = repo_data.get("owner", {}).get("login", "")
    repo_name = repo_data.get("name", "")

    if not owner or not repo_name:
        logger.error("[Webhook] 无法提取 owner 或 repo 名称")
        return None

    # PR 编号
    pr_number = pr_data.get("number")
    if not pr_number:
        logger.error("[Webhook] 无法提取 PR 编号")
        return None

    # PR 标题和描述
    pr_title = pr_data.get("title", "")
    pr_body = pr_data.get("body")  # 可以为 None

    # commit hash: PR 的 head 分支最新 commit
    head_data = pr_data.get("head", {})
    commit_hash = head_data.get("sha", "")

    # diff URL: 用 GitHub token 访问这个 URL 可以获取原始 diff 文本
    diff_url = pr_data.get("diff_url", "")

    # 分支信息（打日志用）
    base_ref = pr_data.get("base", {}).get("ref", "")
    head_ref = head_data.get("ref", "")

    logger.info(
        f"[Webhook] 解析 PR 事件 | "
        f"action={action} | "
        f"{owner}/{repo_name}#{pr_number} | "
        f"{base_ref} ← {head_ref} | "
        f"commit={commit_hash[:8]}..."
    )

    return {
        "action": action,
        "owner": owner,
        "repo": repo_name,
        "pr_number": pr_number,
        "pr_title": pr_title,
        "pr_body": pr_body,
        "commit_hash": commit_hash,
        "diff_url": diff_url,
        "base_ref": base_ref,
        "head_ref": head_ref,
    }


async def fetch_pr_diff(diff_url: str, token: str) -> str:
    """
    从 GitHub API 获取 PR 的原始 diff 文本

    参数：
        diff_url: PR 的 diff URL（来自 webhook payload 的 pull_request.diff_url）
        token:    GitHub Personal Access Token

    返回：
        PR 的 diff 文本。获取失败时返回空字符串。
        空字符串意味着后续不会进行代码评审，直接跳过。

    为什么需要单独获取 diff：
        webhook payload 本身不包含代码 diff 内容，只有元数据。
        diff 内容需要通过 diff_url 单独请求。

    注意：
        Accept 头设为 application/vnd.github.v3.diff（GitHub 专有媒体类型），
        返回的是 unified diff 格式的纯文本，不是 JSON。
    """
    if not diff_url:
        logger.warning("[Webhook] diff_url 为空，无法获取代码变更")
        return ""

    logger.info(f"[Webhook] 获取 PR diff: {diff_url}")

    async with httpx.AsyncClient(
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3.diff",
        }
    ) as client:
        try:
            response = await client.get(diff_url)

            if response.status_code == 200:
                diff_text = response.text
                logger.debug(
                    f"[Webhook] diff 获取成功 | 大小: {len(diff_text)} 字符"
                )
                return diff_text

            else:
                logger.error(
                    f"[Webhook] diff 获取失败 | "
                    f"HTTP {response.status_code} | {response.text[:200]}"
                )
                return ""

        except httpx.TimeoutException:
            logger.error("[Webhook] diff 请求超时")
            return ""

        except Exception as e:
            logger.error(f"[Webhook] diff 请求异常: {type(e).__name__}: {e}")
            return ""


def detect_language(file_paths: list[str]) -> str:
    """
    根据文件扩展名推测编程语言

    参数：
        file_paths: PR 中变更的文件路径列表
                    如 ["src/main.py", "tests/test_main.py"]

    返回：
        推测的语言标识符 ("python" / "java" / "go" / "javascript" / "typescript")，
        无法判断时返回 "python" 作为默认值。

    规则：
        统计所有文件的扩展名，按多数决定语言。
        如果 PR 中大部分文件是 .go 结尾，就认为语言是 Go。
    """
    # 扩展名到语言标识符的映射
    extension_map = {
        ".py": "python",
        ".java": "java",
        ".go": "go",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
    }

    # 统计每种语言的匹配次数
    counts: dict[str, int] = {}
    for path in file_paths:
        for ext, lang in extension_map.items():
            if path.endswith(ext):
                counts[lang] = counts.get(lang, 0) + 1
                break  # 一个文件只匹配一次

    if not counts:
        return "python"  # 没有匹配到任何已知扩展名，默认 Python

    # 取出现次数最多的语言
    detected = max(counts, key=counts.get)  # type: ignore[arg-type]
    logger.debug(f"[Webhook] 语言检测: {detected} | 文件统计: {dict(counts)}")
    return detected

