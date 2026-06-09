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
    ["健康检查", "巡检", "health", "overview"],
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


@_register(
    "full_remediation_all",
    "全 4 域故障修复（监控 → 诊断 → 修复 → 验证 → 报告 × 4 域）",
    ["全域", "所有域", "全部域", "全局", "所有地区", "全部地区",
     "所有故障", "全部故障", "所有网络", "全部网络",
     "all domains", "all regions", "global fix", "global remediation"],
)
def template_full_remediation_all(params: dict) -> dict:
    """全 4 域完整闭环：每域独立 monitor → diagnose → repair → verify，最终汇总报告。"""
    domains = ["east-china", "north-china", "south-china", "west-china"]
    dag_id = params.get("dag_id", f"dag-remediate-all-{uuid.uuid4().hex[:8]}")
    nodes = []
    report_deps = []

    for domain in domains:
        prefix = domain.replace("-", "")
        mon_id = f"mon-{prefix}"
        diag_id = f"diag-{prefix}"
        repair_id = f"fix-{prefix}"
        verify_id = f"ver-{prefix}"

        nodes.extend([
            {
                "node_id": mon_id,
                "type": "monitor",
                "capability": "monitor",
                "domain": domain,
                "params": {"domain": domain},
            },
            {
                "node_id": diag_id,
                "type": "diagnose",
                "capability": "diagnose",
                "domain": domain,
                "depends_on": [mon_id],
            },
            {
                "node_id": repair_id,
                "type": "repair",
                "capability": "repair",
                "domain": domain,
                "depends_on": [diag_id],
            },
            {
                "node_id": verify_id,
                "type": "verify",
                "capability": "verify",
                "domain": domain,
                "depends_on": [repair_id],
            },
        ])
        report_deps.append(verify_id)

    nodes.append({
        "node_id": "report-all",
        "type": "report",
        "capability": "report",
        "depends_on": report_deps,
    })

    return {
        "dag_id": dag_id,
        "correlation_id": params.get("correlation_id", dag_id),
        "description": "全域故障自动诊断修复（4域并行 + 汇总报告）",
        "nodes": nodes,
    }


# ── 模板匹配 ─────────────────────────────────────────────

def match_template(user_input: str) -> tuple[str, dict, float]:
    """根据自然语言输入匹配最佳模板。

    返回 (template_name, template_meta, score)。

    两阶段匹配：
    1. 检测全域意图关键词 → 命中则直接选 full_remediation_all
    2. 否则按关键词命中率选择最佳模板，默认 full_remediation
    """
    user_lower = user_input.lower()

    # 阶段 1：全域意图检测（命中任意一个即认定为全域操作）
    global_keywords = [
        "全域", "所有域", "全部域", "全局", "所有地区", "全部地区",
        "所有故障", "全部故障", "所有网络", "全部网络",
        "all domains", "all regions", "global",
    ]
    if any(kw in user_lower for kw in global_keywords):
        return "full_remediation_all", TEMPLATES["full_remediation_all"], 1.0

    # 阶段 2：关键词命中率匹配
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
