"""DAG 调度引擎

核心任务编排器，负责：
1. 拓扑排序 → 确定节点执行顺序
2. Agent 匹配 → 按能力标签 + 域查询注册中心
3. 任务分发 → 通过消息路由总线发送给 Agent
4. 结果收集 → 连接 WebSocket 作为 "orchestrator" 接收结果
5. 重试逻辑 → 失败节点按 max_retries 自动重试
6. 审计日志 → 所有关键事件写入 audit_logs

单例模式 — 整个中间件只有一个调度器实例。
"""

import asyncio
import json
import logging
import uuid
import time
from collections import deque

import httpx
import websockets

from ioa_middleware.bus import MessageBus
from ioa_middleware.orchestrator.models import (
    DagDefinition,
    DagNodeDef,
    DagStatus,
    NodeStatus,
    DagState,
    DagNodeState,
)
from ioa_middleware.orchestrator import store
from ioa_middleware.router import SmartRouter

logger = logging.getLogger("orchestrator.scheduler")

# 调度器轮询间隔
SCHEDULE_INTERVAL_SEC = 1.0
# 调度器 agent_id
ORCHESTRATOR_AGENT_ID = "orchestrator"


class DagScheduler:
    """DAG 调度器单例。

    生命周期：
        scheduler = DagScheduler(bus, config)
        await scheduler.start()   # 连接 WS + 启动调度循环
        scheduler.submit(dag_def) # 提交 DAG
        await scheduler.stop()    # 关闭连接
    """

    def __init__(self, bus: MessageBus, config: dict):
        self._bus = bus
        self._config = config
        self._http = httpx.AsyncClient(timeout=30.0)
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._running = False
        self._dags: dict[str, DagState] = {}  # 内存中的 DAG 状态缓存
        self._result_queue: asyncio.Queue = asyncio.Queue(maxsize=200)

        # 中间件地址（连接用 localhost，非 bind 地址）
        port = config.get("middleware", {}).get("port", 8000)
        psk = config.get("auth", {}).get("pre_shared_key", "")
        self._middleware_base = f"http://127.0.0.1:{port}"
        self._ws_url = f"ws://127.0.0.1:{port}/messages/ws?agent_id={ORCHESTRATOR_AGENT_ID}&token={psk}"
        self._auth_header = {"Authorization": f"Bearer {psk}"}

        # 语义路由引擎（SmartRouter 自动选择最佳可用引擎）
        self._router = SmartRouter()

        # 日志退避计数器：node_id → 连续无 Agent 次数
        self._no_agent_count: dict[str, int] = {}
        self._no_agent_log_interval = 10  # 每 10 次才 log 一次

    # ── 公开 API ──────────────────────────────────────────

    async def start(self) -> None:
        """启动调度器：连接 WebSocket + 启动后台循环。"""
        self._running = True
        asyncio.create_task(self._ws_listen())
        asyncio.create_task(self._schedule_loop())
        logger.info("DagScheduler started")

    async def stop(self) -> None:
        """停止调度器。"""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        await self._http.aclose()
        logger.info("DagScheduler stopped")

    async def submit(self, dag_def: DagDefinition) -> str:
        """提交 DAG 任务。返回 dag_id。"""
        dag_id = dag_def.dag_id

        # 校验：DAG 中不能有重复 node_id
        node_ids = [n.node_id for n in dag_def.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError(f"DAG {dag_id}: duplicate node_id in definition")

        # 校验：depends_on 引用的节点必须存在
        for node in dag_def.nodes:
            for dep in node.depends_on:
                if dep not in node_ids:
                    raise ValueError(f"DAG {dag_id}: node {node.node_id} depends on unknown node {dep}")

        # 校验：无循环依赖（拓扑排序可行性检测）
        self._topological_sort(dag_def.nodes)

        # 持久化
        await store.create_dag(dag_def)
        await store.create_dag_nodes(dag_id, dag_def.nodes)

        # 构建内存状态
        state = DagState(
            dag_id=dag_id,
            correlation_id=dag_def.correlation_id,
            description=dag_def.description,
            status=DagStatus.pending,
            submitted_at_ms=int(time.time() * 1000),
        )
        for node in dag_def.nodes:
            state.nodes[node.node_id] = DagNodeState(
                dag_id=dag_id,
                node_id=node.node_id,
                node_type=node.type,
                capability=node.capability,
                domain=node.domain,
                params=node.params,
                depends_on=node.depends_on,
                max_retries=node.max_retries,
                timeout_ms=node.timeout_ms,
            )
        self._dags[dag_id] = state

        # 审计
        await store.write_audit(
            "dag.submitted",
            detail={"dag_id": dag_id, "node_count": len(dag_def.nodes)},
            correlation_id=dag_def.correlation_id,
        )

        # 触发调度
        await self._try_start_dag(dag_id)

        logger.info("DAG %s submitted (%d nodes)", dag_id, len(dag_def.nodes))
        return dag_id

    async def get_dag_state(self, dag_id: str) -> dict | None:
        """获取 DAG 状态（从 DB 读取最新）。"""
        dag = await store.get_dag(dag_id)
        if not dag:
            return None
        nodes = await store.get_dag_nodes(dag_id)
        dag["nodes"] = nodes
        return dag

    async def list_dags(self, status: str | None = None) -> list[dict]:
        """列出所有 DAG。"""
        return await store.list_dags(status=status)

    # ── WebSocket 监听 ────────────────────────────────────

    async def _ws_listen(self) -> None:
        """WebSocket 监听循环：连接消息总线，接收发给 orchestrator 的结果。"""
        while self._running:
            try:
                async with websockets.connect(self._ws_url) as ws:
                    self._ws = ws
                    logger.info("Scheduler connected to message bus")
                    while self._running:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                            msg = json.loads(raw)
                            # 只处理 result 类型的消息
                            intent_type = msg.get("intent", {}).get("type", "")
                            if intent_type == "result":
                                await self._result_queue.put(msg)
                            elif msg.get("type") == "ping":
                                await ws.send(json.dumps({"type": "pong"}))
                        except asyncio.TimeoutError:
                            continue
                        except websockets.ConnectionClosed:
                            break
            except Exception:
                logger.exception("Scheduler WS connection error, retrying in 3s")
            await asyncio.sleep(3)

    # ── 调度循环 ──────────────────────────────────────────

    async def _schedule_loop(self) -> None:
        """主调度循环：处理结果 + 检查可调度节点。"""
        while self._running:
            try:
                # 1. 消费结果队列
                while not self._result_queue.empty():
                    msg = await self._result_queue.get()
                    await self._handle_result(msg)

                # 2. 扫描运行中的 DAG，调度就绪节点
                for dag_id, state in list(self._dags.items()):
                    if state.status == DagStatus.running:
                        await self._schedule_ready_nodes(state)
                        await self._check_dag_completion(state)

            except Exception:
                logger.exception("Schedule iteration error")

            await asyncio.sleep(SCHEDULE_INTERVAL_SEC)

    # ── 拓扑排序 ──────────────────────────────────────────

    def _topological_sort(self, nodes: list[DagNodeDef]) -> list[DagNodeDef]:
        """Kahn 算法拓扑排序。返回按依赖顺序排列的节点列表。

        Raises ValueError if cycle detected.
        """
        node_map = {n.node_id: n for n in nodes}
        in_degree = {n.node_id: len(n.depends_on) for n in nodes}
        adj = {n.node_id: [] for n in nodes}
        for n in nodes:
            for dep in n.depends_on:
                adj[dep].append(n.node_id)

        queue = deque([nid for nid, deg in in_degree.items() if deg == 0])
        result = []

        while queue:
            nid = queue.popleft()
            result.append(node_map[nid])
            for downstream in adj[nid]:
                in_degree[downstream] -= 1
                if in_degree[downstream] == 0:
                    queue.append(downstream)

        if len(result) != len(nodes):
            raise ValueError("DAG contains a cycle")
        return result

    # ── DAG 启动 ──────────────────────────────────────────

    async def _try_start_dag(self, dag_id: str) -> None:
        """尝试启动 DAG（将 pending → running）。"""
        state = self._dags.get(dag_id)
        if not state or state.status != DagStatus.pending:
            return

        state.status = DagStatus.running
        await store.update_dag_status(dag_id, DagStatus.running)
        await store.write_audit("dag.started", correlation_id=state.correlation_id,
                                detail={"dag_id": dag_id})
        logger.info("DAG %s started", dag_id)

    # ── 节点调度 ──────────────────────────────────────────

    async def _schedule_ready_nodes(self, state: DagState) -> None:
        """为 DAG 中所有就绪（依赖满足 + pending）的节点分配 Agent。"""
        for node in state.nodes.values():
            if node.status != NodeStatus.pending:
                continue

            # 检查依赖是否全部完成
            if not self._dependencies_satisfied(state, node):
                continue

            # 分配 Agent
            await self._dispatch_node(state, node)

    def _dependencies_satisfied(self, state: DagState, node: DagNodeState) -> bool:
        """检查节点的所有依赖是否已完成。"""
        for dep_id in node.depends_on:
            dep_node = state.nodes.get(dep_id)
            if not dep_node or dep_node.status != NodeStatus.completed:
                return False
        return True

    def _throttled_warn(self, node_id: str, fmt: str, *args):
        """日志退避：同一 node 连续无 Agent 时降低日志频率。"""
        count = self._no_agent_count.get(node_id, 0) + 1
        self._no_agent_count[node_id] = count
        if count == 1 or count % self._no_agent_log_interval == 0:
            logger.warning(fmt + (" (×%d)" if count > 1 else ""), *args,
                           *([] if count == 1 else [count]))

    def _collect_upstream_outputs(
        self, state: DagState, node: DagNodeState, params: dict, visited: set
    ) -> None:
        """递归收集所有上游节点的输出到 params。"""
        for dep_id in node.depends_on:
            if dep_id in visited:
                continue
            visited.add(dep_id)
            dep_node = state.nodes.get(dep_id)
            if dep_node and dep_node.output:
                # 按 node_type 注入（如 "monitor" / "diagnose" / "repair"）
                params[dep_node.node_type] = dep_node.output
                # 也按 node_id 注入
                params[dep_id] = dep_node.output
            # 递归收集上游的上游
            if dep_node:
                self._collect_upstream_outputs(state, dep_node, params, visited)

    async def _dispatch_node(self, state: DagState, node: DagNodeState) -> None:
        """为节点匹配 Agent 并分发任务。"""
        # 1. 查询注册中心，按能力标签 + 域匹配 Agent
        try:
            resp = await self._http.get(
                f"{self._middleware_base}/registry/query",
                params={"capability": node.capability},
            )
            resp.raise_for_status()
            agents = resp.json().get("agents", [])
        except Exception:
            logger.exception("Failed to query registry for capability %s", node.capability)
            return

        if not agents:
            self._throttled_warn(node.node_id,
                "No agent available for node %s (cap=%s, domain=%s)",
                node.node_id, node.capability, node.domain)
            return

        # 2. 语义路由选择最佳 Agent
        task_desc = state.description or f"{node.node_type} {node.capability} {node.domain or ''}"
        agent = await self._router.select(
            candidates=agents,
            capability=node.capability,
            domain=node.domain,
            task_desc=task_desc,
        )

        if not agent:
            self._throttled_warn(node.node_id,
                "SemanticRouter: no suitable agent for node %s", node.node_id)
            return

        # 4. 分配节点
        node.assigned_agent = agent["agent_id"]
        node.status = NodeStatus.assigned
        await store.assign_node(state.dag_id, node.node_id, agent["agent_id"])

        correlation_id = state.correlation_id or state.dag_id

        # 5. 收集所有上游节点输出（递归）→ 注入下游节点 params
        merged_params = dict(node.params)
        self._collect_upstream_outputs(state, node, merged_params, visited=set())

        # 6. 构造任务消息并发送
        task_msg = {
            "msg_id": str(uuid.uuid4()),
            "from_agent": ORCHESTRATOR_AGENT_ID,
            "to_agent": agent["agent_id"],
            "intent": {
                "type": "task",
                "description": f"Execute node {node.node_id} (type={node.node_type})",
                "priority": "normal",
            },
            "payload": {
                "dag_id": state.dag_id,
                "node_id": node.node_id,
                "node_type": node.node_type,
                "capability": node.capability,
                "params": merged_params,
            },
            "correlation_id": correlation_id,
            "ts_ms": int(time.time() * 1000),
        }

        try:
            resp = await self._http.post(
                f"{self._middleware_base}/messages",
                json=task_msg,
                headers=self._auth_header,
            )
            resp.raise_for_status()
            logger.info("Dispatched node %s → agent %s", node.node_id, agent["agent_id"])
            self._no_agent_count.pop(node.node_id, None)  # 成功后重置退避计数
        except Exception:
            logger.exception("Failed to dispatch node %s", node.node_id)
            node.status = NodeStatus.failed
            await store.fail_node(state.dag_id, node.node_id,
                                  {"error": "Failed to send task to agent"})
            return

        # 6. 审计
        await store.write_audit(
            "node.assigned",
            from_agent=ORCHESTRATOR_AGENT_ID,
            to_agent=agent["agent_id"],
            msg_id=task_msg["msg_id"],
            detail={"dag_id": state.dag_id, "node_id": node.node_id, "capability": node.capability},
            correlation_id=correlation_id,
        )

    # ── 结果处理 ──────────────────────────────────────────

    async def _handle_result(self, msg: dict) -> None:
        """处理 Agent 返回的节点执行结果。"""
        payload = msg.get("payload", {})
        dag_id = payload.get("dag_id")
        node_id = payload.get("node_id")
        result = payload.get("result", {})

        if not dag_id or not node_id:
            logger.warning("Result message missing dag_id/node_id: %s", msg.get("msg_id"))
            return

        state = self._dags.get(dag_id)
        if not state:
            logger.warning("Result for unknown DAG %s", dag_id)
            return

        node = state.nodes.get(node_id)
        if not node:
            logger.warning("Result for unknown node %s in DAG %s", node_id, dag_id)
            return

        success = result.get("success", result.get("status") == "ok")
        output = result.get("output", result)

        if success:
            node.status = NodeStatus.completed
            node.output = output
            node.finished_at_ms = int(time.time() * 1000)
            await store.complete_node(dag_id, node_id, output)
            await store.write_audit(
                "node.completed",
                from_agent=msg.get("from_agent"),
                detail={"dag_id": dag_id, "node_id": node_id, "output": output},
                correlation_id=msg.get("correlation_id"),
            )
            # Bandit feedback: successful execution → reward 1.0
            from ioa_middleware.router.bandit_router import get_bandit
            if node.assigned_agent:
                get_bandit().record(node.assigned_agent, reward=1.0)
            logger.info("Node %s/%s completed by %s", dag_id, node_id, msg.get("from_agent"))
        elif result.get("output", {}).get("retry_signal"):
            # 验证节点返回 retry → 重置上游 diagnose+repair 节点
            # Bandit feedback: retry = partial success → reward 0.5
            from ioa_middleware.router.bandit_router import get_bandit
            if node.assigned_agent:
                get_bandit().record(node.assigned_agent, reward=0.5)
            await self._handle_verify_retry(state, node, result, msg)
        else:
            error = result.get("error", "Unknown error")
            await self._handle_node_failure(state, node, error, msg)

    async def _handle_verify_retry(
        self, state: DagState, node: DagNodeState, result: dict, msg: dict
    ) -> None:
        """验证失败 → 重置 diagnose + repair 节点为 pending 以触发重试。"""
        retry_count = result.get("output", {}).get("retry_count", 1)
        retry_nodes = result.get("_retry_dag_nodes", ["diagnose", "repair"])

        logger.info("Verify retry #%d for DAG %s, resetting %s",
                    retry_count, state.dag_id, retry_nodes)

        # 重置验证节点本身
        node.status = NodeStatus.pending
        node.retry_count = retry_count - 1
        await store.retry_node(state.dag_id, node.node_id)
        node.output = {"verify_retry_count": retry_count}

        # 重置 diagnose 和 repair 节点
        for nid_suffix in retry_nodes:
            for nid, n in state.nodes.items():
                if nid_suffix in nid.lower() and n.status == NodeStatus.completed:
                    n.status = NodeStatus.pending
                    n.retry_count += 1
                    await store.retry_node(state.dag_id, nid)
                    logger.info("  Reset node %s for retry", nid)

        await store.write_audit(
            "verify.retry",
            detail={"dag_id": state.dag_id, "retry_count": retry_count,
                    "retry_nodes": retry_nodes},
            correlation_id=msg.get("correlation_id"),
        )

    async def _handle_node_failure(
        self, state: DagState, node: DagNodeState, error: str, msg: dict
    ) -> None:
        """处理节点失败：重试或标记失败。"""
        if node.retry_count < node.max_retries:
            # 重试
            new_count = await store.retry_node(state.dag_id, node.node_id)
            node.status = NodeStatus.pending  # 重置为 pending 以触发重新调度
            node.retry_count = new_count
            node.assigned_agent = None
            await store.write_audit(
                "node.retrying",
                detail={"dag_id": state.dag_id, "node_id": node.node_id,
                        "retry_count": new_count, "error": error},
                correlation_id=msg.get("correlation_id"),
            )
            logger.info("Retrying node %s/%s (attempt %d/%d)",
                        state.dag_id, node.node_id, new_count, node.max_retries)
        else:
            # 重试耗尽，标记失败
            node.status = NodeStatus.failed
            node.output = {"error": error}
            node.finished_at_ms = int(time.time() * 1000)
            await store.fail_node(state.dag_id, node.node_id, {"error": error})
            await store.write_audit(
                "node.failed",
                from_agent=msg.get("from_agent"),
                detail={"dag_id": state.dag_id, "node_id": node.node_id,
                        "error": error, "retries_exhausted": True},
                correlation_id=msg.get("correlation_id"),
            )
            # Bandit feedback: failure → reward 0.0
            from ioa_middleware.router.bandit_router import get_bandit
            if node.assigned_agent:
                get_bandit().record(node.assigned_agent, reward=0.0)
            logger.warning("Node %s/%s failed after %d retries: %s",
                           state.dag_id, node.node_id, node.max_retries, error)

    # ── DAG 完成检查 ──────────────────────────────────────

    async def _check_dag_completion(self, state: DagState) -> None:
        """检查 DAG 是否已完成（成功或失败）。"""
        nodes = list(state.nodes.values())

        # 任一节点失败 → DAG 失败
        failed = [n for n in nodes if n.status == NodeStatus.failed]
        if failed:
            state.status = DagStatus.failed
            state.finished_at_ms = int(time.time() * 1000)
            state.result = {"error": f"Nodes failed: {[n.node_id for n in failed]}"}
            await store.update_dag_status(state.dag_id, DagStatus.failed, state.result)
            await store.write_audit(
                "dag.failed",
                detail={"dag_id": state.dag_id, "failed_nodes": [n.node_id for n in failed]},
                correlation_id=state.correlation_id,
            )
            logger.warning("DAG %s failed: %s", state.dag_id, [n.node_id for n in failed])
            return

        # 全部完成 → DAG 完成
        all_done = all(n.status == NodeStatus.completed for n in nodes)
        if all_done:
            state.status = DagStatus.completed
            state.finished_at_ms = int(time.time() * 1000)
            state.result = {"status": "completed", "nodes": {
                n.node_id: n.output for n in nodes
            }}
            await store.update_dag_status(state.dag_id, DagStatus.completed, state.result)
            await store.write_audit(
                "dag.completed",
                detail={"dag_id": state.dag_id, "node_count": len(nodes)},
                correlation_id=state.correlation_id,
            )
            logger.info("DAG %s completed (%d nodes)", state.dag_id, len(nodes))


# ── 全局单例 ──────────────────────────────────────────────

_scheduler: DagScheduler | None = None


def get_scheduler() -> DagScheduler:
    """获取调度器单例。"""
    global _scheduler
    if _scheduler is None:
        raise RuntimeError("DagScheduler not initialized. Call init_scheduler() first.")
    return _scheduler


def init_scheduler(bus: MessageBus, config: dict) -> DagScheduler:
    """初始化调度器单例。"""
    global _scheduler
    _scheduler = DagScheduler(bus, config)
    return _scheduler
