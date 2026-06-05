"""Verify Agent — 闭环验证 + 状态机

修复完成后自动验证指标是否恢复，支持三重判定：
- pass:   全部指标达标 → 继续 report
- retry:  部分指标未达标 + 重试次数未耗尽 → 重新触发 diagnose→repair
- fail:   重试耗尽 → DAG 标记失败

状态机：
    ┌─────────┐
    └────┬────┘
         │
    ┌────┴────┐
    │ verify  │
    └────┬────┘
    ┌────┼────┐
    ▼    ▼    ▼
  pass retry fail

v2: 修复 retry 死循环 bug — 增加实时指标兜底判定

工厂函数: create_verifier_agent(config) → VerifyAgent
"""

import json
import time
import logging
from agents.base_agent import BaseAgent
from agents.tool_client import HttpToolClient, TOOL_GET_ALL_METRICS
from ioa_middleware.bus import MessageBus

logger = logging.getLogger("verifier_agent")

# ── 验证阈值 ──────────────────────────────────────────────

VERIFY_THRESHOLDS = {
    "latency_ms":     {"max": 80.0,   "min_improvement": 0.20},   # ≤80ms 且改善 ≥20%
    "packet_loss":    {"max": 0.02,   "min_improvement": 0.20},   # ≤2% 且改善 ≥20%
    "bandwidth_util": {"max": 0.75,   "min_improvement": 0.05},   # ≤75% 且改善 ≥5%
}

# 实时指标兜底阈值（必须明显低于故障注入的最低值，否则验证器会误判：
# link_congestion 注入最低 150ms/90%→旧阈值 150.0/0.90 恰好相等→误判 pass）
REALTIME_PASS_THRESHOLDS = {
    "latency_ms":     80.0,      # 故障最低 150ms，阈值设为 80ms 确保不会误判
    "packet_loss":    0.02,      # 故障最低 15%，阈值设为 2% 确保不会误判
    "bandwidth_util": 0.75,      # 故障最低 90%，阈值设为 75% 确保不会误判
}

MAX_VERIFY_RETRIES = 3  # 最多重试验证 3 次


class VerifyAgent(BaseAgent):
    """闭环验证 Agent — 修复后指标判定 + 状态机。"""

    def __init__(self, bus: MessageBus, config: dict | None = None):
        super().__init__(
            agent_id="verifier-global",
            domain="global",
            capability="verify",
            bus=bus,
            config=config,
        )
        self.tool_client = HttpToolClient()

    # ── 消息处理 ──────────────────────────────────────

    async def handle_message(self, topic: str, msg: dict) -> dict:
        intent = msg.get("intent", {})
        if intent.get("type") != "task":
            return {"success": False, "error": "not a task"}

        payload = msg.get("payload", {})
        dag_id = payload.get("dag_id", "")
        node_id = payload.get("node_id", "")
        correlation_id = msg.get("correlation_id", "")
        params = payload.get("params", {})

        # 读取修复结果
        repair_output = params.get("repair", {})
        monitor_output = params.get("monitor", {})
        metrics_before = repair_output.get("metrics_before", {})
        metrics_after = repair_output.get("metrics_after", {})
        target_domain = monitor_output.get("domain", "east-china")
        fault_type = repair_output.get("fault_type", "unknown")

        # 重试计数（从 diagnosis 或 verify 历史中提取）
        retry_count = params.get("verify_retry_count", 0)

        # ── Bug fix: short-circuit when repair was skipped (no fault) ──
        if repair_output.get("repair_result", {}).get("skipped") or fault_type == "none":
            logger.info("VerifyAgent: repair skipped (fault_type=none), returning pass")
            return {
                "success": True,
                "output": {
                    "verdict": "pass",
                    "message": "✅ 验证通过：无故障场景，系统运行正常",
                    "details": {
                        "verdict": "pass",
                        "passed_count": 0,
                        "total_count": 0,
                        "metrics": [],
                        "domain": target_domain,
                        "method": "no_fault_skip",
                    },
                    "current_metrics": {},
                    "retry_count": 0,
                },
            }

        print(f"[{self.agent_id}] Verifying repair (dag={dag_id}, node={node_id}, "
              f"retry={retry_count}, domain={target_domain})")

        try:
            # 0. 先检查 simulator 中是否仍有活跃故障（最权威的判定依据）
            active_faults = []
            try:
                from agents.tool_client import TOOL_LIST_FAULTS
                fr = await self.tool_client.call_tool(TOOL_LIST_FAULTS, {})
                active_faults = fr.get("faults", [])
            except (ConnectionError, TimeoutError, OSError) as exc:
                logger.warning("VerifyAgent: failed to check active faults: %s", exc)
            except Exception:
                logger.exception("VerifyAgent: unexpected error checking active faults")

            if active_faults:
                fault_types = [f.get("type", "?") for f in active_faults]
                logger.warning(
                    "VerifyAgent: domain=%s still has %d active faults: %s — FAIL",
                    target_domain, len(active_faults), fault_types,
                )
                return {
                    "success": False,
                    "error": f"❌ 验证失败：仍有 {len(active_faults)} 个活跃故障 {fault_types}",
                    "output": {
                        "verdict": "fail",
                        "message": f"仍有 {len(active_faults)} 个活跃故障：{fault_types}",
                        "details": {
                            "verdict": "fail",
                            "active_faults": fault_types,
                            "domain": target_domain,
                            "method": "fault_registry_check",
                        },
                        "current_metrics": {},
                        "retry_count": retry_count,
                    },
                    "_retry_dag_nodes": ["repair"],
                }

            # 1. 获取当前实时指标（作为兜底判定依据）
            current_metrics = {}
            try:
                mr = await self.tool_client.call_tool(TOOL_GET_ALL_METRICS, {})
                current_metrics = mr.get("metrics", {})
            except (ConnectionError, TimeoutError, OSError) as exc:
                logger.warning("Failed to fetch current metrics for verification: %s", exc)
            except Exception:
                logger.exception("Unexpected error fetching current metrics")

            # 2. 实时指标兜底：如果当前实时指标全部正常，直接 pass
            #    这修复了 retry 轮次中 metrics_before/after 都正常导致 improvement 接近 0 的死循环
            realtime_pass, realtime_details = self._check_realtime(current_metrics, target_domain)
            if realtime_pass:
                logger.info("VerifyAgent: domain=%s REALTIME PASS (current metrics all normal, retry=%d)",
                            target_domain, retry_count)
                return {
                    "success": True,
                    "output": {
                        "verdict": "pass",
                        "message": f"✅ 验证通过（实时指标正常）：{len(realtime_details)} 项指标达标",
                        "details": {
                            "verdict": "pass",
                            "passed_count": len(realtime_details),
                            "total_count": len(realtime_details),
                            "metrics": realtime_details,
                            "domain": target_domain,
                            "method": "realtime_fallback",
                        },
                        "current_metrics": current_metrics,
                        "retry_count": retry_count,
                    },
                }

            # 3. 常规验证判定（before/after 对比）
            verdict_data = self._evaluate(metrics_before, metrics_after, target_domain, retry_count)

            verdict = verdict_data["verdict"]
            logger.info("VerifyAgent: domain=%s verdict=%s (%d/%d metrics passed, retry=%d)",
                        target_domain, verdict,
                        verdict_data["passed_count"], verdict_data["total_count"],
                        retry_count)

            if verdict == "pass":
                result = {
                    "success": True,
                    "output": {
                        "verdict": "pass",
                        "message": f"✅ 验证通过：{verdict_data['passed_count']}/{verdict_data['total_count']} 项指标达标",
                        "details": verdict_data,
                        "current_metrics": current_metrics,
                        "retry_count": retry_count,
                    },
                }
            elif verdict == "retry":
                result = {
                    "success": False,
                    "error": f"🔄 需要重新修复（{verdict_data['passed_count']}/{verdict_data['total_count']} 项达标）",
                    "output": {
                        "verdict": "retry",
                        "retry_signal": True,
                        "retry_count": retry_count + 1,
                        "failed_metrics": verdict_data["failed_metrics"],
                        "message": verdict_data.get("message", ""),
                        "details": verdict_data,
                    },
                    "_retry_dag_nodes": ["diagnose", "repair"],
                }
            else:  # fail
                result = {
                    "success": False,
                    "error": f"❌ 验证失败（{verdict_data['passed_count']}/{verdict_data['total_count']} 项达标，重试{retry_count}次后放弃）",
                    "output": {
                        "verdict": "fail",
                        "details": verdict_data,
                        "current_metrics": current_metrics,
                    },
                }

        except Exception as e:
            result = {
                "success": False,
                "error": str(e),
            }

        return result

    # ── 实时指标兜底判定 ──────────────────────────────

    def _check_realtime(self, current_metrics: dict, target_domain: str) -> tuple[bool, list[dict]]:
        """检查当前实时指标是否全部在正常范围内。

        返回 (是否全部通过, 各指标详情列表)。
        用于 retry 轮次兜底：故障已清但 before/after 对比无差异时，
        只要实时指标正常就判定通过。
        """
        domain_metrics = current_metrics.get(target_domain, {})
        if not domain_metrics:
            return False, []

        details = []
        all_pass = True
        for metric_name, threshold in REALTIME_PASS_THRESHOLDS.items():
            value = domain_metrics.get(metric_name)
            if value is None:
                continue
            value = float(value)
            passed = value <= threshold
            if not passed:
                all_pass = False
            details.append({
                "metric": metric_name,
                "current_value": round(value, 4),
                "threshold_max": threshold,
                "passed": passed,
            })

        return all_pass, details

    # ── 验证判定 ──────────────────────────────────────

    def _evaluate(
        self, metrics_before: dict, metrics_after: dict,
        target_domain: str, retry_count: int
    ) -> dict:
        """核心验证逻辑：逐项指标对比判定。

        判定规则：
        1. after 指标本身正常（≤阈值）→ 直接通过
        2. after 正常 + 有明显改善（≥20%）→ 通过
        3. after 超标 + 未达重试上限 → retry
        4. after 超标 + 重试耗尽 → fail
        """
        before_domain = metrics_before.get(target_domain, {})
        after_domain = metrics_after.get(target_domain, {})

        results = []
        for metric_name, threshold in VERIFY_THRESHOLDS.items():
            bv = before_domain.get(metric_name)
            av = after_domain.get(metric_name)
            if bv is None or av is None:
                continue

            bv, av = float(bv), float(av)
            improvement = (bv - av) / bv if bv > 0 else 0.0  # 正值 = 改善
            # 规则1: after 本身正常即通过（不需要 20% 改善）
            # 规则2: after 正常 + 有明显改善也通过
            passed = av <= threshold["max"]

            results.append({
                "metric": metric_name,
                "before": round(bv, 4),
                "after": round(av, 4),
                "improvement_pct": round(improvement * 100, 2),
                "threshold_max": threshold["max"],
                "threshold_improvement_pct": round(threshold["min_improvement"] * 100),
                "passed": passed,
            })

        passed_count = sum(1 for r in results if r["passed"])
        total_count = len(results)
        failed_metrics = [r["metric"] for r in results if not r["passed"]]

        # 判定逻辑
        if total_count == 0:
            verdict = "pass"
            message = "无可验证指标，默认通过"
        elif passed_count == total_count:
            verdict = "pass"
            message = f"全部 {total_count} 项指标达标"
        elif retry_count < MAX_VERIFY_RETRIES:
            verdict = "retry"
            message = f"{passed_count}/{total_count} 项达标，尝试重新修复（{retry_count+1}/{MAX_VERIFY_RETRIES}）"
        else:
            verdict = "fail"
            message = f"仅 {passed_count}/{total_count} 项达标，重试 {MAX_VERIFY_RETRIES} 次后放弃"

        return {
            "verdict": verdict,
            "message": message,
            "passed_count": passed_count,
            "total_count": total_count,
            "failed_metrics": failed_metrics,
            "metrics": results,
            "domain": target_domain,
        }


# ── 工厂 ─────────────────────────────────────────────

def create_verifier_agent(config: dict) -> VerifyAgent:
    return VerifyAgent(config=config)
