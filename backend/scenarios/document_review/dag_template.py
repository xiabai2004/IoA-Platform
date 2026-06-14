"""文档审核 DAG 模板

串行拓扑:
  Reviewer (review) → Approver (approve)

条件分支:
  - Reviewer 评分 <60 → 直接结束（无需 Approver）
  - Reviewer 评分 ≥60 → 进入 Approver 判定
"""

DOC_REVIEW_DAG_TEMPLATE = {
    "template_id": "doc-review",
    "name": "文档审核",
    "description": "Reviewer 审核 → Approver 判定（通过 / 有条件通过 / 驳回）",
    "domain": "document",
    "nodes": [
        {
            "node_id": "review",
            "node_type": "review",
            "capability": "review",
            "params": {
                "content": "",       # 待审核文档内容（由用户输入填充）
                "title": "未命名文档",  # 文档标题
            },
            "max_retries": 1,
        },
        {
            "node_id": "approve",
            "node_type": "approve",
            "capability": "approve",
            "depends_on": ["review"],  # 串行依赖
            "params": {},
            "max_retries": 1,
        },
    ],
    "edges": [
        {"from": "review", "to": "approve"},
    ],
    # ── 参数 schema：用户在提交 DAG 时可填写 ──
    "param_schema": {
        "content": {
            "type": "string",
            "label": "文档内容",
            "required": True,
            "description": "待审核的文档全文内容",
        },
        "title": {
            "type": "string",
            "label": "文档标题",
            "required": False,
            "default": "未命名文档",
        },
    },
}
