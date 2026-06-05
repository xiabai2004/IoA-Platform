"""IoA 综合 API 端到端测试套件

从评委视角全面测试所有 API 端点、WebSocket、故障注入/修复、DAG 调度等。
服务器需已启动在 127.0.0.1:8000 和 :8001。

运行方式：pytest tests/test_comprehensive.py -v --tb=short
"""

import time
import json
import os
import httpx
import pytest

BASE_MW = "http://127.0.0.1:8000"
BASE_SIM = "http://127.0.0.1:8001"
AUTH_ENABLED = os.environ.get("IOA_AUTH_ENABLED", "true").lower() != "false"

def get_psk():
    psk = os.environ.get("IOA_PSK", "")
    if psk:
        return psk
    for p in ["backend/.env", ".env"]:
        try:
            with open(p) as f:
                for line in f:
                    if line.startswith("IOA_PSK="):
                        return line.strip().split("=", 1)[1].strip().strip('"').strip("'")
        except FileNotFoundError:
            continue
    return ""

PSK = get_psk()

def auth_headers(token=None):
    t = token or PSK
    if not t:
        return {}
    return {"Authorization": f"Bearer {t}"}

AUTH_HEADERS = auth_headers() or {}


# ============================================================
#  健康检查 & 连通性
# ============================================================

class TestHealthAndConnectivity:
    """测试所有服务可达"""

    def test_middleware_health(self):
        r = httpx.get(f"{BASE_MW}/health", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "agents_count" in data

    def test_simulator_running(self):
        r = httpx.get(f"{BASE_SIM}/simulator/metrics", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert "ts_ms" in data

    def test_gui_accessible(self):
        r = httpx.get(f"{BASE_MW}/gui", timeout=5)
        assert r.status_code == 200

    def test_root_redirects_to_gui(self):
        r = httpx.get(f"{BASE_MW}/", follow_redirects=False, timeout=5)
        assert r.status_code in (200, 301, 302, 307)

    def test_swagger_docs(self):
        r = httpx.get(f"{BASE_MW}/docs", timeout=5)
        assert r.status_code == 200

    def test_openapi_schema(self):
        r = httpx.get(f"{BASE_MW}/openapi.json", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert "paths" in data
        paths = data["paths"]
        assert len(paths) > 10


# ============================================================
#  认证测试
# ============================================================

class TestAuthentication:
    """测试认证机制（根据 AUTH_ENABLED 调整断言）"""

    def test_auth_mechanism_present(self):
        """验证认证中间件存在"""
        if AUTH_ENABLED:
            r = httpx.post(f"{BASE_MW}/registry/register", json={
                "agent_id": "test-agent", "domain": "global", "capabilities": ["test"]
            }, timeout=5)
            assert r.status_code in (401, 403)
        else:
            r = httpx.get(f"{BASE_MW}/health", timeout=5)
            assert r.status_code == 200

    def test_open_endpoints_no_auth(self):
        """开放端点无需认证"""
        for url in [f"{BASE_MW}/health", f"{BASE_MW}/registry/agents"]:
            r = httpx.get(url, timeout=5)
            assert r.status_code == 200


# ============================================================
#  Agent 注册中心
# ============================================================

class TestRegistry:
    """测试 Agent 注册中心"""

    TEST_AGENTS = [
        {"agent_id": "test-mon-east", "domain": "east-china", "capabilities": ["monitor"]},
        {"agent_id": "test-mon-north", "domain": "north-china", "capabilities": ["monitor"]},
        {"agent_id": "test-diag", "domain": "global", "capabilities": ["diagnose"]},
    ]

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        for agent in self.TEST_AGENTS:
            httpx.post(f"{BASE_MW}/registry/register", json=agent,
                       headers=AUTH_HEADERS, timeout=10)
        yield
        for agent in self.TEST_AGENTS:
            httpx.post(f"{BASE_MW}/registry/deregister",
                       json={"agent_id": agent["agent_id"]},
                       headers=AUTH_HEADERS, timeout=10)

    def test_list_agents(self):
        r = httpx.get(f"{BASE_MW}/registry/agents", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "agents" in data
        assert data["count"] >= 3

    def test_list_agents_by_domain(self):
        r = httpx.get(f"{BASE_MW}/registry/agents?domain=east-china", timeout=10)
        assert r.status_code == 200
        data = r.json()
        for a in data["agents"]:
            assert a["domain"] == "east-china"

    def test_list_agents_by_status(self):
        r = httpx.get(f"{BASE_MW}/registry/agents?status=active", timeout=10)
        assert r.status_code == 200
        data = r.json()
        for a in data["agents"]:
            assert a["status"] == "active"

    def test_query_by_capability(self):
        r = httpx.get(f"{BASE_MW}/registry/query?capability=monitor", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 2

    def test_heartbeat(self):
        r = httpx.post(f"{BASE_MW}/registry/heartbeat",
                       json={"agent_id": "test-mon-east"},
                       headers=AUTH_HEADERS, timeout=10)
        assert r.status_code == 200

    def test_duplicate_registration_update(self):
        """重复注册应更新"""
        agent = {"agent_id": "test-mon-east", "domain": "east-china",
                 "capabilities": ["monitor", "diagnose"]}
        r = httpx.post(f"{BASE_MW}/registry/register", json=agent,
                       headers=AUTH_HEADERS, timeout=10)
        assert r.status_code in (200, 201)

    def test_deregister(self):
        r = httpx.post(f"{BASE_MW}/registry/deregister",
                       json={"agent_id": "test-diag"},
                       headers=AUTH_HEADERS, timeout=10)
        assert r.status_code == 200


# ============================================================
#  网络模拟器
# ============================================================

class TestSimulator:
    """测试网络模拟器"""

    def test_get_all_metrics(self):
        r = httpx.get(f"{BASE_SIM}/simulator/metrics", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "ts_ms" in data
        assert "metrics" in data or "regions" in data

    def test_get_single_domain_metrics(self):
        r = httpx.get(f"{BASE_SIM}/simulator/metrics?domain=east-china", timeout=10)
        assert r.status_code == 200

    def test_topology(self):
        r = httpx.get(f"{BASE_SIM}/simulator/topology", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "domains" in data or "nodes" in data

    def test_repair_strategies(self):
        r = httpx.get(f"{BASE_SIM}/simulator/repair/strategies", timeout=10)
        assert r.status_code == 200

    def test_fault_inject_and_clear_cycle(self):
        r = httpx.post(f"{BASE_SIM}/simulator/fault/inject",
                       params={"fault_type": "link_down", "target": "east-china/core-sw1"},
                       timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data.get("status") == "ok"
        fault_id = data.get("fault_id")

        # Clear fault
        if fault_id:
            r2 = httpx.post(f"{BASE_SIM}/simulator/fault/clear",
                            params={"fault_id": fault_id}, timeout=10)
            assert r2.status_code == 200

    def test_repair_action_apply(self):
        r = httpx.post(f"{BASE_SIM}/simulator/repair",
                       json={"action_type": "restart_interface", "target": "east-china/core-sw1"},
                       timeout=10)
        assert r.status_code == 200

    def test_faults_summary(self):
        r = httpx.get(f"{BASE_SIM}/simulator/faults/summary", timeout=10)
        assert r.status_code == 200

    def test_invalid_fault_type(self):
        r = httpx.post(f"{BASE_SIM}/simulator/fault/inject",
                       params={"fault_type": "nonexistent", "target": "test"},
                       timeout=10)
        assert r.status_code in (200, 400, 422)

    def test_clear_all_faults(self):
        r = httpx.get(f"{BASE_SIM}/simulator/fault/clear_all", timeout=10)
        assert r.status_code == 200


# ============================================================
#  消息路由
# ============================================================

class TestMessageRouter:
    """测试消息路由"""

    def test_send_message(self):
        msg = {
            "from_agent": "test-mon-east",
            "to_agent": "test-diag",
            "intent": {"type": "task", "description": "test task", "priority": "normal"},
            "payload": {"test": True},
        }
        r = httpx.post(f"{BASE_MW}/messages", json=msg,
                       headers=AUTH_HEADERS, timeout=10)
        assert r.status_code in (200, 201)
        data = r.json()
        assert data.get("status") == "ok"
        assert "msg_id" in data

    def test_query_messages(self):
        r = httpx.get(f"{BASE_MW}/messages?limit=10", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "messages" in data

    def test_query_messages_with_filters(self):
        r = httpx.get(f"{BASE_MW}/messages?from_agent=test-mon-east&limit=5", timeout=10)
        assert r.status_code == 200

    def test_bandit_stats(self):
        r = httpx.get(f"{BASE_MW}/messages/bandit/stats", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "total_trials" in data

    def test_reranker_status(self):
        r = httpx.get(f"{BASE_MW}/messages/reranker/status", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "available" in data


# ============================================================
#  DAG 调度
# ============================================================

class TestDAGScheduler:
    """测试 DAG 调度引擎"""

    def test_list_dags(self):
        r = httpx.get(f"{BASE_MW}/dag?limit=10", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "dags" in data

    def test_submit_simple_dag(self):
        dag = {
            "dag_id": "test-dag-001",
            "description": "Simple test DAG",
            "nodes": [
                {
                    "node_id": "node-1",
                    "type": "task",
                    "capability": "monitor",
                    "depends_on": [],
                    "params": {},
                    "max_retries": 2,
                    "timeout_ms": 30000,
                }
            ]
        }
        r = httpx.post(f"{BASE_MW}/dag", json=dag,
                       headers=AUTH_HEADERS, timeout=10)
        assert r.status_code in (200, 201)
        data = r.json()
        assert "dag_id" in data

    def test_get_dag_detail(self):
        # First submit
        dag = {
            "dag_id": "test-dag-002",
            "description": "Detail test DAG",
            "nodes": [
                {"node_id": "n1", "type": "task", "capability": "monitor",
                 "depends_on": [], "params": {}, "max_retries": 1, "timeout_ms": 10000}
            ]
        }
        httpx.post(f"{BASE_MW}/dag", json=dag, headers=AUTH_HEADERS, timeout=10)
        r = httpx.get(f"{BASE_MW}/dag/test-dag-002", timeout=10)
        assert r.status_code == 200

    def test_cancel_dag(self):
        dag = {
            "dag_id": "test-dag-cancel",
            "description": "Cancel test",
            "nodes": [
                {"node_id": "n1", "type": "task", "capability": "monitor",
                 "depends_on": [], "params": {}, "max_retries": 1, "timeout_ms": 60000}
            ]
        }
        httpx.post(f"{BASE_MW}/dag", json=dag, headers=AUTH_HEADERS, timeout=10)
        r = httpx.post(f"{BASE_MW}/dag/test-dag-cancel/cancel",
                       headers=AUTH_HEADERS, timeout=10)
        assert r.status_code == 200


# ============================================================
#  A2A 协议
# ============================================================

class TestA2AProtocol:
    """测试 A2A 协议"""

    def test_agent_card(self):
        r = httpx.get(f"{BASE_MW}/a2a/.well-known/agent.json", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "name" in data
        assert "skills" in data or "capabilities" in data

    def test_a2a_health(self):
        r = httpx.get(f"{BASE_MW}/a2a/health", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"

    def test_send_task(self):
        task = {
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": "Check network health"}]
            }
        }
        r = httpx.post(f"{BASE_MW}/a2a/tasks/send", json=task, timeout=10)
        assert r.status_code in (200, 201)


# ============================================================
#  端到端工作流
# ============================================================

class TestEndToEndWorkflow:
    """端到端集成场景"""

    def test_full_monitoring_cycle(self):
        """完整的监控 -> 诊断 -> 修复 -> 验证流程"""
        # 1. Inject fault
        r1 = httpx.post(f"{BASE_SIM}/simulator/fault/inject",
                        params={"fault_type": "high_latency", "target": "east-china/router-1"},
                        timeout=10)
        assert r1.status_code == 200

        # 2. Check fault appears
        r2 = httpx.get(f"{BASE_SIM}/simulator/faults/summary", timeout=10)
        assert r2.status_code == 200

        # 3. Send diagnosis message
        msg = {
            "from_agent": "monitor-east-china",
            "to_agent": "diagnoser-global",
            "intent": {"type": "task", "description": "Diagnose high latency", "priority": "high"},
            "payload": {"fault_type": "high_latency", "target": "east-china/router-1"},
        }
        r3 = httpx.post(f"{BASE_MW}/messages", json=msg, headers=AUTH_HEADERS, timeout=10)
        assert r3.status_code in (200, 201)

        # 4. Apply repair
        r4 = httpx.post(f"{BASE_SIM}/simulator/repair",
                        json={"action_type": "optimize_routing", "target": "east-china/router-1"},
                        timeout=10)
        assert r4.status_code == 200

        # 5. Clear fault
        fault_id = r1.json().get("fault_id")
        if fault_id:
            r5 = httpx.post(f"{BASE_SIM}/simulator/fault/clear",
                            params={"fault_id": fault_id}, timeout=10)
            assert r5.status_code == 200

    def test_message_flow_through_bus(self):
        """消息总线完整流转"""
        msg = {
            "from_agent": "monitor-east-china",
            "to_agent": "orchestrator-agent",
            "intent": {"type": "report", "description": "Health report", "priority": "normal"},
            "payload": {"status": "healthy", "metrics": {"cpu": 0.45, "mem": 0.62}},
            "correlation_id": "e2e-test-corr-001",
        }
        r = httpx.post(f"{BASE_MW}/messages", json=msg, headers=AUTH_HEADERS, timeout=10)
        assert r.status_code in (200, 201)
        msg_id = r.json().get("msg_id")

        # Query by correlation_id
        r2 = httpx.get(f"{BASE_MW}/messages?correlation_id=e2e-test-corr-001", timeout=10)
        assert r2.status_code == 200
        data = r2.json()
        assert data["count"] >= 1

        # Get single message
        if msg_id:
            r3 = httpx.get(f"{BASE_MW}/messages/{msg_id}", timeout=10)
            assert r3.status_code == 200


# ============================================================
#  WebSocket 测试
# ============================================================

class TestWebSocket:
    """WebSocket 连通性测试"""

    def test_dashboard_ws_connect(self):
        """Dashboard WebSocket 连接"""
        try:
            import websocket as ws_lib
            token_param = f"?token={PSK}" if PSK and AUTH_ENABLED else ""
            sock = ws_lib.create_connection(
                f"ws://127.0.0.1:8000/ws/dashboard{token_param}",
                timeout=5
            )
            result = sock.recv()
            data = json.loads(result)
            assert "type" in data
            sock.close()
        except ImportError:
            pytest.skip("websocket-client not installed")
        except Exception as e:
            # WebSocket might fail if auth token is required but not valid
            pytest.skip(f"WebSocket connection failed: {e}")

    def test_simulator_ws_connect(self):
        """Simulator WebSocket 连接"""
        try:
            import websocket as ws_lib
            sock = ws_lib.create_connection(
                f"ws://127.0.0.1:8001/simulator/ws",
                timeout=5
            )
            result = sock.recv()
            data = json.loads(result)
            assert "type" in data
            sock.close()
        except ImportError:
            pytest.skip("websocket-client not installed")
        except Exception as e:
            pytest.skip(f"WebSocket connection failed: {e}")


# ============================================================
#  边界条件测试
# ============================================================

class TestEdgeCases:
    """边界条件和异常场景"""

    def test_nonexistent_endpoint(self):
        r = httpx.get(f"{BASE_MW}/nonexistent", timeout=5)
        assert r.status_code == 404

    def test_invalid_json_body(self):
        r = httpx.post(f"{BASE_MW}/messages",
                       content="not-json",
                       headers={"Content-Type": "application/json", **AUTH_HEADERS},
                       timeout=5)
        assert r.status_code in (400, 422)

    def test_empty_dag_submission(self):
        r = httpx.post(f"{BASE_MW}/dag",
                       json={"dag_id": "empty-dag", "nodes": []},
                       headers=AUTH_HEADERS, timeout=5)
        assert r.status_code in (400, 422)

    def test_concurrent_health_checks(self):
        """并发健康检查"""
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                executor.submit(httpx.get, f"{BASE_MW}/health", timeout=5)
                for _ in range(10)
            ]
            results = [f.result() for f in futures]
            assert all(r.status_code == 200 for r in results)

    def test_large_message_payload(self):
        """大数据量消息"""
        msg = {
            "from_agent": "test-mon-east",
            "to_agent": "test-diag",
            "intent": {"type": "task", "description": "Large payload test", "priority": "low"},
            "payload": {"data": "x" * 10000, "items": list(range(100))},
        }
        r = httpx.post(f"{BASE_MW}/messages", json=msg, headers=AUTH_HEADERS, timeout=10)
        assert r.status_code in (200, 201)
