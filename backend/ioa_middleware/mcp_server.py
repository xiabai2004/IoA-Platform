"""MCP Server — 将模拟器 API 封装为 MCP 工具

架构方案 v2 §4.2：MCP Server 作为 Agent 与模拟器之间的标准协议层。

工具列表（6 个）：
- get_metrics(domain: str)        → 单个域指标
- get_all_metrics()                → 全域网指标
- get_topology()                   → 网络拓扑
- inject_fault(fault_type, target) → 注入故障
- clear_fault(fault_id: str)       → 清除故障
- list_faults()                    → 列出激活故障

启动方式：
    python -m ioa_middleware.mcp_server

或集成到 run.py 中作为后台任务。
"""

import asyncio
import json
import httpx

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent


# ── 模拟器 HTTP 客户端 ────────────────────────────────────

class SimulatorClient:
    """模拟器 HTTP API 客户端。"""

    def __init__(self, base_url: str = "http://127.0.0.1:8001"):
        self._base = base_url
        self._http = httpx.AsyncClient(timeout=30.0)

    async def get_metrics(self, domain: str = "") -> dict:
        url = f"{self._base}/simulator/metrics"
        if domain:
            url += f"?domain={domain}"
        resp = await self._http.get(url)
        resp.raise_for_status()
        return resp.json()

    async def get_topology(self) -> dict:
        resp = await self._http.get(f"{self._base}/simulator/topology")
        resp.raise_for_status()
        return resp.json()

    async def inject_fault(self, fault_type: str, target: str) -> dict:
        resp = await self._http.post(
            f"{self._base}/simulator/fault/inject?fault_type={fault_type}&target={target}"
        )
        resp.raise_for_status()
        return resp.json()

    async def clear_fault(self, fault_id: str) -> dict:
        resp = await self._http.post(
            f"{self._base}/simulator/fault/clear?fault_id={fault_id}"
        )
        resp.raise_for_status()
        return resp.json()

    async def clear_all_faults(self) -> dict:
        resp = await self._http.get(f"{self._base}/simulator/fault/clear_all")
        resp.raise_for_status()
        return resp.json()

    async def list_faults(self) -> dict:
        resp = await self._http.get(f"{self._base}/simulator/faults")
        resp.raise_for_status()
        return resp.json()

    async def execute_repair(self, action_type: str, target: str, params: dict = None) -> dict:
        resp = await self._http.post(
            f"{self._base}/simulator/repair",
            json={"action_type": action_type, "target": target, "params": params or {}},
        )
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        await self._http.aclose()


# ── MCP Server ────────────────────────────────────────────

def create_mcp_server(sim_url: str = "http://127.0.0.1:8001") -> Server:
    """创建并配置 MCP Server。"""
    server = Server("ioa-simulator")
    sim = SimulatorClient(sim_url)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="get_metrics",
                description="获取网络域指标快照。可选 domain 参数过滤单个域（east-china/north-china/south-china/west-china）",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "domain": {
                            "type": "string",
                            "description": "域名，如 east-china。留空返回全部域",
                        }
                    },
                },
            ),
            Tool(
                name="get_all_metrics",
                description="获取全部 4 个域的网络指标快照",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="get_topology",
                description="获取网络拓扑数据（节点+链路）",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="inject_fault",
                description="向模拟器注入故障。fault_type: link_congestion/link_outage/cpu_overload/ddos/misconfig/device_failure",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "fault_type": {
                            "type": "string",
                            "description": "故障类型",
                            "enum": ["link_congestion", "link_outage", "cpu_overload", "ddos", "misconfig", "device_failure"],
                        },
                        "target": {
                            "type": "string",
                            "description": "目标域或设备名，如 east-china",
                        },
                    },
                    "required": ["fault_type", "target"],
                },
            ),
            Tool(
                name="clear_fault",
                description="清除指定故障",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "fault_id": {"type": "string", "description": "故障 ID"},
                    },
                    "required": ["fault_id"],
                },
            ),
            Tool(
                name="list_faults",
                description="列出所有激活的故障",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="execute_repair",
                description="执行修复操作。action_type: traffic_shape/route_switch/acl_deploy/link_failover/restart_service",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "action_type": {
                            "type": "string",
                            "description": "修复操作类型",
                            "enum": ["traffic_shape", "route_switch", "acl_deploy", "link_failover", "restart_service"],
                        },
                        "target": {
                            "type": "string",
                            "description": "修复目标（链路或设备名）",
                        },
                        "params": {
                            "type": "object",
                            "description": "修复参数",
                        },
                    },
                    "required": ["action_type", "target"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        handler = {
            "get_metrics": lambda: sim.get_metrics(arguments.get("domain", "")),
            "get_all_metrics": lambda: sim.get_metrics(""),
            "get_topology": sim.get_topology,
            "inject_fault": lambda: sim.inject_fault(
                arguments["fault_type"], arguments["target"]
            ),
            "clear_fault": lambda: sim.clear_fault(arguments["fault_id"]),
            "list_faults": sim.list_faults,
            "execute_repair": lambda: sim.execute_repair(
                arguments["action_type"], arguments["target"],
                arguments.get("params", {}),
            ),
        }

        fn = handler.get(name)
        if not fn:
            return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]

        try:
            result = await fn()
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        except Exception as e:
            return [TextContent(type="text", text=json.dumps({"error": str(e)}))]

    return server


# ── 入口 ──────────────────────────────────────────────────

async def main():
    """MCP Server 主入口（stdio 模式）。"""
    server = create_mcp_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def create_mcp_sse_app(sim_url: str = "http://127.0.0.1:8001"):
    """创建 MCP SSE 传输层的 ASGI 应用（供 FastAPI 挂载）。

    使用 SSE transport 替代 stdio，使得可通过 HTTP/SSE 连接 MCP Server。
    客户端使用 mcp.client.sse.sse_client 连接，与服务端 SseServerTransport 配对。
    """
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.requests import Request
    import asyncio as _asyncio

    mcp_server = create_mcp_server(sim_url)
    sse_transport = SseServerTransport("/messages")

    async def handle_sse(request: Request):
        # Windows CRLF fix: replace \r\n with \n in SSE output
        async def lf_send(msg):
            if msg.get("type") == "http.response.body":
                body = msg.get("body", b"")
                if isinstance(body, bytes) and b"\r\n" in body:
                    msg = {**msg, "body": body.replace(b"\r\n", b"\n"), "more_body": msg.get("more_body", False)}
            await request._send(msg)
        async with sse_transport.connect_sse(
            request.scope, request.receive, lf_send
        ) as (read_stream, write_stream):
            await mcp_server.run(
                read_stream, write_stream,
                mcp_server.create_initialization_options(),
            )

    async def handle_messages(request: Request):
        try:
            await sse_transport.handle_post_message(
                request.scope, request.receive, request._send
            )
        except Exception as exc:
            import logging
            _log = logging.getLogger("mcp_server")
            _log.exception("handle_post_message crashed")
            from starlette.responses import JSONResponse
            response = JSONResponse({"error": str(exc)}, status_code=500)
            await response(request.scope, request.receive, request._send)

    app = Starlette(routes=[
        Route("/sse", endpoint=handle_sse),
        Route("/messages", endpoint=handle_messages, methods=["POST"]),
    ])
    return app


def register_mcp_routes(app, sim_url: str = "http://127.0.0.1:8001"):
    """Store MCP config on app state for later startup in lifespan."""
    app.state._mcp_sim_url = sim_url


async def start_mcp_server(sim_url: str = "http://127.0.0.1:8001"):
    """Start MCP SSE server on port 9000 (called from lifespan)."""
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.routing import Route
    from starlette.applications import Starlette
    from starlette.requests import Request
    import logging
    _log = logging.getLogger("mcp_server")

    mcp_server = create_mcp_server(sim_url)
    sse_transport = SseServerTransport("/messages")

    async def handle_sse(request: Request):
        # Windows CRLF fix: replace \r\n with \n in SSE output
        async def lf_send(msg):
            if msg.get("type") == "http.response.body":
                body = msg.get("body", b"")
                if isinstance(body, bytes) and b"\r\n" in body:
                    msg = {**msg, "body": body.replace(b"\r\n", b"\n"), "more_body": msg.get("more_body", False)}
            await request._send(msg)
        async with sse_transport.connect_sse(
            request.scope, request.receive, lf_send
        ) as (read_stream, write_stream):
            await mcp_server.run(
                read_stream, write_stream,
                mcp_server.create_initialization_options(),
            )

    async def handle_messages(request: Request):
        await sse_transport.handle_post_message(
            request.scope, request.receive, request._send
        )

    starlette_app = Starlette(routes=[
        Route("/sse", handle_sse, methods=["GET"]),
        Route("/messages", handle_messages, methods=["POST"]),
    ])

    # Force Connection: close on /messages to fix HTTP/1.1 keep-alive on Windows
    from starlette.middleware.base import BaseHTTPMiddleware
    class CloseMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            if request.url.path == "/messages":
                response.headers["Connection"] = "close"
            return response
    starlette_app.add_middleware(CloseMiddleware)

    mcp_cfg = uvicorn.Config(starlette_app, host="127.0.0.1", port=9000, log_level="warning")
    server = uvicorn.Server(mcp_cfg)
    _log.info("MCP SSE server starting on :9000")
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
