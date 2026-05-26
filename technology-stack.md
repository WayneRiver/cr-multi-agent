# CodeReview Multi-Agent System - 最终技术清单

## 一、核心框架
| 技术 | 用途 |
|------|------|
| LangChain | Agent基础框架、ReAct实现、工具定义 |
| LangGraph | 状态图编排、并行/串行节点、条件边 |
| FastAPI | REST API、GitHub Webhook接收 |

## 二、LLM相关
| 技术 | 用途 |
|------|------|
| OpenAI API / 兼容接口 | 调用LLM进行代码分析 |
| AsyncOpenAI | 异步调用，支持超时控制 |

## 三、异步与并发
| 技术 | 用途 |
|------|------|
| asyncio | 并行执行Agent、超时控制 |
| asyncio.gather | 第一层并行（规范员+安全员） |
| asyncio.wait_for | 单Agent超时控制 |

## 四、数据验证与Schema
| 技术 | 用途 |
|------|------|
| Pydantic | Finding Schema定义、输出校验 |
| typing | 类型注解 |

## 五、测试与数据对比
| 技术 | 用途 |
|------|------|
| pytest | 单元测试、Agent功能测试 |
| 手工测试脚本 | 跑10-30个PR收集对比数据 |

## 六、MCP集成
| 技术 | 用途 |
|------|------|
| mcp-server-analyzer | 规范员通过MCP调用Ruff进行代码格式检查 |
| MCP Python SDK | MCP Client配置，连接Agent到MCP Server |

## 七、
| 技术 | 用途 | 建议 |
|------|------|------|
| GitHub API | 自动评论PR | ✅ 建议做 |
| Streamlit | 可视化监控面板 | ✅ 建议做 |
| python-dotenv | 环境变量管理 | ✅ 必须 |
| loguru | 日志记录（降级事件） | ✅ 建议做 |
| Redis | 缓存评审结果、降级计数器  |
