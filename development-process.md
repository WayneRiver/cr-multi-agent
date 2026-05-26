# 代码评审多智能体系统 - 完整开发流程(开发过务必按照此文档来开发)

## 阶段 0：项目脚手架

配置已经完成
生产依赖和开发依赖已经安装

## 阶段 1：数据模型
目标：所有 Pydantic Schema 就绪，有单元测试覆盖。
技术：Pydantic v2（BaseModel、Field、field_validator、Enum）
实现方法：
文件：src/models/finding.py
内容：Finding — id(自动uuid)/severity(枚举)/type(枚举)/file/line_start/line_end/title/description/suggestion。校验：line_end ≥ line_start
文件：src/models/review_input.py
内容：ReviewInput — code(必填)/language(枚举:python/java/go/javascript/typescript)/pr_description(可选)/commit_hash(可选)。校验：code 非空
文件：src/models/review_result.py
内容：ReviewResult — status(pass/reject/partial_pass)/findings/skipped_agents/degraded/total_duration_ms
文件：src/api/schemas.py
内容：ReviewRequest(继承ReviewInput) + ReviewResponse(继承ReviewResult)，加 API 层字段
测试：tests/test_models.py — 覆盖成功创建、校验失败、边界值


## 阶段 2：Agent 基类
目标：所有继承它的 Agent 只需实现 _build_system_prompt()，其余由基类统一处理。
技术：LangChain create_agent + ChatOpenAI(AsyncOpenAI) + asyncio.wait_for
实现方法：
方法：__init__(llm, tools, name, timeout)
说明：用 create_agent 构建 AgentExecutor，传入 system prompt 模板
方法：_build_system_prompt()（抽象方法）
说明：子类必须实现，返回 system prompt 字符串
方法：async run(input: dict) -> list[Finding]
说明：用 asyncio.wait_for(executor.ainvoke(), timeout) 包裹，超时返回空列表
方法：_parse_output(raw: str) -> list[Finding]
说明：先尝试 JSON 解析 → Pydantic 校验；失败则正则提取兜底；都失败返回空列表
子类只需做：
class MyAgent(BaseAgent):
    def _build_system_prompt(self) -> str:
        return "你是XX专家，检查..."
构造时传入: llm=ChatOpenAI(temperature=0.1), tools=[], name="my_agent", timeout=10
继承关系：
• BaseAgent → SecurityChecker、LogicChecker、ReadabilityChecker
• 不继承的：StyleChecker、Planner、Aggregator
测试：
• Mock LLM 返回，测超时 → 空列表
• Mock LLM 返回非法 JSON → 空列表
• Mock LLM 返回合法 JSON → 正确解析为 Finding 列表


## 阶段 3：规范员（独立类）
目标：调用 Ruff MCP 服务，解析输出为 Finding 列表。轻量快速，不套 ReAct。
技术：MCP Python SDK（stdio_client + ClientSession）
实现方法：
方法：__init__(mcp_client, timeout=10)
说明：持有 MCPClient 实例
方法：async check(code, language) -> list[Finding]
说明：只支持 python；调用 MCP ruff_check tool；asyncio.wait_for 超时
方法：_parse(raw) -> list[Finding]
说明：解析 Ruff 输出，映射到 Finding。Ruff 每个 violation → 一条 Finding
MCPClient 封装 (src/tools/mcp_client.py)：
• async call_tool(tool_name, arguments) — 建立会话 → 调用 tool → 返回结果
• 使用 StdioServerParameters 启动 mcp-server-analyzer 进程
测试：
• 准备一段有格式问题的 Python 代码，Mock MCP 返回 Ruff 格式输出，验证正确解析出 Finding
• 准备一段格式正确的代码，验证返回空列表


## 阶段 4：安全员 + 逻辑员 + 可读员
目标：三个继承 BaseAgent 的真实 Agent，各自独立可运行。
技术：BaseAgent 继承 + prompt 工程

安全员
类名：SecurityChecker(BaseAgent)
tools：空列表（纯 LLM 分析）
超时：10s
system prompt：覆盖 OWASP Top 10：SQL注入、XSS、命令注入、路径遍历、硬编码密钥、不安全的反序列化、敏感信息泄露。要求输出 JSON 数组
测试：含 SQL 注入的代码 → 检出；安全代码 → 误报率低

逻辑员
类名：LogicChecker(BaseAgent)
tools：空列表
超时：15s（推理更复杂）
system prompt：覆盖：空指针、数组越界、死锁/竞态、资源泄漏、类型错误、边界条件、死循环
测试：含 KeyError 风险的代码 → 检出

可读员
类名：ReadabilityChecker(BaseAgent)
tools：空列表
超时：10s
system prompt：覆盖：命名规范、函数过长(>50行)、嵌套过深(>4层)、魔法数字、注释质量、重复代码
特殊：run() 时接收所有前置 Agent 的 findings，prompt 强调不要重复已报问题
测试：含魔法数字的代码 → 检出


## 阶段 5：Planner
目标：双层决策 —— 80% 场景不调用 LLM，20% 边界场景用 LLM 增强。
技术：规则引擎 + 正则匹配 + LangChain ChatOpenAI.invoke（单次调用，非 Agent）
实现方法：
方法：decide(input: ReviewInput) -> list[str]
说明：顺序执行第一层 → 判断是否需要第二层
方法：_rule_based(input)
说明：第一层：语言→默认映射 + PR描述关键词精简（"格式化" → 仅 style+readability）
方法：_needs_llm(input)
说明：判断标准：含"数据库"/"SQL"/"并发"/"多线程"或文件数 > 20
方法：_llm_enhance(input, enabled)
说明：第二层：单次 LLM 调用，传入当前 agent 列表和 PR 描述，返回调整后的列表
语言 → 默认 Agent 映射：
Python/Java：style, security, logic, readability
Go：style, logic, readability
JavaScript/TypeScript：style, security, readability
测试：Python → 4 个；Go → 3 个；含"格式化"的 PR 描述 → 2 个；Go + "数据库" → 强制包含 security


## 阶段 6：Redis 缓存层
目标：评审结果缓存，避免重复评审同一 commit。
技术：redis.asyncio.Redis + Pydantic model_dump_json / model_validate_json
实现方法：
方法：__init__(redis_url)
说明：用 redis.asyncio.Redis.from_url 创建连接
方法：async get(commit_hash) → ReviewResult|None
说明：key = cr:result:{hash}；get() → model_validate_json()
方法：async set(commit_hash, result, ttl_days=7)
说明：setex(key, ttl*86400, result.model_dump_json())
方法：async close()
说明：关闭连接池
测试：用 fakeredis 覆盖 — 写后读、不存在返回 None、TTL 过期


## 阶段 7：降级系统 + Aggregator
目标：LangGraph 编排所需的两个上游模块就绪。

7a — DegradationMonitor
技术：dataclass + 状态收集
方法：__init__(enabled_agents)
说明：记录启用列表
方法：record(agent_name, findings, error)
说明：存储每个 Agent 的执行结果（成功/失败/错误原因/是否重试）
方法：get_failed()
说明：返回失败 Agent 名称列表
方法：success_count()
说明：返回成功 Agent 数量
方法：get_events()
说明：返回结构化的降级事件列表（agent/reason/retried），供最终输出使用

7b — Fallback（单 Agent 兜底）
技术：单次 LLM 调用（非 Agent，不经过 ReAct）
async def single_agent_fallback(input: ReviewInput, llm: ChatOpenAI) -> list[Finding]:
    # 构建全科式 prompt，覆盖安全+逻辑+可读+规范所有维度
    # 单次 LLM invoke
    # JSON 解析 → model_validate → 返回

7c — Aggregator
技术：纯函数，不调 LLM
方法：merge(findings_by_agent: dict)
说明：①展平所有 finding ②(file/l_start/l_end/type) 去重 ③同位置冲突取 severity 更高 ④按 severity 排序
severity 排序：critical(0) > high(1) > medium(2) > low(3) > info(4)，同 severity 按 line_start

7d — degradation_handler 聚合逻辑
给 LangGraph 节点用的顶层函数：
1. 用 Monitor 收集所有 Agent 结果
2. 若 success_count < 2 → fallback
3. 若 0 < failed < total → 跳过失败 Agent，成功的 merged 返回
4. 全部成功 → 直接 merged 返回


## 阶段 8：LangGraph 编排（核心）
目标：8 个节点 + 2 条条件边构成完整工作流图。
技术：LangGraph StateGraph + add_node + add_conditional_edges + compile

状态定义 state.py
ReviewState(TypedDict)：包含 input / enabled_agents / cache_hit / cached_result / findings 各维度 / failed_agents / degraded / final_findings / result

8 个节点 nodes.py（每个节点是 async def (state: ReviewState) -> dict）：

节点：cache_check
输入：input
输出：cache_hit, cached_result
关键实现：有 commit_hash 才查 Redis

节点：plan
输入：input
输出：enabled_agents
关键实现：调用 Planner.decide()

节点：early_stop
输入：input, enabled_agents
输出：style_findings, 或 result(驳回)
关键实现：仅当 style 在启用列表时执行；>3 critical→驳回

节点：run_layer1
输入：input, enabled_agents
输出：style_findings, security_findings
关键实现：asyncio.gather(style.check(), security.run())

节点：run_layer2
输入：input, enabled_agents, security_findings
输出：logic_findings
关键实现：logic.run() 传 security_findings

节点：run_layer3
输入：input, enabled_agents, 所有前置 findings
输出：readability_findings
关键实现：readability.run() 传全量 findings

节点：degradation_handler
输入：所有 agent 产出
输出：final_findings, failed_agents, degraded
关键实现：调用 monitor + 判 fallback

节点：aggregate
输入：final_findings
输出：result
关键实现：Aggregator.merge() + 确定 status

节点：cache_write
输入：input, result
输出：无
关键实现：有 commit_hash 才写

2 条条件边 edges.py：
边：after_cache
判断：cache_hit 是否为真
分支：→ END 或 → plan

边：after_early_stop
判断：result 是否存在且 status=reject
分支：→ END 或 → run_layer1

图流程：
START → cache_check → plan → early_stop → run_layer1 → run_layer2 → run_layer3 → degradation_handler → aggregate → cache_write → END
（命中缓存或格式严重驳回时提前 END；degradation_handler 内部处理 fallback）

测试覆盖场景：
1. 正常流程：Python → 4 Agent 成功 → merged → 写缓存
2. 缓存命中：传入 commit_hash → 直接 END
3. 提前终止：格式 > 3 严重 → reject → END
4. 部分降级：安全员超时 → 跳过 → degraded=True
5. 大面积降级：3 个失败 → fallback → degraded=True
6. Go 语言分支：3 个 Agent，无 security
7. JS 语言分支：3 个 Agent，无 logic


## 阶段 9：API 集成 + 中间件
目标：服务可被外部调用。
技术：FastAPI lifespan + middleware + TestClient
实现：
• lifespan：初始化 LLM、Redis、MCP、Graph，注入 app.state
• POST /review：拿 app.state.graph 调 ainvoke，返回 ReviewResponse
• GET /health：返回服务状态
• 中间件：请求耗时记录(loguru)、未处理异常捕获
测试：httpx TestClient，Mock graph 返回，测 200/400 状态码


## 阶段 10：日志与监控
目标：降级事件可追踪。
技术：loguru filter + bind
实现：
• 降级事件专用 log：logger.bind(event="degradation")
• 在 degradation/monitor.py 每条记录都写入
• 可选 GET /metrics：缓存命中率、各 Agent 平均耗时、降级次数


## 阶段 11：GitHub 集成（可选）
目标：PR 自动触发评审 + 结果自动评论。
技术：httpx + hashlib（HMAC 签名验证）
实现：
• Webhook 端点 POST /webhook：验证 HMAC-SHA256 签名 → 解析 pull_request.opened/synchronize → 提取 diff + commit_hash + PR 描述 → 构造 ReviewInput → 调 POST /review
• post_pr_comment(pr_number, findings)：httpx 异步调 GitHub API，发布 Markdown 评论


## 阶段 12：Streamlit 面板（可选）
目标：评审可视化监控。
技术：Streamlit st.status + st.dataframe + st.metric
实现：
• Agent 执行状态展示
• Finding 表格（按 severity 颜色标记）
• 降级事件时间线
• 统计指标（耗时、Finding 数、降级次数）


## 阶段 13：手工对比测试
目标：10-30 个真实 PR，量化对比数据。
技术：pandas + CSV
实现：
• scripts/benchmark.py：对每个 PR 跑完整流程 + 兜底模式
• 对比指标：Finding 总数、误报率（人工标）、耗时、降级次数
• 输出 CSV 报告


## 总结：技术到实现映射

LangChain create_react_agent：agents/base.py — 封装 ReAct 循环，子类覆盖 system prompt
LangGraph StateGraph：graph/workflow.py — 9 节点 + 2 条件边，编译后 main.py 调用 ainvoke
FastAPI：main.py + api/routes.py — uvicorn 启动，lifespan 初始化
Pydantic v2：models/*.py — Schema 定义 + 校验 + JSON 序列化
AsyncOpenAI (ChatOpenAI)：agents/base.py — temperature=0.1，asyncio.wait_for 超时
asyncio.gather：graph/nodes.py run_layer1 — 规范员 + 安全员并行
asyncio.wait_for：agents/base.py, agents/style_checker.py — 单 Agent 超时
MCP Python SDK：tools/mcp_client.py — StdioServerParameters + ClientSession
redis.asyncio：cache/redis_client.py — 异步连接池，model_dump_json 序列化
loguru：utils/logger.py — 控制台 + 文件轮转 + 降级专用 filter
fakeredis：测试 — Mock Redis 缓存
pytest + pytest-asyncio：全测试 — 异步测试