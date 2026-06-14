"""Diagnoser LangGraph workflow — 用 StateGraph 建模诊断流程

节点: fetch_metrics → fetch_topology → rule_diagnose → llm_enhance → output

与 Orchestrator 共用 langgraph 库，提升框架覆盖率。
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

try:
    from langgraph.graph import StateGraph, END
    LANGRAPH_AVAILABLE = True
except ImportError:
    LANGRAPH_AVAILABLE = False

logger = logging.getLogger("diagnoser.workflow")


class DiagnoserState(TypedDict):
    """诊断工作流状态"""
    anomalies: list[dict]
    metrics: dict
    all_metrics: dict
    topology: dict
    diagnosis: dict
    llm_insight: str
    llm_available: bool
    error: str


def _make_fetch_metrics(tool_client):
    async def fetch_metrics(state: DiagnoserState) -> DiagnoserState:
        try:
            mr = await tool_client.call_tool("get_all_metrics", {})
            state["all_metrics"] = mr.get("metrics", {})
        except Exception as exc:
            logger.warning("fetch_metrics failed: %s", exc)
        return state
    return fetch_metrics


def _make_fetch_topology(tool_client):
    async def fetch_topology(state: DiagnoserState) -> DiagnoserState:
        try:
            tr = await tool_client.call_tool("get_topology", {})
            state["topology"] = tr
        except Exception as exc:
            logger.warning("fetch_topology failed: %s", exc)
        return state
    return fetch_topology


def _make_rule_diagnose(rule_fn):
    async def rule_diagnose(state: DiagnoserState) -> DiagnoserState:
        state["diagnosis"] = rule_fn(
            state.get("anomalies", []),
            state.get("metrics", {}),
            state.get("all_metrics", {}),
        )
        return state
    return rule_diagnose


def _make_llm_enhance(llm_enhance_fn):
    async def llm_enhance(state: DiagnoserState) -> DiagnoserState:
        if state.get("llm_available") and state.get("anomalies"):
            try:
                state["llm_insight"] = await llm_enhance_fn(
                    state.get("anomalies", []),
                    state.get("metrics", {}),
                    state.get("topology", {}),
                    state.get("diagnosis", {}),
                )
            except Exception as exc:
                logger.warning("llm_enhance failed: %s", exc)
        return state
    return llm_enhance


def build_diagnoser_graph(tool_client, rule_fn, llm_enhance_fn=None) -> StateGraph:
    """构建诊断工作流 StateGraph。

    Args:
        tool_client: 工具客户端（HttpToolClient / AutoToolClient）
        rule_fn: 规则诊断函数 signature: (anomalies, metrics, all_metrics) -> dict
        llm_enhance_fn: LLM 增强函数 (可选)
    """
    if not LANGRAPH_AVAILABLE:
        raise RuntimeError("langgraph not installed")

    workflow = StateGraph(DiagnoserState)

    # 添加节点
    workflow.add_node("fetch_metrics", _make_fetch_metrics(tool_client))
    workflow.add_node("fetch_topology", _make_fetch_topology(tool_client))
    workflow.add_node("rule_diagnose", _make_rule_diagnose(rule_fn))
    if llm_enhance_fn:
        workflow.add_node("llm_enhance", _make_llm_enhance(llm_enhance_fn))

    # 边：fetch_metrics → fetch_topology → rule_diagnose → (llm_enhance) → END
    workflow.set_entry_point("fetch_metrics")
    workflow.add_edge("fetch_metrics", "fetch_topology")
    workflow.add_edge("fetch_topology", "rule_diagnose")
    if llm_enhance_fn:
        workflow.add_edge("rule_diagnose", "llm_enhance")
        workflow.add_edge("llm_enhance", END)
    else:
        workflow.add_edge("rule_diagnose", END)

    return workflow.compile()


async def run_diagnoser_workflow(
    tool_client,
    rule_fn,
    llm_enhance_fn,
    anomalies: list[dict],
    metrics: dict,
) -> dict:
    """执行诊断工作流并返回结果。

    Returns:
        {"diagnosis": dict, "llm_insight": str, "error": str}
    """
    if not LANGRAPH_AVAILABLE:
        # 降级：直接调用规则引擎
        diagnosis = rule_fn(anomalies, metrics, {})
        return {"diagnosis": diagnosis, "llm_insight": "", "error": ""}

    try:
        graph = build_diagnoser_graph(tool_client, rule_fn, llm_enhance_fn)
        initial_state: DiagnoserState = {
            "anomalies": anomalies,
            "metrics": metrics,
            "all_metrics": {},
            "topology": {},
            "diagnosis": {},
            "llm_insight": "",
            "llm_available": llm_enhance_fn is not None,
            "error": "",
        }
        result = await graph.ainvoke(initial_state)
        return {
            "diagnosis": result.get("diagnosis", {}),
            "llm_insight": result.get("llm_insight", ""),
            "error": result.get("error", ""),
        }
    except Exception as exc:
        logger.exception("Diagnoser workflow failed, falling back to rule engine")
        diagnosis = rule_fn(anomalies, metrics, {})
        return {"diagnosis": diagnosis, "llm_insight": "", "error": str(exc)}
