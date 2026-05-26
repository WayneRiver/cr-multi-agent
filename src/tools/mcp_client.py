"""
src/tools/mcp_client.py - MCP Client 封装

MCP（Model Context Protocol）客户端，
负责与 mcp-server-analyzer 进程通信。

使用示例：
    client = MCPClient()
    result = await client.call_tool("ruff-check", {"code": "..."})
    await client.close()
"""


import asyncio
from typing import Any, Optional

from contextlib import AsyncExitStack
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.session import ClientSession

from src.utils.logger import logger

class MCPClient:
    """
    MCP 客户端，封装与 mcp-server-analyzer 的通信。
    """
    def __init__(
        self,
        server_command: str = "mcp-server-analyzer",
        server_args: Optional[list[str]] = None,
        timeout: int = 10,
    ):
        """
        初始化 MCP 客户端。

        :param server_command: MCP 服务器命令，默认 "mcp-server-analyzer"
        :param server_args: MCP 服务器参数，默认 None
        :param timeout: MCP 服务器超时时间，默认 10 秒
        """
        self.server_command = server_command
        self.server_args = server_args or []
        self.timeout = timeout

        self._server_params = StdioServerParameters(
            command=self.server_command,
            args=self.server_args,
        )

        self._session: Optional[ClientSession] = None
        self._read = None      # 读取流的上下文管理器
        self._write = None     # 写入流的上下文管理器
        self._stdio = None     # stdio 上下文管理器
        self._connected = False

    async def connect(self) -> None:
        """
        建立与 MCP Server 的连接

        执行流程：
            1. 用 StdioServerParameters 启动 mcp-server-analyzer 子进程
            2. 通过 stdio 建立双向通信管道（读写流）
            3. 创建 ClientSession 并初始化握手
            4. 标记连接已建立
        """
        # 创建 stdio 连接并获取读写流
        # AsyncExitStack 可以"进入"多个上下文管理器，并保持它们打开
        # 直到手动调用 aclose()
        self._stack = AsyncExitStack()

        # 进入 stdio 上下文，获取读写流
        transport = await self._stack.enter_async_context(
            stdio_client(self._server_params)
        )
        self._read, self._write = transport
        self._stdio = transport

        # 进入会话上下文，保持会话打开
        self._session = await self._stack.enter_async_context(
            ClientSession(self._read, self._write)
        )

        # 初始握手, initialize() 交换客户端和服务器的能力信息（协议版本、支持的工具等）
        await self._session.initialize()
        
        self._connected = True
        logger.info(f"MCP 客户端已连接（{self.server_command}）")

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: Optional[int] = None,
    ) -> dict:
        """
        调用 MCP Server 上的工具

        参数：
            tool_name: 工具名称，例如 "ruff-check"
            arguments: 工具参数，例如 {"code": "print('hello')"}
            timeout:   本次调用的超时时间（秒），默认使用 self.timeout
        """
        if not self._session or not self._connected:
            raise ConnectionError(
                "MCP 客户端未连接。请先调用 connect() 建立连接。"
            )

        result = await asyncio.wait_for(
            self._session.call_tool(tool_name, arguments),
            timeout=timeout or self.timeout,
        )

        return result.model_dump() if hasattr(result, "model_dump") else result

    async def close(self) -> None:
        """关闭与 MCP Server 的连接"""
        if hasattr(self, '_stack'):
            await self._stack.aclose()  # 一次性关闭所有已进入的上下文

        self._session = None
        self._read = None
        self._write = None
        self._connected = False
        logger.info("MCP 客户端已断开")

async def _test():
    """
    快速验证 MCPClient 的基本功能

    测试内容：
        1. 连接 MCP Server（启动 mcp-server-analyzer 子进程）
        2. 调用 ruff_check 工具检查一段有格式问题的代码
        3. 确认返回结果包含 content
        4. 关闭连接

    前提：
        mcp-server-analyzer 需要已安装并在 PATH 中可用
    """
    client = MCPClient(timeout=10)

    try:
        print("=== 测试 1：连接 ===")
        await client.connect()
        print("✓ 连接成功")

        print("\n=== 测试 2：调 ruff_check 检查代码 ===")
        code = """
                import os
                def add(a,b):return a+b
                """
        result = await client.call_tool(
            tool_name="ruff-check",
            arguments={"code": code},
        )
        assert "content" in result, "返回结果缺少 content"
        print(f"✓ call_tool 成功")
        print(f"   返回内容块数：{len(result.get('content', []))}")
        print(result)

        # 提取文本输出
        texts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                texts.append(item.get("text", ""))
        output = "\n".join(texts)
        print(f"   Ruff 输出行数：{len(output.strip().split(chr(10)))}")
        if output:
            print(f"   第一条输出预览：{output}")

        print("\n=== 测试 3：关闭 ===")
        await client.close()
        print("✓ 关闭成功")

        print("\n✅ 所有测试通过！")

    except FileNotFoundError as e:
        print(f"❌ mcp-server-analyzer 未安装：{e}")
        print("   请先安装：pip install mcp-server-analyzer")
    except Exception as e:
        print(f"❌ 测试失败：{type(e).__name__}: {e}")
        await client.close()


if __name__ == "__main__":
    
    import asyncio
    asyncio.run(_test())



