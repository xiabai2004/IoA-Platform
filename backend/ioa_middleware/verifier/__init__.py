"""闭环验证器

架构方案 v2 §3.4.4：修复完成后，对比修复前后的网络指标，判定修复是否生效。

验证逻辑：
1. 获取故障域修复前后的指标快照
2. 计算各指标的变化率
3. 应用阈值判定：pass / fail / retry

阈值定义：
- latency_ms:     下降 ≥30% 且 ≤200ms → pass
- packet_loss:    下降 ≥50% 且 ≤0.01  → pass
- bandwidth_util: 下降 ≥20% 且 ≤0.85  → pass

判定规则：
- 全部指标通过 → pass
- 全部指标未通过且重试次数 < max_retries → retry
- 重试耗尽或任一指标通过 → 取最好结果
"""

import logging
from ioa_middleware.orchestrator import store

logger = logging.getLogger("verifier")

# ── 阈值配置 ──────────────────────────────────────────────

THRESHOLDS = {
    "latency_ms": {
        "max_value": 200.0,        # 延迟不得超过 200ms
        "min_improvement": 0.30,   # 至少改善 30%
    },
    "packet_loss": {
        "max_value": 0.01,         # 丢包率不得超 1%
        "min_improvement": 0.50,   # 至少改善 50%
    },
    "bandwidth_util": {
        "max_value": 0.85,         # 带宽使用率不得超 85%
        "min_improvement": 0.20,   # 至少改善 20%
    },
}

MAX_VERIFY_RETRIES = 3


# ── 核心判定 ──────────────────────────────────────────────

def _evaluate_metric(name: str, before: float, after: float) -> dict:
    """评估单个指标。返回 {passed, improvement, detail}。"""
    threshold = THRESHOLDS.get(name, {})
    max_val = threshold.get("max_value", float("inf"))
    min_imp = threshold.get("min_improvement", 0)

    if before == 0:
        improvement = 0.0
    else:
        improvement = (before - after) / before  # 正值 = 改善

    # 判定条件：改善达标 AND 当前值在阈值内
    passed = (improvement >= min_imp) and (after <= max_val)

    return {
        "metric": name,
        "before": before,
        "after": after,
        "improvement_pct": round(improvement * 100, 2),
        "passed": passed,
        "threshold_max": max_val,
        "threshold_improvement_pct": round(min_imp * 100, 2),
    }


async def verify_repair(
    dag_id: str,
    metric_before: dict,
    metric_after: dict,
    retry_count: int = 0,
) -> dict:
    """执行闭环验证。

    参数：
        dag_id:         关联的 DAG ID
        metric_before:  修复前指标 {"latency_ms": 150, "packet_loss": 0.05, ...}
        metric_after:   修复后指标 {"latency_ms": 20,  "packet_loss": 0.002, ...}
        retry_count:    当前重试次数

    返回：
        {
            "verdict": "pass" | "fail" | "retry",
            "metrics": [...],
            "passed_count": N,
            "total_count": N,
            "retry_count": N,
        }
    """
    results = []
    for metric_name in THRESHOLDS:
        before = metric_before.get(metric_name)
        after = metric_after.get(metric_name)
        if before is not None and after is not None:
            results.append(_evaluate_metric(metric_name, float(before), float(after)))

    if not results:
        logger.warning("No metrics to verify for DAG %s", dag_id)
        verdict = "pass"
    else:
        passed_count = sum(1 for r in results if r["passed"])
        total_count = len(results)

        if passed_count == total_count:
            verdict = "pass"
        elif retry_count < MAX_VERIFY_RETRIES and passed_count >= total_count // 2:
            verdict = "retry"
        elif retry_count < MAX_VERIFY_RETRIES:
            verdict = "retry"
        else:
            verdict = "fail"

    # 持久化验证记录
    await store.save_verification(dag_id, metric_before, metric_after, verdict, retry_count)

    # 审计
    await store.write_audit(
        f"verify.{verdict}" if verdict in ("pass", "fail", "retry") else "verify.pass",
        detail={
            "dag_id": dag_id,
            "verdict": verdict,
            "retry_count": retry_count,
            "metrics": results,
        },
    )

    logger.info("Verification for DAG %s: %s (%d/%d passed, retry=%d)",
                dag_id, verdict,
                sum(1 for r in results if r["passed"]), len(results),
                retry_count)

    return {
        "verdict": verdict,
        "metrics": results,
        "passed_count": sum(1 for r in results if r["passed"]),
        "total_count": len(results),
        "retry_count": retry_count,
    }
