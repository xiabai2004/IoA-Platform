"""Reviewer Agent — 文档内容审核

能力标签: review
域: document

审核规则：
- 检查文档长度（≥50 字符）
- 检查敏感词
- 检查格式规范（标题、段落）
- 综合打分（0-100）

与网络运维共用同一套 BaseAgent / MessageBus / Registry 基础设施。
"""

import logging
from typing import Any
from agents.base_agent import BaseAgent
from ioa_middleware.bus import MessageBus

logger = logging.getLogger("reviewer_agent")

# ── 审核规则 ──────────────────────────────────────────

MIN_CONTENT_LENGTH = 50
SENSITIVE_WORDS = ["违禁", "非法", "诈骗"]

SCORE_WEIGHTS = {
    "length": 0.3,   # 内容长度
    "safety": 0.4,   # 安全审核
    "format": 0.3,   # 格式规范
}


class ReviewerAgent(BaseAgent):
    """文档审核 Agent — 接收文档内容，输出审核评分和建议。"""

    def __init__(self, bus: MessageBus, config: dict | None = None):
        super().__init__(
            agent_id="reviewer-agent",
            domain="document",
            capability="review",
            bus=bus,
            config=config,
        )

    async def handle_message(self, topic: str, message: dict[str, Any]) -> dict[str, Any]:
        """接收文档内容，执行多维度审核。"""
        payload = message.get("payload", {})
        content = payload.get("params", {}).get("content", "")
        doc_title = payload.get("params", {}).get("title", "未命名文档")

        if not content:
            return {"success": False, "error": "文档内容为空", "score": 0}

        # 1. 内容长度评分
        length_score = min(100, len(content) / MIN_CONTENT_LENGTH * 100)

        # 2. 安全审核 — 敏感词检测
        found_sensitive = []
        for word in SENSITIVE_WORDS:
            if word in content:
                found_sensitive.append(word)
        safety_score = 100 if not found_sensitive else max(0, 100 - len(found_sensitive) * 40)

        # 3. 格式规范 — 检查是否有标题和段落分隔
        has_title = bool(doc_title and doc_title != "未命名文档")
        has_paragraphs = "\n" in content or len(content) > 100
        format_score = (50 if has_title else 20) + (50 if has_paragraphs else 20)

        # 4. 综合评分
        total_score = (
            length_score * SCORE_WEIGHTS["length"]
            + safety_score * SCORE_WEIGHTS["safety"]
            + format_score * SCORE_WEIGHTS["format"]
        )

        issues = []
        if len(content) < MIN_CONTENT_LENGTH:
            issues.append(f"文档内容过短 ({len(content)} 字符，要求 ≥{MIN_CONTENT_LENGTH})")
        if found_sensitive:
            issues.append(f"发现 {len(found_sensitive)} 个敏感词: {', '.join(found_sensitive)}")
        if not has_title:
            issues.append("缺少有效标题")

        logger.info(
            "[%s] Reviewed doc '%s': score=%.1f (len=%.0f, safety=%.0f, fmt=%.0f), issues=%d",
            self.agent_id, doc_title, total_score, length_score, safety_score, format_score, len(issues),
        )

        return {
            "success": True,
            "output": {
                "score": round(total_score, 1),
                "length_score": round(length_score, 1),
                "safety_score": safety_score,
                "format_score": format_score,
                "issues": issues,
                "sensitive_words": found_sensitive,
                "passed": total_score >= 60,
            },
        }
