"""DAG 模板库 — 预设故障处理流程

供 OrchestratorAgent 根据自然语言意图选择并填充参数。
每个模板是一个函数，接受参数 dict，返回 DagDefinition 所需的 dict。

模板列表：
- monitor_only       — 单域指标采集
- diagnose           — 监控 + 诊断
- full_remediation   — 监控 → 诊断 → 修复 → 报告（完整闭环）
- health_check       — 全 4 域监控 + 汇总报告
"""

import uuid

# ── 模板注册表 ────────────────────────────────────────────

TEMPLATES: dict[str, dict] = {}


def _register(name: str, description: str, keywords: list[str]):
    """装饰器：注册模板。"""
    def decorator(fn):
        TEMPLATES[name] = {
            "fn": fn,
            "description": description,
            "keywords": keywords,
        }
        return fn
    return decorator


# ═══════════════════════════════════════════════════════════
#  模板定义
# ═══════════════════════════════════════════════════════════

@_register(
    "monitor_only",
    "单域指标采集，不做后续处理",
    ["监控", "查看", "指标", "状态", "monitor", "check", "status"],
)
def template_monitor_only(params: dict) -> dict:
    """只采集一个域的指标。"""
    domain = params.get("domain", "east-china")
    dag_id = params.get("dag_id", f"dag-monitor-{uuid.uuid4().hex[:8]}")
    return {
        "dag_id": dag_id,
        "correlation_id": params.get("correlation_id", dag_id),
        "description": f"监控 {domain} 网络指标",
        "nodes": [
            {
                "node_id": "monitor-1",
                "type": "monitor",
                "capability": "monitor",
                "domain": domain,
                "params": {"domain": domain},
            },
        ],
    }


@_register(
    "diagnose",
    "监控 + 根因诊断",
    ["诊断", "分析", "原因", "根因", "diagnose", "analyze", "why"],
)
def template_diagnose(params: dict) -> dict:
    """监控 → 诊断。"""
    domain = params.get("domain", "east-china")
    dag_id = params.get("dag_id", f"dag-diagnose-{uuid.uuid4().hex[:8]}")
    return {
        "dag_id": dag_id,
        "correlation_id": params.get("correlation_id", dag_id),
        "description": f"诊断 {domain} 网络异常原因",
        "nodes": [
            {
                "node_id": "monitor-1",
                "type": "monitor",
                "capability": "monitor",
                "domain": domain,
                "params": {"domain": domain},
            },
            {
                "node_id": "diagnose-1",
                "type": "diagnose",
                "capability": "diagnose",
                "depends_on": ["monitor-1"],
            },
        ],
    }


@_register(
    "full_remediation",
    "监控 → 诊断 → 修复 → 验证 → 报告（完整闭环）",
    ["修复", "处理", "解决", "自动", "全流程", "故障", "repair", "fix", "remediate", "auto", "full"],
)
def template_full_remediation(params: dict) -> dict:
    """完整闭环：监控 → 诊断 → 修复 → 闭环验证 → 报告。"""
    domain = params.get("domain", "east-china")
    dag_id = params.get("dag_id", f"dag-remediate-{uuid.uuid4().hex[:8]}")
    return {
        "dag_id": dag_id,
        "correlation_id": params.get("correlation_id", dag_id),
        "description": f"{domain} 故障自动诊断修复（含闭环验证）",
        "nodes": [
            {
                "node_id": "monitor-1",
                "type": "monitor",
                "capability": "monitor",
                "domain": domain,
                "params": {"domain": domain},
            },
            {
                "node_id": "diagnose-1",
                "type": "diagnose",
                "capability": "diagnose",
                "depends_on": ["monitor-1"],
            },
            {
                "node_id": "repair-1",
                "type": "repair",
                "capability": "repair",
                "depends_on": ["diagnose-1"],
            },
            {
                "node_id": "verify-1",
                "type": "verify",
                "capability": "verify",
                "depends_on": ["repair-1"],
            },
            {
                "node_id": "report-1",
                "type": "report",
                "capability": "report",
                "depends_on": ["verify-1"],
            },
        ],
    }


@_register(
    "health_check",
    "全 4 域健康检查 + 汇总报告",
    ["健康检查", "全局", "所有", "整体", "巡检", "health", "all", "overview", "summary"],
)
def template_health_check(params: dict) -> dict:
    """全 4 域监控 → 汇总报告。"""
    domains = ["east-china", "north-china", "south-china", "west-china"]
    dag_id = params.get("dag_id", f"dag-health-{uuid.uuid4().hex[:8]}")
    nodes = []
    for i, domain in enumerate(domains):
        nodes.append({
            "node_id": f"monitor-{domain}",
            "type": "monitor",
            "capability": "monitor",
            "domain": domain,
            "params": {"domain": domain},
        })
    nodes.append({
        "node_id": "report-1",
        "type": "report",
        "capability": "report",
        "depends_on": [f"monitor-{d}" for d in domains],
    })
    return {
        "dag_id": dag_id,
        "correlation_id": params.get("correlation_id", dag_id),
        "description": "全域网健康检查",
        "nodes": nodes,
    }


# ── 模板匹配 ─────────────────────────────────────────────

def match_template(user_input: str) -> tuple[str, dict, float]:
    """根据自然语言输入匹配最佳模板。

    返回 (template_name, template_meta, score)。
    score 基于关键词命中率。

    无匹配时返回 ("full_remediation", ..., 0.0) 作为默认。
    """
    user_lower = user_input.lower()
    best_name = "full_remediation"
    best_score = 0.0

    for name, meta in TEMPLATES.items():
        keywords = meta["keywords"]
        hits = sum(1 for kw in keywords if kw.lower() in user_lower)
        if keywords:
            score = hits / len(keywords)
            if score > best_score:
                best_score = score
                best_name = name

    return best_name, TEMPLATES[best_name], best_score
