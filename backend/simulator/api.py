"""网络模拟器 — HTTP API + WebSocket 推送

独立 FastAPI App，端口 8001。
- REST 端点：指标查询、拓扑查询、故障注入/清除
- WebSocket：每秒推送全部域指标快照
"""

import asyncio
import json
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from simulator.topology import DOMAINS, DOMAIN_NODES, get_all_links
from simulator.state import init_state, get_state
from simulator.generator import get_all_domain_metrics, get_domain_metrics
from simulator.faults import (
    FAULT_ACTIONS, clear_fault, list_active_faults,
    REPAIR_HANDLERS, FAULT_REPAIR_STRATEGIES, get_fault_summary,
)

app = FastAPI(title="IoA Network Simulator")

# 安全配置：仅允许本地访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# WebSocket 连接池
_ws_clients: list[WebSocket] = []


# ── REST 端点 ─────────────────────────────────────────────

@app.get("/simulator/metrics")
async def get_metrics(domain: str | None = None):
    """获取网络指标快照。可选 domain 参数过滤单个域。"""
    if domain:
        return {"ts_ms": int(time.time() * 1000), "metrics": get_domain_metrics(domain)}
    return {"ts_ms": int(time.time() * 1000), "metrics": get_all_domain_metrics()}


@app.get("/simulator/topology")
async def get_topology():
    """获取网络拓扑数据（节点+链路）。"""
    links = [{"from": f, "to": t, "bandwidth_gbps": bw} for f, t, bw in get_all_links()]
    return {
        "domains": DOMAINS,
        "nodes": {
            domain: {
                "edge_router": nodes["edge_router"],
                "servers": nodes["servers"],
                "terminal_count": len(nodes["terminals"]),
            }
            for domain, nodes in DOMAIN_NODES.items()
        },
        "links": links,
    }


@app.post("/simulator/fault/inject")
async def inject_fault(fault_type: str, target: str):
    """注入故障。fault_type 见 simulator/faults.py FAULT_ACTIONS。"""
    if fault_type not in FAULT_ACTIONS:
        return {"status": "error", "message": f"Unknown fault type: {fault_type}",
                "available": list(FAULT_ACTIONS.keys())}
    result = FAULT_ACTIONS[fault_type](target)
    if isinstance(result, dict):
        return {"status": "ok" if result.get("success") else "error", **result}
    return {"status": "ok", "fault_id": result, "fault_type": fault_type, "target": target}


@app.post("/simulator/fault/clear")
async def clear_fault_endpoint(fault_id: str):
    """清除指定故障。"""
    ok = clear_fault(fault_id)
    return {"status": "ok" if ok else "not_found", "fault_id": fault_id}


@app.get("/simulator/fault/clear_all")
async def clear_all_faults():
    """清除所有故障。"""
    get_state().clear_all_faults()
    return {"status": "ok"}


@app.post("/simulator/repair")
async def apply_repair(action: dict):
    """Apply a specific repair action to the simulated network.

    Request body:
        {"action_type": "route_switch", "target": "link-xxx", "params": {"backup_link_id": "link-yyy"}}
    """
    action_type = action.get("action_type", "")
    target = action.get("target", "")
    params = action.get("params", {})

    handler = REPAIR_HANDLERS.get(action_type)
    if not handler:
        from fastapi import HTTPException
        raise HTTPException(400, f"Unknown repair action: {action_type}. Available: {list(REPAIR_HANDLERS)}")

    try:
        result = handler(target, **params)
        return {"status": "applied", **result}
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("Repair action %s failed on %s", action_type, target)
        from fastapi import HTTPException
        raise HTTPException(500, f"Repair failed: {e}")


@app.get("/simulator/repair/strategies")
async def list_repair_strategies():
    """List all fault types and their repair strategies."""
    return FAULT_REPAIR_STRATEGIES


@app.get("/simulator/faults/summary")
async def list_faults_summary():
    """List active faults with their recommended repair strategies."""
    return get_fault_summary()


@app.get("/simulator/faults")
async def get_faults():
    """列出所有激活故障。"""
    return {"faults": list_active_faults()}


# ── WebSocket ──────────────────────────────────────────────

@app.websocket("/simulator/ws")
async def ws_endpoint(ws: WebSocket):
    """每秒推送一次全量指标 + 活跃故障列表。"""
    await ws.accept()
    _ws_clients.append(ws)
    try:
        while True:
            data = {
                "type": "metrics",
                "ts_ms": int(time.time() * 1000),
                "data": {
                    "regions": get_all_domain_metrics(),
                    "faults": list_active_faults(),
                },
            }
            try:
                await ws.send_text(json.dumps(data))
            except Exception:
                break
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.remove(ws)


# ── 生命周期 ──────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    """初始化模拟器状态（基于拓扑链路表）。"""
    init_state(get_all_links())


@app.on_event("shutdown")
async def shutdown():
    """清理 WebSocket 连接。"""
    for ws in _ws_clients:
        try:
            await ws.close()
        except (WebSocketDisconnect, RuntimeError, OSError) as exc:
            logger.debug("Error closing simulator WebSocket: %s", exc)
