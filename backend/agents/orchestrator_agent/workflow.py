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
from prompts import PROMPTS
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
    global_keywords = ["全域", "所有域", "全部域", "全局", "所有地区", "全部地区", "所有故障", "全部故障"]

    # 提取域名
    domain = "east-china"  # 默认
    if any(kw in user_input for kw in global_keywords):
        domain = "global"
    else:
        for alias, d in domain_aliases.items():
            if alias in user_input.lower():
                domain = d
                break
    
    # 如果有 LLM，使用 LLM 增强解析
    if llm_client and llm_client.available:
        prompt = PROMPTS.orchestrator_intent_workflow(user_input, domain)
        
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
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("LLM intent parse failed (will fallback to rules): %s", exc)
        except (ConnectionError, TimeoutError) as exc:
            logger.warning("LLM connection failed (will fallback to rules): %s", exc)
        except Exception:
            logger.warning("Unexpected LLM error, falling back to rules", exc_info=True)
    
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
    """模板匹配节点 — 复用 templates.match_template() 避免重复关键词逻辑"""
    from ioa_middleware.orchestrator.templates import match_template

    name, meta, score = match_template(state["user_input"])

    return {
        "template_name": name,
        "template_meta": meta,
        "messages": [f"[模板匹配] 选择: {name} (score={score:.2f})"],
    }

async def validate_params_node(state: OrchestratorState) -> dict:
    """参数验证节点 — 校验必要参数"""
    intent = state["intent"]
    errors = []

    # 检查必要字段
    if not intent.get("domain"):
        errors.append("缺少 domain 参数")

    # 域名有效性检查（global 用于全域模板，允许通过）
    valid_domains = ["east-china", "north-china", "south-china", "west-china", "global"]
    if intent.get("domain") not in valid_domains:
        errors.append(f"无效的 domain: {intent.get('domain')}")

    return {
        "errors": errors,
        "messages": [f"[参数验证] {'通过' if not errors else '错误: ' + '; '.join(errors)}"],
    }


async def generate_dag_node(state: OrchestratorState) -> dict:
    """DAG 生成节点 — 调用模板函数生成 DAG 定义"""
    import time
    from ioa_middleware.orchestrator.templates import TEMPLATES

    intent = state["intent"]
    template_name = state["template_name"]
    domain = intent.get("domain", "east-china")

    dag_id = f"dag-{template_name}-{int(time.time()*1000)}"

    # 优先使用模板函数生成（避免硬编码重复逻辑）
    template_meta = TEMPLATES.get(template_name)
    if template_meta:
        dag_definition = template_meta["fn"]({
            "domain": domain,
            "dag_id": dag_id,
            "correlation_id": dag_id,
        })
    else:
        # 未知模板：降级为单域全流程
        dag_definition = {
            "dag_id": dag_id,
            "description": f"未知模板 {template_name} — {domain}",
            "nodes": [
                {"node_id": "monitor-1", "type": "monitor", "capability": "monitor", "params": {"domain": domain}},
                {"node_id": "diagnose-1", "type": "diagnose", "capability": "diagnose", "depends_on": ["monitor-1"]},
                {"node_id": "repair-1", "type": "repair", "capability": "repair", "depends_on": ["diagnose-1"]},
                {"node_id": "verify-1", "type": "verify", "capability": "verify", "depends_on": ["repair-1"]},
                {"node_id": "report-1", "type": "report", "capability": "report", "depends_on": ["verify-1"]},
            ],
        }

    dag_definition["metadata"] = {
        "template": template_name,
        "intent": intent,
        "generated_by": "langgraph",
    }

    return {
        "dag_definition": dag_definition,
        "messages": [f"[DAG 生成] 模板={template_name}, 节点数={len(dag_definition.get('nodes', []))}"],
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
    
    # 添加节点（使用异步闭包传递依赖）
    async def _parse_intent(state):
        return await parse_intent_node(state, llm_client)

    async def _match_template(state):
        return await match_template_node(state, templates)

    workflow.add_node("parse_intent", _parse_intent)
    workflow.add_node("match_template", _match_template)
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
