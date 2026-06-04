"""LangGraph 工作流 — Orchestrator Agent 的意图解析与 DAG 编排

使用 LangGraph 实现有状态的工作流：
1. 意图解析节点 — LLM 提取结构化参数
2. 模板匹配节点 — 语义匹配 DAG 模板
3. 参数验证节点 — 校验参数完整性
4. DAG 生成节点 — 生成 DAG 定义

支持条件路由和错误恢复。
LangGraph 为可选依赖，未安装时自动降级为顺序执行模式。
"""

import json
import logging
from typing import TypedDict, Annotated, Sequence
from operator import add as add_messages

logger = logging.getLogger("workflow")

# 尝试导入 LangGraph（可选依赖）
try:
    from langgraph.graph import StateGraph, END
    HAS_LANGGRAPH = True
    logger.info("LangGraph available, using graph-based workflow")
except ImportError:
    HAS_LANGGRAPH = False
    logger.info("LangGraph not available, using sequential workflow fallback")


# ── 状态定义 ─────────────────────────────────────────────

class OrchestratorState(TypedDict):
    """工作流状态"""
    user_input: str
    messages: Annotated[Sequence[str], add_messages]  # 消息历史
    intent: dict  # 解析后的意图
    template_name: str  # 匹配的模板名
    template_meta: dict  # 模板元数据
    dag_params: dict  # DAG 参数
    dag_definition: dict  # 最终 DAG 定义
    errors: list[str]  # 错误列表
    confidence: float  # 置信度


# ── 节点函数 ─────────────────────────────────────────────

async def parse_intent_node(state: OrchestratorState, llm_client=None) -> dict:
    """意图解析节点 — 使用 LLM 提取结构化参数"""
    user_input = state["user_input"]
    
    # 域名映射
    domain_aliases = {
        "华东": "east-china", "东部": "east-china", "east": "east-china",
        "华北": "north-china", "北部": "north-china", "north": "north-china",
        "华南": "south-china", "南部": "south-china", "south": "south-china",
        "西南": "west-china", "西部": "west-china", "west": "west-china",
    }
    
    # 提取域名
    domain = "east-china"  # 默认
    for alias, d in domain_aliases.items():
        if alias in user_input.lower():
            domain = d
            break
    
    # 如果有 LLM，使用 LLM 增强解析
    if llm_client and llm_client.available:
        prompt = f"""你是网络运维专家。从用户输入中提取以下信息，返回 JSON。

## 用户输入
{user_input}

## 要求
返回严格 JSON：
{{"domain": "<域名>", "fault_type": "<故障类型>", "urgency": "<low|medium|high>", "description": "<简要描述>"}}

已初步提取 domain={domain}，请确认或修正。"""
        
        try:
            response = await llm_client.ask(prompt)
            if response:
                # 提取 JSON
                if "```" in response:
                    response = response.split("```")[1]
                    if response.startswith("json"):
                        response = response[4:]
                parsed = json.loads(response.strip())
                parsed.setdefault("domain", domain)
                return {
                    "intent": parsed,
                    "messages": [f"[意图解析] LLM 提取: {json.dumps(parsed, ensure_ascii=False)}"],
                    "confidence": 0.85,
                }
        except Exception:
            pass
    
    # 降级为规则解析
    intent = {
        "domain": domain,
        "fault_type": "unknown",
        "urgency": "high",
        "description": user_input,
    }
    
    # 关键词提取故障类型
    if "拥塞" in user_input or "congestion" in user_input:
        intent["fault_type"] = "link_congestion"
    elif "中断" in user_input or "outage" in user_input:
        intent["fault_type"] = "link_outage"
    elif "cpu" in user_input.lower() or "过载" in user_input:
        intent["fault_type"] = "cpu_overload"
    elif "ddos" in user_input.lower() or "攻击" in user_input:
        intent["fault_type"] = "ddos"
    elif "配置" in user_input or "misconfig" in user_input:
        intent["fault_type"] = "misconfig"
    
    return {
        "intent": intent,
        "messages": [f"[意图解析] 规则提取: {json.dumps(intent, ensure_ascii=False)}"],
        "confidence": 0.70,
    }


async def match_template_node(state: OrchestratorState, templates: dict = None) -> dict:
    """模板匹配节点 — 根据意图选择 DAG 模板"""
    intent = state["intent"]
    user_input = state["user_input"]
    
    if not templates:
        # 默认模板
        templates = {
            "full_remediation": {
                "keywords": ["修复", "诊断", "全流程", "remediation", "fix"],
                "description": "全流程故障修复",
            },
            "monitor_only": {
                "keywords": ["监控", "检测", "monitor"],
                "description": "仅监控",
            },
            "diagnose_only": {
                "keywords": ["诊断", "分析", "diagnose"],
                "description": "仅诊断",
            },
        }
    
    # 关键词匹配
    best_match = "full_remediation"  # 默认
    best_score = 0.0
    
    for name, meta in templates.items():
        keywords = meta.get("keywords", [])
        score = sum(1 for kw in keywords if kw in user_input.lower())
        normalized_score = score / max(len(keywords), 1)
        if normalized_score > best_score:
            best_score = normalized_score
            best_match = name
    
    return {
        "template_name": best_match,
        "template_meta": templates.get(best_match, {}),
        "messages": [f"[模板匹配] 选择: {best_match} (score={best_score:.2f})"],
    }


async def validate_params_node(state: OrchestratorState) -> dict:
    """参数验证节点 — 校验必要参数"""
    intent = state["intent"]
    errors = []
    
    # 检查必要字段
    if not intent.get("domain"):
        errors.append("缺少 domain 参数")
    
    # 域名有效性检查
    valid_domains = ["east-china", "north-china", "south-china", "west-china"]
    if intent.get("domain") not in valid_domains:
        errors.append(f"无效的 domain: {intent.get('domain')}")
    
    return {
        "errors": errors,
        "messages": [f"[参数验证] {'通过' if not errors else '错误: ' + '; '.join(errors)}"],
    }


async def generate_dag_node(state: OrchestratorState) -> dict:
    """DAG 生成节点 — 生成 DAG 定义"""
    import uuid
    import time
    
    intent = state["intent"]
    template_name = state["template_name"]
    domain = intent.get("domain", "east-china")
    
    # 生成 DAG ID
    dag_id = f"dag-{template_name}-{int(time.time()*1000)}"
    
    # 根据模板生成节点
    nodes = []
    if template_name == "full_remediation":
        nodes = [
            {"node_id": f"monitor-{dag_id}", "node_type": "monitor", "agent_capability": "monitoring"},
            {"node_id": f"diagnose-{dag_id}", "node_type": "diagnose", "agent_capability": "diagnose", "depends_on": [f"monitor-{dag_id}"]},
            {"node_id": f"repair-{dag_id}", "node_type": "repair", "agent_capability": "repair", "depends_on": [f"diagnose-{dag_id}"]},
            {"node_id": f"verify-{dag_id}", "node_type": "verify", "agent_capability": "verify", "depends_on": [f"repair-{dag_id}"]},
            {"node_id": f"report-{dag_id}", "node_type": "report", "agent_capability": "report", "depends_on": [f"verify-{dag_id}"]},
        ]
    elif template_name == "monitor_only":
        nodes = [
            {"node_id": f"monitor-{dag_id}", "node_type": "monitor", "agent_capability": "monitoring"},
        ]
    elif template_name == "diagnose_only":
        nodes = [
            {"node_id": f"monitor-{dag_id}", "node_type": "monitor", "agent_capability": "monitoring"},
            {"node_id": f"diagnose-{dag_id}", "node_type": "diagnose", "agent_capability": "diagnose", "depends_on": [f"monitor-{dag_id}"]},
        ]
    
    dag_definition = {
        "dag_id": dag_id,
        "description": f"LangGraph 生成的 {template_name} DAG",
        "domain": domain,
        "nodes": nodes,
        "metadata": {
            "template": template_name,
            "intent": intent,
            "generated_by": "langgraph",
        },
    }
    
    return {
        "dag_definition": dag_definition,
        "messages": [f"[DAG 生成] 生成 DAG: {dag_id}, 包含 {len(nodes)} 个节点"],
    }


# ── 条件路由 ─────────────────────────────────────────────

def should_continue(state: OrchestratorState) -> str:
    """条件路由 — 决定是否继续执行"""
    errors = state.get("errors", [])
    if errors:
        return "error"
    return "continue"


# ── 工作流构建 ────────────────────────────────────────────

def create_orchestrator_workflow(llm_client=None, templates: dict = None):
    """创建 Orchestrator LangGraph 工作流
    
    Args:
        llm_client: LLM 客户端实例
        templates: DAG 模板字典
    
    Returns:
        编译好的工作流图，或 None（如果 LangGraph 不可用）
    """
    if not HAS_LANGGRAPH:
        return None
        
    # 创建状态图
    workflow = StateGraph(OrchestratorState)
    
    # 添加节点（使用闭包传递依赖）
    workflow.add_node("parse_intent", lambda state: parse_intent_node(state, llm_client))
    workflow.add_node("match_template", lambda state: match_template_node(state, templates))
    workflow.add_node("validate_params", validate_params_node)
    workflow.add_node("generate_dag", generate_dag_node)
    
    # 定义边
    workflow.set_entry_point("parse_intent")
    workflow.add_edge("parse_intent", "match_template")
    workflow.add_edge("match_template", "validate_params")
    
    # 条件路由：验证失败则结束，成功则继续
    workflow.add_conditional_edges(
        "validate_params",
        should_continue,
        {
            "continue": "generate_dag",
            "error": END,
        }
    )
    workflow.add_edge("generate_dag", END)
    
    # 编译工作流
    return workflow.compile()


async def _run_sequential_workflow(
    user_input: str,
    llm_client=None,
    templates: dict = None,
) -> dict:
    """顺序执行工作流（LangGraph 不可用时的降级方案）"""
    # 初始状态
    state = {
        "user_input": user_input,
        "messages": [],
        "intent": {},
        "template_name": "",
        "template_meta": {},
        "dag_params": {},
        "dag_definition": {},
        "errors": [],
        "confidence": 0.0,
    }
    
    # 1. 意图解析
    result = await parse_intent_node(state, llm_client)
    state.update(result)
    state["messages"].extend(result.get("messages", []))
    
    # 2. 模板匹配
    result = await match_template_node(state, templates)
    state.update(result)
    state["messages"].extend(result.get("messages", []))
    
    # 3. 参数验证
    result = await validate_params_node(state)
    state.update(result)
    state["messages"].extend(result.get("messages", []))
    
    # 4. 如果有错误，提前返回
    if state.get("errors"):
        return {
            "dag_definition": {},
            "template_name": state.get("template_name", ""),
            "intent": state.get("intent", {}),
            "confidence": state.get("confidence", 0.0),
            "errors": state.get("errors", []),
            "workflow_log": state.get("messages", []),
        }
    
    # 5. DAG 生成
    result = await generate_dag_node(state)
    state.update(result)
    state["messages"].extend(result.get("messages", []))
    
    return {
        "dag_definition": state.get("dag_definition", {}),
        "template_name": state.get("template_name", ""),
        "intent": state.get("intent", {}),
        "confidence": state.get("confidence", 0.0),
        "errors": state.get("errors", []),
        "workflow_log": state.get("messages", []),
    }


async def run_orchestrator_workflow(
    user_input: str,
    llm_client=None,
    templates: dict = None,
) -> dict:
    """执行 Orchestrator 工作流
    
    Args:
        user_input: 用户自然语言输入
        llm_client: LLM 客户端实例
        templates: DAG 模板字典
    
    Returns:
        工作流执行结果
    """
    # 如果 LangGraph 不可用，使用顺序执行
    if not HAS_LANGGRAPH:
        return await _run_sequential_workflow(user_input, llm_client, templates)
    
    # 创建工作流
    workflow = create_orchestrator_workflow(llm_client, templates)
    
    # 初始状态
    initial_state = {
        "user_input": user_input,
        "messages": [f"[开始] 用户输入: {user_input}"],
        "intent": {},
        "template_name": "",
        "template_meta": {},
        "dag_params": {},
        "dag_definition": {},
        "errors": [],
        "confidence": 0.0,
    }
    
    # 执行工作流
    final_state = await workflow.ainvoke(initial_state)
    
    return {
        "dag_definition": final_state.get("dag_definition", {}),
        "template_name": final_state.get("template_name", ""),
        "intent": final_state.get("intent", {}),
        "confidence": final_state.get("confidence", 0.0),
        "errors": final_state.get("errors", []),
        "workflow_log": final_state.get("messages", []),
    }
