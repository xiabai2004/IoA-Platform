"""文档审核场景 — 演示 IoA 中间件通用性

场景描述：
  Reviewer Agent 审核文档 → Approver Agent 判定通过/驳回

使用与网络运维完全相同的中间件栈：
  - MessageBus（Agent 间通信）
  - Registry（Agent 注册与发现）
  - SmartRouter（任务路由）
  - DagScheduler（DAG 编排）
  - TokenAuthMiddleware（认证）

工厂函数: create_doc_review_agents(config) → list[BaseAgent]
"""

from scenarios.document_review.reviewer_agent import ReviewerAgent
from scenarios.document_review.approver_agent import ApproverAgent
from scenarios.document_review.dag_template import DOC_REVIEW_DAG_TEMPLATE


def create_doc_review_agents(bus, config: dict):
    """创建文档审核场景的 2 个 Agent（复用同一套 MessageBus）。"""
    return [
        ReviewerAgent(bus=bus, config=config),
        ApproverAgent(bus=bus, config=config),
    ]


__all__ = [
    "ReviewerAgent",
    "ApproverAgent",
    "DOC_REVIEW_DAG_TEMPLATE",
    "create_doc_review_agents",
]
