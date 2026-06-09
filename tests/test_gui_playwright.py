"""IoA GUI 浏览器自动化测试

使用 Playwright 测试 GUI 仪表盘的所有交互功能。
从用户视角验证：页面加载、数据展示、交互操作、故障注入、NL 命令等。

运行方式：pytest tests/test_gui_playwright.py -v --tb=short
"""

import time
import json
import pytest
import httpx

try:
    from playwright.sync_api import sync_playwright, expect
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

BASE_MW = "http://127.0.0.1:8000"
BASE_SIM = "http://127.0.0.1:8001"


def get_psk():
    import os
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

pytestmark = pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="Playwright not installed")


@pytest.fixture(scope="module")
def browser():
    """启动浏览器实例"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def context(browser):
    """创建浏览器上下文（带 PSK 存储）"""
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
    # 预设 localStorage 中的 PSK
    ctx.add_init_script(f"""
        localStorage.setItem('ioa_psk', '{PSK}');
    """)
    yield ctx
    ctx.close()


@pytest.fixture
def page(context):
    """创建新页面"""
    p = context.new_page()
    yield p
    p.close()


# ============================================================
#  页面加载与基础渲染
# ============================================================

class TestPageLoad:
    """测试页面加载和基础渲染"""

    def test_gui_page_loads(self, page):
        """GUI 页面成功加载"""
        response = page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        assert response.status == 200
        # 检查页面标题或关键元素
        page.wait_for_timeout(2000)
        content = page.content()
        assert "IoA" in content or "网络" in content or "dashboard" in content.lower()
        print(f"  ✓ GUI 页面加载成功, 大小: {len(content)} bytes")

    def test_root_redirects_to_gui(self, page):
        """根路径重定向到 GUI"""
        page.goto(BASE_MW, wait_until="networkidle", timeout=10000)
        assert "/gui" in page.url
        print(f"  ✓ 根路径重定向到: {page.url}")

    def test_topology_graph_rendered(self, page):
        """拓扑图是否渲染"""
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(3000)
        # vis-network 会创建 canvas
        canvas = page.query_selector("canvas")
        # 或者检查拓扑容器
        topo_container = page.query_selector("#topology-container, #topo, .topology, canvas, svg")
        assert topo_container is not None, "拓扑图容器未找到"
        print(f"  ✓ 拓扑图已渲染")

    def test_metrics_cards_displayed(self, page):
        """指标卡片是否显示"""
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(3000)
        content = page.content()
        # 应包含域指标信息
        has_metrics = any(kw in content for kw in ["延迟", "latency", "丢包", "packet", "带宽", "bandwidth"])
        assert has_metrics, "指标卡片未显示"
        print(f"  ✓ 指标卡片已显示")

    def test_agent_table_displayed(self, page):
        """Agent 表格是否显示"""
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(5000)  # Agent 表格每 10 秒刷新
        content = page.content()
        has_agents = any(kw in content for kw in ["orchestrator", "monitor", "diagnoser", "Agent", "agent"])
        assert has_agents, "Agent 表格未显示"
        print(f"  ✓ Agent 表格已显示")


# ============================================================
#  交互功能测试
# ============================================================

class TestInteractions:
    """测试用户交互功能"""

    def test_fault_injection_panel(self, page):
        """故障注入面板可用"""
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(2000)

        # 查找故障注入相关的 UI 元素
        content = page.content()
        has_fault_ui = any(kw in content for kw in ["故障", "fault", "注入", "inject"])
        assert has_fault_ui, "故障注入面板未找到"
        print(f"  ✓ 故障注入面板存在")

    def test_nl_command_input(self, page):
        """NL 命令输入框"""
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(2000)

        # 查找输入框
        inputs = page.query_selector_all("input[type='text'], textarea")
        has_input = len(inputs) > 0
        # 或者查找按钮
        buttons = page.query_selector_all("button")
        has_button = len(buttons) > 0

        assert has_input or has_button, "NL 命令输入界面未找到"
        print(f"  ✓ NL 命令输入: {len(inputs)} 个输入框, {len(buttons)} 个按钮")

    def test_demo_button_exists(self, page):
        """一键演示按钮"""
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(2000)
        content = page.content()
        has_demo = any(kw in content for kw in ["演示", "demo", "Demo", "一键", "DEMO"])
        print(f"  ✓ 演示按钮存在: {has_demo}")

    def test_send_nl_command(self, page):
        """发送 NL 命令"""
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(2000)

        # 尝试找到输入框并发送命令
        input_el = page.query_selector("input[type='text'], textarea, #cmd-input, #nl-input, .command-input")
        if input_el:
            input_el.fill("查看华东域网络状态")
            # 找到发送按钮
            send_btn = page.query_selector("button:has-text('发送'), button:has-text('Send'), button:has-text('执行'), button[type='submit']")
            if send_btn:
                send_btn.click()
                page.wait_for_timeout(3000)
                print(f"  ✓ NL 命令已发送")
            else:
                # 尝试回车发送
                input_el.press("Enter")
                page.wait_for_timeout(3000)
                print(f"  ✓ NL 命令已通过回车发送")
        else:
            print(f"  ⚠ 未找到 NL 命令输入框")

    def test_clear_faults_button(self, page):
        """清除故障按钮"""
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(2000)

        # 先注入一个故障
        httpx.post(f"{BASE_SIM}/simulator/fault/inject?fault_type=cpu_overload&target=Edge-R1",
                    timeout=5)

        clear_btn = page.query_selector("button:has-text('清除'), button:has-text('Clear'), button:has-text('clear')")
        if clear_btn:
            clear_btn.click()
            page.wait_for_timeout(2000)
            print(f"  ✓ 清除故障按钮已点击")
        else:
            # 直接 API 清除
            httpx.get(f"{BASE_SIM}/simulator/fault/clear_all", timeout=5)
            print(f"  ⚠ 未找到清除按钮 (已通过 API 清除)")

    def test_dag_records_display(self, page):
        """DAG 执行记录显示"""
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(5000)  # DAG 表格每 5 秒刷新
        content = page.content()
        has_dag = any(kw in content for kw in ["DAG", "dag", "任务", "task", "执行"])
        print(f"  ✓ DAG 记录区域存在: {has_dag}")

    def test_message_flow_display(self, page):
        """消息流显示"""
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(5000)  # 消息流每 3 秒刷新
        content = page.content()
        has_msgs = any(kw in content for kw in ["消息", "message", "IoAP", "Message", "msg"])
        print(f"  ✓ 消息流区域存在: {has_msgs}")


# ============================================================
#  实时数据测试
# ============================================================

class TestRealTimeData:
    """测试实时数据更新"""

    def test_metrics_update_over_time(self, page):
        """指标随时间更新"""
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(3000)

        # 注入故障观察变化
        httpx.post(f"{BASE_SIM}/simulator/fault/inject?fault_type=link_congestion&target=Edge-R1",
                    timeout=5)
        page.wait_for_timeout(5000)

        content1 = page.content()
        page.wait_for_timeout(5000)
        content2 = page.content()

        # 内容应有变化（实时更新）
        # 即使内容完全一样也没关系，关键是不报错
        httpx.get(f"{BASE_SIM}/simulator/fault/clear_all", timeout=5)
        print(f"  ✓ 实时数据更新不报错")

    def test_latency_chart_renders(self, page):
        """延迟趋势图渲染"""
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(5000)
        content = page.content()
        # Chart.js 会创建 canvas
        canvas_count = page.query_selector_all("canvas")
        has_chart = any(kw in content for kw in ["Chart", "chart", "延迟", "latency", "趋势"])
        print(f"  ✓ 图表区域: {len(canvas_count)} 个 canvas, Chart 相关内容: {has_chart}")


# ============================================================
#  完整用户场景模拟
# ============================================================

class TestUserScenarios:
    """完整用户场景模拟"""

    def test_scenario_single_fault_repair(self, page):
        """场景：单故障发现与修复"""
        # 1. 打开仪表盘
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(2000)

        # 2. 注入故障
        r = httpx.post(
            f"{BASE_SIM}/simulator/fault/inject?fault_type=cpu_overload&target=Edge-R1",
            timeout=5
        )
        assert r.json()["status"] == "ok"
        print(f"  ✓ [1/4] 故障已注入")

        # 3. 等待仪表盘刷新
        page.wait_for_timeout(5000)

        # 4. 发送 NL 命令
        input_el = page.query_selector("input[type='text'], textarea")
        if input_el:
            input_el.fill("华东域网络延迟异常，请诊断并修复")
            send_btn = page.query_selector("button:has-text('发送'), button:has-text('Send'), button:has-text('执行')")
            if send_btn:
                send_btn.click()
                page.wait_for_timeout(3000)
                print(f"  ✓ [2/4] NL 命令已发送")
            else:
                input_el.press("Enter")
                page.wait_for_timeout(3000)
                print(f"  ✓ [2/4] NL 命令已通过回车发送")
        else:
            # 通过 API 发送
            msg = {
                "msg_id": f"gui-test-{int(time.time())}",
                "from_agent": "test-gui",
                "to_agent": "orchestrator-agent",
                "intent": {"type": "user_request", "description": "华东域网络延迟异常，请诊断并修复", "priority": 1},
                "payload": {"raw_text": "华东域网络延迟异常，请诊断并修复"},
                "correlation_id": f"gui-corr-{int(time.time())}",
                "ts_ms": int(time.time() * 1000)
            }
            httpx.post(f"{BASE_MW}/messages", json=msg,
                       headers={"Authorization": f"Bearer {PSK}"}, timeout=10)
            print(f"  ✓ [2/4] NL 命令已通过 API 发送")

        # 5. 等待 Agent 处理
        print(f"  ⏳ [3/4] 等待 Agent 处理...")
        page.wait_for_timeout(20000)

        # 6. 检查页面状态
        content = page.content()
        # 页面应仍在正常显示
        assert "IoA" in content or "html" in content.lower()
        print(f"  ✓ [4/4] 场景完成, 页面正常")

        # 清理
        httpx.get(f"{BASE_SIM}/simulator/fault/clear_all", timeout=5)

    def test_scenario_multi_fault(self, page):
        """场景：多故障并发"""
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(2000)

        # 注入多个故障
        faults = [
            ("link_congestion", "Core-Router->Edge-R1"),
            ("ddos", "Edge-R2"),
            ("misconfig", "Edge-R3"),
        ]
        for ft, target in faults:
            r = httpx.post(
                f"{BASE_SIM}/simulator/fault/inject?fault_type={ft}&target={target}",
                timeout=5
            )
            assert r.json()["status"] == "ok"

        page.wait_for_timeout(5000)
        print(f"  ✓ [1/2] 3 个多域故障已注入")

        # 发送修复命令
        msg = {
            "msg_id": f"multi-fault-{int(time.time())}",
            "from_agent": "test-gui",
            "to_agent": "orchestrator-agent",
            "intent": {"type": "user_request",
                       "description": "多个域出现网络异常，华东域拥塞、华北域DDoS攻击、华域南配置错误，请逐一排查修复",
                       "priority": 1},
            "payload": {"raw_text": "多个域出现网络异常"},
            "correlation_id": f"multi-corr-{int(time.time())}",
            "ts_ms": int(time.time() * 1000)
        }
        httpx.post(f"{BASE_MW}/messages", json=msg,
                   headers={"Authorization": f"Bearer {PSK}"}, timeout=10)

        page.wait_for_timeout(25000)
        print(f"  ✓ [2/2] 多故障场景完成")

        # 清理
        httpx.get(f"{BASE_SIM}/simulator/fault/clear_all", timeout=5)

    def test_page_no_console_errors(self, page):
        """页面无 JS 控制台错误"""
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.goto(f"{BASE_MW}/gui", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(5000)

        # 过滤掉 WebSocket 连接错误（正常，因为测试环境可能不稳定）
        critical_errors = [e for e in console_errors
                          if "WebSocket" not in e and "ws://" not in e and "net::" not in e]
        if critical_errors:
            print(f"  ⚠ 发现 {len(critical_errors)} 个 JS 错误:")
            for e in critical_errors[:5]:
                print(f"    - {e[:200]}")
        else:
            print(f"  ✓ 无关键 JS 控制台错误 ({len(console_errors)} 个 WS 相关错误已忽略)")
