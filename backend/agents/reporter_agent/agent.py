"""Reporter Agent — 全链路报告生成

收集 Monitor → Diagnoser → Repairer 的完整执行链数据，
生成结构化总结报告。

"""

import json
import time
from typing import Any
from agents.base_agent import BaseAgent
from agents.tool_client import HttpToolClient, TOOL_GET_ALL_METRICS
from agents.llm_client import get_llm_client
from ioa_middleware.bus import MessageBus


class ReporterAgent(BaseAgent):
    """报告生成 Agent — 汇总全链路数据 → 生成报告。"""

    def __init__(self, bus: MessageBus, config: dict | None = None):
        super().__init__(
            agent_id="reporter-global",
            domain="global",
            capability="report",
            bus=bus,
            config=config,
        )
        self.tool_client = HttpToolClient()
        self._llm = get_llm_client(config)

    # ── 消息处理 ──────────────────────────────────────

    async def handle_message(self, topic: str, message: dict[str, Any]) -> dict[str, Any]:
        """处理 task 消息：收集上下文 → 生成报告 → 返回。"""
        intent = message.get("intent", {})
        if intent.get("type") != "task":
            return {}

        payload = message.get("payload", {})
        dag_id = payload.get("dag_id", "")
        node_id = payload.get("node_id", "")
        correlation_id = message.get("correlation_id", "")
        params = payload.get("params", {})

        print(f"[{self.agent_id}] Generating report (dag={dag_id}, node={node_id})")

        try:
            # 1. 收集最终指标
            final_metrics = {}
            try:
                mr = await self.tool_client.call_tool(TOOL_GET_ALL_METRICS, {})
                final_metrics = mr.get("metrics", {})
            except Exception:
                pass

            # 2. 生成结构化报告（含规则中文叙述）
            report = self._build_report(params, final_metrics, dag_id)

            # 3. LLM 增强叙述（覆盖规则版本）
            if self._llm.available:
                llm_narrative = await self._llm_enhance(report)
                if llm_narrative:
                    report["narrative"] = llm_narrative

            result = {
                "success": True,
                "output": report,
            }
        except Exception as e:
            result = {
                "success": False,
                "error": str(e),
            }

        # Return result via the reply mechanism (set by _on_message)
        return result

    # ── 报告生成 ──────────────────────────────────────

    def _build_report(self, params: dict, final_metrics: dict, dag_id: str) -> dict:
        """生成结构化总结报告。"""
        monitor_output = params.get("monitor", {})
        diagnose_output = params.get("diagnose", {})
        repair_output = params.get("repair", {})

        analysis = monitor_output
        diagnosis = diagnose_output.get("diagnosis", {})
        repair_result = repair_output.get("repair_result", {})
        # 修复：metrics_before/after 是 repair_output 的顶层字段，非 repair_result 的子字段
        metrics_before = repair_output.get("metrics_before", {})
        metrics_after = repair_output.get("metrics_after", {})

        # 提取关键信息
        fault_type = diagnosis.get("fault_type", "unknown")
        anomaly_count = analysis.get("anomaly_count", 0)
        repair_success = repair_result.get("status") == "ok"

        # 计算改善情况
        improvements = self._calculate_improvements(metrics_before, metrics_after)

        # 中文叙述（规则生成，LLM 可用时增强）
        narrative = self._build_chinese_narrative(
            fault_type, anomaly_count, diagnosis, repair_success, improvements
        )

        return {
            "dag_id": dag_id,
            "generated_at_ms": int(time.time() * 1000),
            "summary": {
                "fault_type": fault_type,
                "anomaly_count": anomaly_count,
                "diagnosis_confidence": diagnosis.get("confidence", 0),
                "diagnosis_description": diagnosis.get("description", ""),
                "repair_action": diagnosis.get("repair_action"),
                "repair_success": repair_success,
            },
            "narrative": narrative,
            "improvements": improvements,
            "final_metrics": final_metrics,
            "details": {
                "analysis": analysis,
                "diagnosis": diagnosis,
                "repair": repair_output,
            },
        }

    def _build_chinese_narrative(
        self, fault_type: str, anomaly_count: int, diagnosis: dict,
        repair_success: bool, improvements: dict
    ) -> str:
        """规则生成中文运维总结。"""
        fault_names = {
            "link_congestion": "链路拥塞", "link_outage": "链路中断",
            "cpu_overload": "CPU 过载", "ddos": "DDoS 攻击",
            "misconfig": "配置错误", "device_failure": "设备故障",
            "none": "无异常", "unknown": "未知故障",
        }
        fault_cn = fault_names.get(fault_type, fault_type)

        parts = []
        # 检测段
        if anomaly_count > 0:
            parts.append(f"检测到 {anomaly_count} 项指标异常")
        else:
            parts.append("未检测到明显异常指标")

        # 诊断段
        confidence = diagnosis.get("confidence", 0)
        desc = diagnosis.get("description", "")
        if confidence > 0:
            parts.append(f"诊断为「{fault_cn}」（置信度 {confidence:.0%}）")
            if desc:
                parts.append(desc)

        # 修复段
        if repair_success:
            parts.append("修复操作已成功执行")
        else:
            parts.append("修复操作未完全成功")

        # 改善段
        if improvements:
            parts.append(self._format_improvements(improvements))

        return "。".join(parts) + "。"

    def _format_improvements(self, improvements: dict) -> str:
        """格式化改善数据为中文。"""
        items = []
        for domain, metrics in improvements.items():
            for key, data in metrics.items():
                pct = data.get("improvement_pct", 0)
                if abs(pct) > 5:  # 忽略微小变化
                    metric_cn = {"latency_ms": "延迟", "packet_loss": "丢包率", "bandwidth_util": "带宽使用率"}
                    name = metric_cn.get(key, key)
                    direction = "下降" if pct > 0 else "上升"
                    items.append(f"{domain} {name} {direction} {abs(pct):.1f}%")
        if items:
            return "指标改善：" + "；".join(items)
        return "各域指标无明显变化"

    def _calculate_improvements(
        self, before: dict, after: dict
    ) -> dict[str, dict]:
        """计算各域指标的改善情况。"""
        improvements = {}
        for domain in before:
            if domain not in after:
                continue
            b = before[domain]
            a = after[domain]
            domain_imp = {}
            for key in ("latency_ms", "packet_loss", "bandwidth_util"):
                bv = b.get(key)
                av = a.get(key)
                if bv is not None and av is not None:
                    bv, av = float(bv), float(av)
                    pct = ((bv - av) / bv * 100) if bv > 0 else 0
                    domain_imp[key] = {
                        "before": round(bv, 4),
                        "after": round(av, 4),
                        "improvement_pct": round(pct, 2),
                    }
            improvements[domain] = domain_imp
        return improvements

    # ── LLM 增强 ──────────────────────────────────────

    async def _llm_enhance(self, report: dict) -> str:
        """使用 LLM 生成叙述性总结。"""
        prompt = f"""你是网络运维报告专家。请根据以下数据生成一份简短的运维总结报告。

## 故障类型
{report['summary']['fault_type']}

## 诊断描述
{report['summary']['diagnosis_description']}

## 修复结果
{'成功' if report['summary']['repair_success'] else '失败'}

## 指标改善
{json.dumps(report.get('improvements', {}), ensure_ascii=False, indent=2)[:600]}

请用 3-5 句话总结本次故障处理过程、结果和建议。"""
        return await self._llm.ask(prompt)


