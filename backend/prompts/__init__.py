"""Prompt Registry — 统一管理所有 LLM Prompt，支持版本号和 A/B 测试。

用法:
    from prompts import PROMPTS
    prompt = PROMPTS.diagnoser_root_cause(anomalies, metrics, topology, rule_diag)

版本规范: MAJOR.MINOR — MAJOR 变更需评估输出兼容性。
"""

from __future__ import annotations
from dataclasses import dataclass, field
import json


# ── Prompt 模板注册表 ────────────────────────────────────

@dataclass(frozen=True)
class Prompt:
    """单个 Prompt 模板，带版本号和描述。"""
    name: str
    version: str
    description: str
    template: str


class PromptRegistry:
    """命名空间式 Prompt 访问器。提供类型安全的生成函数。"""

    _VERSION = "1.0"

    # ══════════════════════════════════════════════════════
    #  Diagnoser — 根因分析
    # ══════════════════════════════════════════════════════

    def diagnoser_root_cause(
        self, anomalies: list, metrics: dict, topology: dict, rule_diag: dict,
    ) -> str:
        """异常根因推理 Prompt。"""
        return f"""你是网络运维专家。请分析以下异常并给出根因判断。

## 异常指标
{json.dumps(anomalies, ensure_ascii=False, indent=2)}

## 当前域指标
{json.dumps(metrics, ensure_ascii=False, indent=2)}

## 拓扑概要
{json.dumps(topology, ensure_ascii=False, indent=2)[:800]}

## 规则引擎初步判断
{json.dumps(rule_diag, ensure_ascii=False, indent=2)}

请用 2-3 句话给出根因分析和建议。"""

    # ══════════════════════════════════════════════════════
    #  Orchestrator — 意图解析
    # ══════════════════════════════════════════════════════

    def orchestrator_intent_rule(
        self, template_list: str, domains: list, user_input: str, domain: str,
    ) -> str:
        """编排器：规则模式下从用户输入提取意图。（agent.py 用）"""
        return f"""你是网络运维编排专家。从用户输入中提取以下信息，返回 JSON。

## 可用模板
{template_list}

## 可用域
{json.dumps(domains)}

## 用户输入
{user_input}

## 要求
返回严格 JSON，不要包含其他内容：
{{"domain": "<匹配的域>", "fault_type": "<故障类型或unknown>", "urgency": "<low|medium|high>"}}

已初步提取 domain={domain}，请确认或修正。"""

    def orchestrator_intent_workflow(
        self, user_input: str, domain: str,
    ) -> str:
        """编排器：LangGraph 工作流中从用户输入提取意图。（workflow.py 用）"""
        return f"""你是网络运维专家。从用户输入中提取以下信息，返回 JSON。

## 用户输入
{user_input}

## 要求
返回严格 JSON：
{{"domain": "<域名>", "fault_type": "<故障类型>", "urgency": "<low|medium|high>", "description": "<简要描述>"}}

已初步提取 domain={domain}，请确认或修正。"""

    # ══════════════════════════════════════════════════════
    #  Reporter — 报告生成
    # ══════════════════════════════════════════════════════

    def reporter_summary(
        self, fault_type: str, diagnosis_desc: str, repair_success: bool,
        improvements: dict,
    ) -> str:
        """报告 Agent：故障处理总结。"""
        return f"""你是网络运维报告专家。请根据以下数据生成一份简短的运维总结报告。

## 故障类型
{fault_type}

## 诊断描述
{diagnosis_desc}

## 修复结果
{'成功' if repair_success else '失败'}

## 指标改善
{json.dumps(improvements, ensure_ascii=False, indent=2)[:600]}

请用 3-5 句话总结本次故障处理过程、结果和建议。"""

    # ══════════════════════════════════════════════════════
    #  Router — 语义路由
    # ══════════════════════════════════════════════════════

    def semantic_router(self, task_desc: str, candidates_text: str) -> str:
        """WeightedRouter LLM 语义匹配。"""
        return f"""你是一个智能体路由评估专家。判断以下网络运维任务与各候选Agent能力的匹配程度。

任务描述: "{task_desc}"

候选Agent列表：
{candidates_text}

请分析每个Agent与任务的匹配程度，按以下标准评分（0-1）：
- 0.9-1.0: 完全匹配，该Agent的功能就是处理这类任务
- 0.5-0.8: 部分匹配，Agent能胜任但不是最优选择
- 0.1-0.4: 弱匹配，能力与需求有一定关联
- 0.0:    完全不匹配

只返回JSON格式：{{"scores": {{"agent_id_1": 0.0, "agent_id_2": 0.0}}}}
不要任何额外说明。"""


# ── 全局单例 ──────────────────────────────────────────────

PROMPTS = PromptRegistry()

__all__ = ["PROMPTS", "PromptRegistry"]
