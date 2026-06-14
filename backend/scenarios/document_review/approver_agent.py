"""Approver Agent — 文档最终判定

能力标签: approve
域: document

接收 Reviewer 的审核结果，执行：
- 评分 ≥80 且无敏感词 → 通过
- 评分 ≥60 但有问题 → 有条件通过（需人工复核）
- 评分 <60 或有敏感词 → 驳回

与网络运维共用同一套 BaseAgent / MessageBus / Registry 基础设施。
"""

import logging
from typing import Any
from agents.base_agent import BaseAgent
from ioa_middleware.bus import MessageBus

logger = logging.getLogger("approver_agent")


class ApproverAgent(BaseAgent):
    """文档审批 Agent — 接收审核结果，做出最终判定。"""

    def __init__(self, bus: MessageBus, config: dict | None = None):
        super().__init__(
            agent_id="approver-agent",
            domain="document",
            capability="approve",
            bus=bus,
            config=config,
        )

    async def handle_message(self, topic: str, message: dict[str, Any]) -> dict[str, Any]:
        """接收 Reviewer 审核结果 + 上游上下文，做出审批决定。"""
        payload = message.get("payload", {})
        params = payload.get("params", {})

        # 提取上游 Reviewer 输出
        review_result = params.get("reviewer_output", {}) or params.get("review", {})
        score = review_result.get("score", 0)
        issues = review_result.get("issues", [])
        sensitive = review_result.get("sensitive_words", [])
        doc_title = params.get("title", "未命名文档")

        # ── 审批判定 ──────────────────────────────────
        if score >= 80 and not sensitive:
            decision = "approved"
            reason = f"文档 '{doc_title}' 审核通过（评分 {score}）"
        elif score >= 60 and not sensitive:
            decision = "conditional"
            reason = f"文档 '{doc_title}' 有条件通过（评分 {score}），需人工复核: {', '.join(issues) if issues else '无'}"
        else:
            decision = "rejected"
            if sensitive:
                reason = f"文档 '{doc_title}' 驳回：含 {len(sensitive)} 个敏感词 ({', '.join(sensitive)})"
            else:
                reason = f"文档 '{doc_title}' 驳回：评分 {score} 不达标"

        logger.info("[%s] Decision: %s — %s", self.agent_id, decision, reason)

        return {
            "success": True,
            "output": {
                "decision": decision,
                "reason": reason,
                "score": score,
                "doc_title": doc_title,
                "approved": decision == "approved",
            },
        }
