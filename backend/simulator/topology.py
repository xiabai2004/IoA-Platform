"""网络模拟器 — 4域拓扑定义

架构方案 v2 §5.2：
- 4 个域（华东/华北/华南/西南）
- 每域 1 台边缘路由器 + 3 台服务器 + 20 台终端
- 域间通过 Core-Router 互联
- 链路带宽：域间 10 Gbps，域内 1 Gbps
"""

DOMAINS = ["east-china", "north-china", "south-china", "west-china"]

# 每个域的节点定义
DOMAIN_NODES: dict[str, dict] = {
    "east-china": {
        "edge_router": "Edge-R1",
        "servers": ["srv-east-1", "srv-east-2", "srv-east-3"],
        "terminals": [f"term-east-{i}" for i in range(1, 21)],
    },
    "north-china": {
        "edge_router": "Edge-R2",
        "servers": ["srv-north-1", "srv-north-2", "srv-north-3"],
        "terminals": [f"term-north-{i}" for i in range(1, 21)],
    },
    "south-china": {
        "edge_router": "Edge-R3",
        "servers": ["srv-south-1", "srv-south-2", "srv-south-3"],
        "terminals": [f"term-south-{i}" for i in range(1, 21)],
    },
    "west-china": {
        "edge_router": "Edge-R4",
        "servers": ["srv-west-1", "srv-west-2", "srv-west-3"],
        "terminals": [f"term-west-{i}" for i in range(1, 21)],
    },
}

# 链路定义：每条链路连接两个端点，有带宽上限
LINK_BANDWIDTH_GBPS = {
    "inter_domain": 10.0,    # 域间链路
    "intra_domain": 1.0,     # 域内链路（边缘路由器到服务器/终端）
}

# 域间链路（星型：每个域的 Edge 连到 Core）
INTER_DOMAIN_LINKS = [
    ("Core-Router", "Edge-R1"),
    ("Core-Router", "Edge-R2"),
    ("Core-Router", "Edge-R3"),
    ("Core-Router", "Edge-R4"),
]

# 域内链路（Edge 到 3 台服务器的链路视为聚合链路，外加终端链路简化处理）
def get_all_links() -> list[tuple[str, str, float]]:
    """返回所有链路的 (from, to, bandwidth_gbps) 列表。"""
    links = []
    # 域间链路
    for a, b in INTER_DOMAIN_LINKS:
        links.append((a, b, LINK_BANDWIDTH_GBPS["inter_domain"]))
    # 域内链路：每个域的 Edge 到服务器的聚合链路（简化）
    for domain, nodes in DOMAIN_NODES.items():
        er = nodes["edge_router"]
        for srv in nodes["servers"]:
            links.append((er, srv, LINK_BANDWIDTH_GBPS["intra_domain"]))
    return links


def get_domain_for_device(device_name: str) -> str | None:
    """根据设备名返回所属域。"""
    for domain, nodes in DOMAIN_NODES.items():
        if device_name == nodes["edge_router"] or device_name in nodes["servers"]:
            return domain
    if device_name == "Core-Router":
        return "global"
    return None
