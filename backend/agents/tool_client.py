"""ToolClient 抽象层 — Agent 工具调用的统一接口

- HttpToolClient：直接 HTTP 调用模拟器 API（Phase 4 开发调试用）
- McpToolClient：通过 MCP Server 调用（Phase 3 MCP 就绪后切换）

Agent 代码只调 self.tool_client.call_tool(...)，不关心底层实现。
"""

from abc import ABC, abstractmethod
import httpx


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


class HttpToolClient(ToolClient):
    """通过 HTTP 直接调用模拟器 API。

    用于 Phase 4 Agent 开发阶段，不依赖 MCP Server。
    """

    def __init__(self, simulator_url: str = "http://localhost:8001"):
        self._base = simulator_url
        self._http = httpx.AsyncClient(timeout=30.0)

    async def call_tool(self, name: str, params: dict) -> dict:
        # 路由表使用函数延迟求值，避免 f-string 立即展开导致 KeyError
        _ROUTES = {
            TOOL_GET_METRICS:     lambda p: ("GET", f"/simulator/metrics?domain={p.get('region', '')}"),
            TOOL_GET_ALL_METRICS: lambda p: ("GET", "/simulator/metrics"),
            TOOL_GET_TOPOLOGY:    lambda p: ("GET", "/simulator/topology"),
            TOOL_INJECT_FAULT:    lambda p: ("POST", f"/simulator/fault/inject?fault_type={p['fault_type']}&target={p['target']}"),
            TOOL_CLEAR_FAULT:     lambda p: ("POST", f"/simulator/fault/clear?fault_id={p['fault_id']}"),
            TOOL_CLEAR_ALL_FAULTS: lambda p: ("GET", "/simulator/fault/clear_all"),
            TOOL_LIST_FAULTS:     lambda p: ("GET", "/simulator/faults"),
            TOOL_EXECUTE_REPAIR:  lambda p: ("GET", "/simulator/fault/clear_all"),
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

    优先使用 MCP 协议，MCP Server 不可用时降级为 HTTP 直连。
    """

    def __init__(self, mcp_server_url: str = "http://127.0.0.1:8001", mcp_session=None):
        self._mcp = mcp_session
        # HTTP 降级客户端
        self._http_fallback = HttpToolClient(mcp_server_url)
        self._http = httpx.AsyncClient(timeout=30.0)

    async def call_tool(self, name: str, params: dict) -> dict:
        # 优先 MCP
        if self._mcp:
            try:
                result = await self._mcp.call_tool(name, params)
                # MCP 返回 TextContent，提取文本
                if hasattr(result, 'content') and result.content:
                    import json
                    for block in result.content:
                        if hasattr(block, 'text'):
                            return json.loads(block.text)
                return result
            except Exception:
                pass  # MCP 失败 → 降级

        # HTTP 降级
        return await self._http_fallback.call_tool(name, params)

    async def close(self):
        await self._http.aclose()
        await self._http_fallback.close()
