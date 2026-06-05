"""ToolClient 抽象层 — Agent 工具调用的统一接口

实现三种 ToolClient：
1. HttpToolClient：直接 HTTP 调用模拟器 API（降级方案）
2. McpToolClient：通过 MCP Server 调用（标准协议）
3. AutoToolClient：自动选择（优先 MCP，降级 HTTP）

Agent 代码只调 self.tool_client.call_tool(...)，不关心底层实现。
"""

from abc import ABC, abstractmethod
import json
import logging
import httpx
from exceptions import MCPConnectionError

logger = logging.getLogger("tool_client")


# ── 工具名称常量 ──────────────────────────────────────────

TOOL_GET_METRICS = "get_metrics"
TOOL_GET_ALL_METRICS = "get_all_metrics"
TOOL_GET_TOPOLOGY = "get_topology"
TOOL_INJECT_FAULT = "inject_fault"
TOOL_CLEAR_FAULT = "clear_fault"
TOOL_CLEAR_ALL_FAULTS = "clear_all_faults"
TOOL_LIST_FAULTS = "list_faults"
TOOL_EXECUTE_REPAIR = "execute_repair"


class ToolClient(ABC):
    """工具调用抽象基类。"""

    @abstractmethod
    async def call_tool(self, name: str, params: dict) -> dict:
        """调用指定工具，返回结果 dict。"""
        ...

    @abstractmethod
    async def close(self):
        """关闭客户端连接。"""
        ...

    @property
    @abstractmethod
    def protocol(self) -> str:
        """返回使用的协议名称。"""
        ...


class HttpToolClient(ToolClient):
    """通过 HTTP 直接调用模拟器 API。

    降级方案，不依赖 MCP Server。
    """

    def __init__(self, simulator_url: str = "http://127.0.0.1:8001"):
        self._base = simulator_url
        self._http = httpx.AsyncClient(timeout=30.0)

    @property
    def protocol(self) -> str:
        return "http"

    async def call_tool(self, name: str, params: dict) -> dict:
        # 路由表使用函数延迟求值
        _ROUTES = {
            TOOL_GET_METRICS:     lambda p: ("GET", f"/simulator/metrics?domain={p.get('region', '')}"),
            TOOL_GET_ALL_METRICS: lambda p: ("GET", "/simulator/metrics"),
            TOOL_GET_TOPOLOGY:    lambda p: ("GET", "/simulator/topology"),
            TOOL_INJECT_FAULT:    lambda p: ("POST", f"/simulator/fault/inject?fault_type={p['fault_type']}&target={p['target']}"),
            TOOL_CLEAR_FAULT:     lambda p: ("POST", f"/simulator/fault/clear?fault_id={p['fault_id']}"),
            TOOL_CLEAR_ALL_FAULTS: lambda p: ("GET", "/simulator/fault/clear_all"),
            TOOL_LIST_FAULTS:     lambda p: ("GET", "/simulator/faults"),
            TOOL_EXECUTE_REPAIR:  lambda p: ("POST", "/simulator/repair"),
        }

        if name not in _ROUTES:
            return {"error": f"Unknown tool: {name}"}

        method, path = _ROUTES[name](params)
        if method == "GET":
            resp = await self._http.get(f"{self._base}{path}")
        else:
            resp = await self._http.post(f"{self._base}{path}", json=params)

        resp.raise_for_status()
        return resp.json()

    async def close(self):
        await self._http.aclose()


class McpToolClient(ToolClient):
    """通过 MCP Server 调用工具。

    使用 MCP 协议（Model Context Protocol）调用工具。
    MCP 是 Anthropic 提出的标准工具调用协议。
    """

    def __init__(self, mcp_server_url: str = "http://127.0.0.1:8000/mcp"):
        self._server_url = mcp_server_url
        self._http = httpx.AsyncClient(timeout=30.0)
        self._session = None
        self._tools_cache = None

    @property
    def protocol(self) -> str:
        return "mcp"

    async def _ensure_session(self):
        """确保 MCP 会话已建立。"""
        if self._session is not None:
            return

        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            # 建立 MCP 连接
            transport = await streamablehttp_client(self._server_url).__aenter__()
            read_stream, write_stream, _ = transport
            self._session = await ClientSession(read_stream, write_stream).__aenter__()
            await self._session.initialize()

            # 缓存工具列表
            tools_result = await self._session.list_tools()
            self._tools_cache = {t.name: t for t in tools_result.tools}
            logger.info("MCP session established, %d tools available", len(self._tools_cache))

        except Exception as e:
            logger.warning("Failed to establish MCP session: %s", e)
            self._session = None

    async def call_tool(self, name: str, params: dict) -> dict:
        """通过 MCP 协议调用工具。"""
        await self._ensure_session()

        if self._session is None:
            raise MCPConnectionError("MCP session not available")

        try:
            # 调用 MCP 工具
            result = await self._session.call_tool(name, params)

            # 解析 MCP 返回的 TextContent
            if hasattr(result, 'content') and result.content:
                for block in result.content:
                    if hasattr(block, 'text'):
                        return json.loads(block.text)

            return {"result": "ok"}

        except Exception as e:
            logger.error("MCP tool call failed: %s", e)
            raise

    async def close(self):
        """关闭 MCP 会话。"""
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except (ConnectionError, OSError, RuntimeError) as exc:
                logger.debug("Error closing MCP session: %s", exc)
        await self._http.aclose()


class AutoToolClient(ToolClient):
    """自动选择 ToolClient。

    优先使用 MCP 协议，MCP 不可用时降级为 HTTP。
    这是推荐使用的 ToolClient 类型。
    """

    def __init__(self, mcp_server_url: str = "http://127.0.0.1:8000/mcp"):
        self._mcp_client = McpToolClient(mcp_server_url)
        self._http_client = HttpToolClient("http://127.0.0.1:8001")
        self._use_mcp = True
        self._initialized = False

    @property
    def protocol(self) -> str:
        return "mcp" if self._use_mcp else "http"

    async def call_tool(self, name: str, params: dict) -> dict:
        """调用工具，优先 MCP，降级 HTTP。"""

        # 首次调用时尝试初始化 MCP
        if not self._initialized:
            self._initialized = True
            try:
                await self._mcp_client._ensure_session()
                if self._mcp_client._session is None:
                    self._use_mcp = False
                    logger.info("MCP not available, using HTTP fallback")
                else:
                    logger.info("Using MCP protocol for tool calls")
            except Exception:
                self._use_mcp = False
                logger.info("MCP initialization failed, using HTTP fallback")

        # 调用工具
        if self._use_mcp:
            try:
                return await self._mcp_client.call_tool(name, params)
            except Exception as e:
                logger.warning("MCP call failed, falling back to HTTP: %s", e)
                self._use_mcp = False

        return await self._http_client.call_tool(name, params)

    async def close(self):
        """关闭所有客户端。"""
        await self._mcp_client.close()
        await self._http_client.close()


# ── 工厂函数 ──────────────────────────────────────────────

def create_tool_client(config: dict = None, prefer_mcp: bool = True) -> ToolClient:
    """创建 ToolClient 实例。

    Args:
        config: 配置字典
        prefer_mcp: 是否优先使用 MCP 协议

    Returns:
        ToolClient 实例
    """
    simulator_url = "http://127.0.0.1:8001"
    if config:
        simulator_url = f"http://127.0.0.1:{config.get('simulator', {}).get('port', 8001)}"

    if prefer_mcp:
        return AutoToolClient(simulator_url)
    else:
        return HttpToolClient(simulator_url)
