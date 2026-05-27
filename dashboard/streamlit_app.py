"""
dashboard/streamlit_app.py — 代码评审可视化面板

多智能体代码评审系统的 Streamlit 前端。
通过调用后端 FastAPI /api/v1/review 接口，
将评审结果以可视化方式呈现。

功能模块：
1. 侧边栏 — API 地址配置
2. 输入区 — 代码粘贴 + 语言选择
3. 执行区 — 提交评审 + 实时状态
4. 指标卡 — 4 个 st.metric 展示关键数据
5. Finding 表格 — 按 severity 颜色标记的交互式表格
6. 降级信息 — 降级事件 + 跳过 Agent 列表
7. 指标页 — /metrics 端点数据展示

使用方式：
    # 先启动后端
    uvicorn main:app --host 0.0.0.0 --port 8000

    # 再启动 Streamlit（另一个终端）
    streamlit run dashboard/streamlit_app.py
"""

import streamlit as st
import httpx
import pandas as pd
import time

# ============================================================
# 配置
# ============================================================

# 设置页面标题、图标、布局
st.set_page_config(
    page_title="代码评审面板",
    page_icon="🤖",
    layout="wide",
)

# ============================================================
# 函数 1：初始化 Session State
# ============================================================

def init_session_state():
    """
    初始化 Streamlit 的会话状态变量

    会话状态（st.session_state）是 Streamlit 在多次页面重跑之间
    保留数据的机制。类似一个 dict，但 key 的值在用户关闭浏览器前
    一直存在。

    这里初始化三个 key：
        review_done:   是否已经完成过一次评审（控制结果显示）
        review_result: 最近一次评审的完整 API 响应 dict
        review_input:  最近一次输入的代码（方便查看）
    """
    defaults = {
        "review_done": False,
        "review_result": None,
        "review_input": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ============================================================
# 函数 2：调后端 API
# ============================================================

async def call_review_api(
    api_url: str,
    code: str,
    language: str,
    commit_hash: str,
) -> dict | None:
    """
    调用 FastAPI 后端的 POST /api/v1/review 接口

    参数：
        api_url:     后端基础地址，如 http://localhost:8000
        code:        待评审的代码文本
        language:    编程语言（python/java/go/javascript/typescript）
        commit_hash: 可选的 commit 哈希（用于缓存）

    返回：
        成功 → API 响应的完整 dict
        失败 → None（同时用 st.error 显示错误信息）

    实现细节：
        使用 httpx.AsyncClient 异步调用，设置 120 秒超时（多 Agent 评审较慢）。
    """
    full_url = f"{api_url}/api/v1/review"

    request_body = {
        "code": code,
        "language": language,
        "commit_hash": commit_hash or "",
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(full_url, json=request_body)

            if response.status_code == 201:
                return response.json()
            else:
                st.error(f"API 返回错误 {response.status_code}: {response.text[:300]}")
                return None

    except httpx.ConnectError:
        st.error(f"无法连接后端服务: {api_url}，请确认 uvicorn 已启动")
        return None

    except httpx.TimeoutException:
        st.error(f"请求超时（>120s），评审 Agent 可能卡住，请检查后端日志")
        return None

    except Exception as e:
        st.error(f"请求异常: {type(e).__name__}: {e}")
        return None

# ============================================================
# 函数 3：侧边栏
# ============================================================

def render_sidebar() -> str:
    """
    渲染侧边栏，返回用户配置的 API 地址

    侧边栏内容：
        - 后端 API 地址输入框
        - 配置说明
        - 快速操作按钮（清空缓存等）

    返回：
        API 基础地址字符串
    """
    with st.sidebar:
        st.title("⚙️ 配置")

        api_url = st.text_input(
            "后端 API 地址",
            value="http://localhost:8000",
            help="FastAPI 服务的地址（默认 http://localhost:8000）",
        )

        st.divider()

        st.markdown("### 📋 使用说明")
        st.markdown("""
        1. 确保后端已启动：`uvicorn main:app --port 8000`
        2. 在左侧输入框粘贴待评审代码
        3. 选择编程语言
        4. 点击「开始评审」
        5. 查看可视化结果
        """)

        st.divider()

        # 清空历史结果的按钮
        if st.button("🗑️ 清空当前结果"):
            st.session_state.review_done = False
            st.session_state.review_result = None
            st.rerun()

        st.caption("多智能体代码评审系统 v0.1.0")

    return api_url


# ============================================================
# 函数 4：主输入区
# ============================================================

def render_input_area() -> tuple[str, str, str]:
    """
    渲染代码输入区域

    使用三列布局：
        左列（60%）：代码文本输入框
        右列（40%）：语言选择 + commit hash + 提交按钮

    返回：
        (code, language, commit_hash) 三元组
    """
    st.title("🤖 多智能体代码评审系统")

    left, right = st.columns([6, 4])

    with left:
        code = st.text_area(
            "待评审代码",
            height=350,
            placeholder="在此粘贴代码...\n\n示例：\ndef add(a, b):\n    return a + b",
            help="粘贴完整的文件内容或 git diff 输出",
        )

    with right:
        language = st.selectbox(
            "编程语言",
            options=["python", "java", "go", "javascript", "typescript"],
            index=0,
            help="选择代码的编程语言，Planner 会据此决定启用哪些 Agent",
        )

        commit_hash = st.text_input(
            "Commit Hash（可选）",
            placeholder="如: abc123def456",
            help="填写后系统会缓存评审结果（7天），相同 commit 再次评审直接返回缓存",
        )

        st.divider()

        # 提交按钮 — 只有输入了代码才能点击
        submit_disabled = len(code.strip()) == 0
        submitted = st.button(
            "🚀 开始评审",
            type="primary",
            use_container_width=True,
            disabled=submit_disabled,
        )

    return code, language, commit_hash, submitted


# ============================================================
# 函数 5：渲染结果概览指标卡
# ============================================================

def render_metrics_cards(result: dict):
    """
    用 4 个 st.metric 卡片展示评审关键指标

    参数：
        result: API 返回的完整响应 dict，包含：
            - status:           "pass" / "reject" / "partial_pass"
            - findings:         Finding 列表
            - total_duration_ms: 总耗时（毫秒）
            - degraded:         是否触发降级
            - skipped_agents:   被跳过的 Agent 列表
            - findings_count:   Finding 总数
    """
    st.markdown("### 📊 评审概览")

    col1, col2, col3, col4 = st.columns(4)

    # 指标 1：评审状态
    status = result.get("status", "unknown")
    status_display = {
        "pass": "✅ 通过",
        "reject": "❌ 驳回",
        "partial_pass": "⚠️ 部分通过",
    }.get(status, status)

    with col1:
        st.metric(
            label="评审状态",
            value=status_display,
        )

    # 指标 2：Finding 数量
    findings_count = result.get("findings_count", 0)
    findings = result.get("findings", [])

    # 统计各级别数量
    critical_count = sum(
        1 for f in findings if f.get("severity") == "critical"
    )
    high_count = sum(
        1 for f in findings if f.get("severity") == "high"
    )

    with col2:
        st.metric(
            label="发现问题数",
            value=findings_count,
            delta=f"🔴 {critical_count + high_count} 严重" if (critical_count + high_count) > 0 else "无严重问题",
        )

    # 指标 3：总耗时
    duration_ms = result.get("total_duration_ms", 0)
    duration_s = duration_ms / 1000

    with col3:
        st.metric(
            label="总耗时",
            value=f"{duration_s:.1f}s",
        )

    # 指标 4：降级状态
    degraded = result.get("degraded", False)
    skipped = result.get("skipped_agents", [])

    with col4:
        if degraded:
            st.metric(
                label="降级状态",
                value="⚠️ 已触发",
                delta=f"跳过: {', '.join(skipped) if skipped else '无'}",
            )
        else:
            st.metric(
                label="降级状态",
                value="✅ 正常",
                delta="所有 Agent 成功",
            )


# ============================================================
# 函数 6：渲染 Finding 表格
# ============================================================

def _severity_color(severity: str) -> str:
    """
    根据 severity 返回对应的 CSS 颜色值

    用于 st.dataframe 的列着色。
    """
    colors = {
        "critical": "background-color: #ff4b4b; color: white; font-weight: bold",
        "high": "background-color: #ff6b6b; color: white",
        "medium": "background-color: #ffd93d; color: black",
        "low": "background-color: #6bcb77; color: white",
        "info": "background-color: #4d96ff; color: white",
    }
    return colors.get(severity, "")


def render_findings_table(result: dict):
    """
    渲染 Finding 列表为交互式表格

    使用 st.dataframe 配合 pandas DataFrame 和 column_config，
    实现：
        - severity 列按级别着色（红/黄/绿/蓝）
        - 文件位置列左对齐
        - 表格默认按 severity 排序

    参数：
        result: API 响应 dict
    """
    st.markdown("### 🔍 发现的问题")

    findings = result.get("findings", [])

    if not findings:
        st.success("🎉 未发现任何问题，代码质量良好！")
        return

    # 将 Finding 列表转为 DataFrame
    rows = []
    for f in findings:
        # 格式化文件位置
        line_start = f.get("line_start", 0)
        line_end = f.get("line_end")
        if line_end and line_end != line_start:
            location = f"{f.get('file', '')} L{line_start}-L{line_end}"
        else:
            location = f"{f.get('file', '')} L{line_start}"

        rows.append({
            "级别": f.get("severity", "unknown").upper(),
            "类型": f.get("type", ""),
            "位置": location,
            "标题": f.get("title", ""),
            "说明": f.get("description", "")[:150],  # 截断过长的描述
            "建议": f.get("suggestion", "")[:150],
        })

    df = pd.DataFrame(rows)

    # 按严重程度排序
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    df["_sort"] = df["级别"].map(severity_order)
    df = df.sort_values("_sort").drop(columns=["_sort"])

    # 用 column_config 自定义列样式
    st.dataframe(
        df,
        column_config={
            "级别": st.column_config.TextColumn(
                "级别",
                width="small",
            ),
            "类型": st.column_config.TextColumn(
                "类型",
                width="small",
            ),
            "位置": st.column_config.TextColumn(
                "位置",
                width="medium",
            ),
            "标题": st.column_config.TextColumn(
                "标题",
                width="large",
            ),
            "说明": st.column_config.TextColumn("说明"),
            "建议": st.column_config.TextColumn("建议"),
        },
        hide_index=True,
        use_container_width=True,
    )

    # 简易的 severity 统计条
    st.caption(
        f"🔴 Critical: {sum(1 for f in findings if f.get('severity') == 'critical')} | "
        f"🟠 High: {sum(1 for f in findings if f.get('severity') == 'high')} | "
        f"🟡 Medium: {sum(1 for f in findings if f.get('severity') == 'medium')} | "
        f"🟢 Low: {sum(1 for f in findings if f.get('severity') == 'low')} | "
        f"🔵 Info: {sum(1 for f in findings if f.get('severity') == 'info')}"
    )


# ============================================================
# 函数 7：渲染降级信息
# ============================================================

def render_degradation_info(result: dict):
    """
    展示降级和 Agent 执行状态信息

    使用 st.expander 折叠区域，默认展开。
    只展示降级相关的信息：是否触发降级、哪些 Agent 被跳过、错误列表。

    参数：
        result: API 响应 dict
    """
    degraded = result.get("degraded", False)
    skipped = result.get("skipped_agents", [])
    errors = result.get("errors", [])

    if not degraded and not skipped and not errors:
        # 一切正常，不展示
        return

    st.markdown("### ⚠️ 降级信息")

    with st.expander("查看详情", expanded=True):
        if degraded:
            st.warning("本次评审触发了降级策略：部分 Agent 执行失败，系统使用了备用方案。")

        if skipped:
            st.info(f"跳过的 Agent: {', '.join(skipped)}")

        if errors:
            st.markdown("**错误详情：**")
            for err in errors:
                st.text(f"• {err}")


# ============================================================
# 函数 8：原始 JSON 查看
# ============================================================

def render_raw_json(result: dict):
    """
    在折叠区域展示 API 返回的完整 JSON

    方便开发者查看原始数据、调试问题。
    """
    with st.expander("📄 原始 JSON 响应", expanded=False):
        st.json(result)


# ============================================================
# 主函数
# ============================================================

def main():
    """
    Streamlit 应用主入口

    整体布局：
        侧边栏  →  配置
        主区域  →  输入区 + 结果区

    执行流程：
        1. init_session_state() → 初始化会话状态
        2. render_sidebar() → 侧边栏
        3. render_input_area() → 代码输入 + 提交按钮
        4. 用户提交后 → call_review_api() 调后端
        5. 渲染结果 → metrics 卡片 + findings 表格 + 降级信息
    """
    # 初始化
    init_session_state()

    # 侧边栏
    api_url = render_sidebar()

    # 输入区
    code, language, commit_hash, submitted = render_input_area()

    st.divider()

    # ---------- 处理提交 ----------
    if submitted:
        # 记录请求开始时间（用于前端计时）
        request_start = time.time()

        with st.status("🤖 多智能体评审中...", expanded=True) as status:
            st.write("📤 提交代码到后端服务...")

            # 异步调用后端 API
            result = asyncio.run(
                call_review_api(api_url, code, language, commit_hash)
            )

            if result is not None:
                # 计算前端感知的总耗时（包括网络传输）
                frontend_duration = time.time() - request_start
                st.write(f"✅ 评审完成！前端总耗时: {frontend_duration:.1f}s")

                # 保存到 session_state，避免页面重跑后结果丢失
                st.session_state.review_result = result
                st.session_state.review_done = True
                st.session_state.review_input = code

                status.update(label="✅ 评审完成", state="complete")
            else:
                status.update(label="❌ 评审失败", state="error")

    # ---------- 渲染结果 ----------
    if st.session_state.review_done and st.session_state.review_result is not None:
        result = st.session_state.review_result

        st.divider()
        st.header("📋 评审结果")

        # 指标卡片
        render_metrics_cards(result)

        st.divider()

        # Finding 表格
        render_findings_table(result)

        st.divider()

        # 降级信息
        render_degradation_info(result)

        # 原始 JSON
        render_raw_json(result)

    else:
        # 没有结果时展示欢迎信息
        st.info("👆 在左侧粘贴代码，选择语言后点击「开始评审」按钮")


if __name__ == "__main__":
    import asyncio
    main()

