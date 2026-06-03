# IoA Agent 能力自描述规范

**版本**: 1.0.0
**更新日期**: 2026-06-03
**规范状态**: 正式版

---

## 📋 概述

本文档定义了 IoA 平台中所有 Agent 的能力自描述规范，包括：
- 能力标签定义
- 协议版本管理
- Agent 注册信息格式
- 动态能力发现机制

---

## 一、能力标签体系

### 1.1 核心能力标签

| 标签 | 描述 | 版本 | 负责 Agent |
|------|------|------|-----------|
| `orchestrate` | 任务编排与调度 | v1.0 | orchestrator-agent |
| `monitor` | 网络指标监控与采集 | v1.0 | monitor-{domain} |
| `diagnose` | 故障根因分析 | v1.0 | diagnoser-global |
| `repair` | 故障自动修复 | v1.0 | repairer-global |
| `verify` | 闭环验证 | v1.0 | verifier-global |
| `report` | 报告生成 | v1.0 | reporter-global |

### 1.2 能力标签命名规范

```
<category>.<action>[.<sub_action>]
```

**示例**:
- `monitor.metrics` - 监控指标采集
- `diagnose.root_cause` - 根因分析
- `repair.clear_fault` - 清除故障

---

## 二、协议版本管理

### 2.1 支持的协议

| 协议 | 版本 | 描述 |
|------|------|------|
| IoAP | v1.0 | IoA 消息协议 |
| MCP | v2024 | 模型上下文协议 |
| A2A | v1.0 | Agent-to-Agent 协议 |

### 2.2 协议兼容性矩阵

| Agent | IoAP | MCP | A2A |
|-------|------|-----|-----|
| orchestrator | ✅ v1.0 | ✅ v2024 | ✅ v1.0 |
| monitor | ✅ v1.0 | ✅ v2024 | ✅ v1.0 |
| diagnoser | ✅ v1.0 | ✅ v2024 | ✅ v1.0 |
| repairer | ✅ v1.0 | ✅ v2024 | ✅ v1.0 |
| verifier | ✅ v1.0 | ✅ v2024 | ✅ v1.0 |
| reporter | ✅ v1.0 | ✅ v2024 | ✅ v1.0 |

---

## 三、Agent 注册信息格式

### 3.1 Capability Profile 结构

```json
{
  "agent_id": "string",
  "domain": "string",
  "capabilities": ["string"],
  "protocols": ["string"],
  "model": "string",
  "load": 0.0,
  "status": "active|offline|busy",
  "endpoint": "string",
  "metadata": {
    "version": "string",
    "description": "string",
    "supported_tasks": ["string"],
    "input_schema": {},
    "output_schema": {},
    "constraints": {},
    "performance": {}
  }
}
```

### 3.2 各 Agent 的完整注册信息

#### Orchestrator Agent

```json
{
  "agent_id": "orchestrator-agent",
  "domain": "global",
  "capabilities": ["orchestrate"],
  "protocols": ["ioap-v1", "mcp-v2024", "a2a-v1"],
  "model": "deepseek-chat",
  "load": 0.0,
  "status": "active",
  "endpoint": "agent://orchestrator-agent",
  "metadata": {
    "version": "1.0.0",
    "description": "任务编排 Agent - 接收自然语言指令，解析意图，匹配 DAG 模板，提交执行",
    "supported_tasks": [
      "natural_language_parsing",
      "template_matching",
      "dag_submission",
      "intent_recognition"
    ],
    "input_schema": {
      "type": "object",
      "properties": {
        "message": {
          "type": "string",
          "description": "自然语言运维指令"
        },
        "domain": {
          "type": "string",
          "enum": ["east-china", "north-china", "south-china", "west-china"],
          "description": "目标域（可选，LLM 自动识别）"
        }
      },
      "required": ["message"]
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "dag_id": {"type": "string"},
        "template": {"type": "string"},
        "status": {"type": "string"}
      }
    },
    "constraints": {
      "max_input_length": 1000,
      "supported_languages": ["zh", "en"],
      "requires_llm": false
    },
    "performance": {
      "avg_response_time_ms": 500,
      "throughput_rps": 10
    }
  }
}
```

#### Monitor Agent (华东域示例)

```json
{
  "agent_id": "monitor-east-china",
  "domain": "east-china",
  "capabilities": ["monitor"],
  "protocols": ["ioap-v1", "mcp-v2024", "a2a-v1"],
  "model": null,
  "load": 0.0,
  "status": "active",
  "endpoint": "agent://monitor-east-china",
  "metadata": {
    "version": "1.0.0",
    "description": "华东域监控 Agent - 采集网络指标，检测异常",
    "supported_tasks": [
      "metrics_collection",
      "anomaly_detection",
      "threshold_checking"
    ],
    "input_schema": {
      "type": "object",
      "properties": {
        "domain": {
          "type": "string",
          "description": "监控目标域"
        },
        "metrics": {
          "type": "array",
          "items": {"type": "string"},
          "description": "指定采集的指标列表（可选）"
        }
      }
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "domain": {"type": "string"},
        "metrics": {
          "type": "object",
          "properties": {
            "latency_ms": {"type": "number"},
            "packet_loss": {"type": "number"},
            "bandwidth_util": {"type": "number"},
            "throughput_mbps": {"type": "number"},
            "connection_count": {"type": "integer"}
          }
        },
        "anomalies": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "metric": {"type": "string"},
              "value": {"type": "number"},
              "threshold": {"type": "number"},
              "severity": {"type": "string", "enum": ["medium", "high", "critical"]}
            }
          }
        }
      }
    },
    "constraints": {
      "monitored_domain": "east-china",
      "refresh_interval_ms": 1000
    },
    "performance": {
      "avg_response_time_ms": 100,
      "throughput_rps": 100
    }
  }
}
```

#### Diagnoser Agent

```json
{
  "agent_id": "diagnoser-global",
  "domain": "global",
  "capabilities": ["diagnose"],
  "protocols": ["ioap-v1", "mcp-v2024", "a2a-v1"],
  "model": "deepseek-chat",
  "load": 0.0,
  "status": "active",
  "endpoint": "agent://diagnoser-global",
  "metadata": {
    "version": "1.0.0",
    "description": "全局诊断 Agent - 规则引擎 + LLM 增强的根因分析",
    "supported_tasks": [
      "root_cause_analysis",
      "fault_classification",
      "symptom_matching",
      "llm_enhanced_diagnosis"
    ],
    "input_schema": {
      "type": "object",
      "properties": {
        "anomalies": {
          "type": "array",
          "description": "Monitor 检测到的异常列表"
        },
        "metrics": {
          "type": "object",
          "description": "当前域指标快照"
        }
      },
      "required": ["anomalies"]
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "diagnosis": {
          "type": "object",
          "properties": {
            "fault_type": {
              "type": "string",
              "enum": ["link_congestion", "link_outage", "cpu_overload", "ddos", "misconfig", "device_failure", "none", "unknown"]
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "description": {"type": "string"},
            "repair_action": {"type": "string"}
          }
        },
        "llm_insight": {"type": "string"}
      }
    },
    "constraints": {
      "supported_fault_types": 6,
      "requires_llm": false,
      "llm_enhanced": true
    },
    "performance": {
      "avg_response_time_ms": 200,
      "llm_response_time_ms": 2000
    }
  }
}
```

#### Repairer Agent

```json
{
  "agent_id": "repairer-global",
  "domain": "global",
  "capabilities": ["repair"],
  "protocols": ["ioap-v1", "mcp-v2024", "a2a-v1"],
  "model": null,
  "load": 0.0,
  "status": "active",
  "endpoint": "agent://repairer-global",
  "metadata": {
    "version": "1.0.0",
    "description": "全局修复 Agent - 执行故障修复，采集修复前后指标",
    "supported_tasks": [
      "fault_clearing",
      "metrics_comparison",
      "repair_verification"
    ],
    "input_schema": {
      "type": "object",
      "properties": {
        "diagnosis": {
          "type": "object",
          "description": "Diagnoser 的诊断结果"
        },
        "domain": {
          "type": "string",
          "description": "故障所在域"
        }
      },
      "required": ["diagnosis"]
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "repair_result": {
          "type": "object",
          "properties": {
            "status": {"type": "string", "enum": ["ok", "error"]},
            "message": {"type": "string"},
            "cleared_faults": {"type": "array"}
          }
        },
        "metrics_before": {"type": "object"},
        "metrics_after": {"type": "object"}
      }
    },
    "constraints": {
      "supported_actions": ["clear_all_faults", "clear_fault"],
      "requires_fault_list": true
    },
    "performance": {
      "avg_response_time_ms": 500
    }
  }
}
```

#### Verifier Agent

```json
{
  "agent_id": "verifier-global",
  "domain": "global",
  "capabilities": ["verify"],
  "protocols": ["ioap-v1", "mcp-v2024", "a2a-v1"],
  "model": null,
  "load": 0.0,
  "status": "active",
  "endpoint": "agent://verifier-global",
  "metadata": {
    "version": "1.0.0",
    "description": "全局验证 Agent - 闭环验证，三态判定（pass/retry/fail）",
    "supported_tasks": [
      "metrics_verification",
      "improvement_calculation",
      "verdict_determination"
    ],
    "input_schema": {
      "type": "object",
      "properties": {
        "metrics_before": {"type": "object"},
        "metrics_after": {"type": "object"},
        "domain": {"type": "string"},
        "retry_count": {"type": "integer"}
      },
      "required": ["metrics_before", "metrics_after", "domain"]
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "verdict": {
          "type": "string",
          "enum": ["pass", "retry", "fail"]
        },
        "message": {"type": "string"},
        "details": {
          "type": "object",
          "properties": {
            "passed_count": {"type": "integer"},
            "total_count": {"type": "integer"},
            "failed_metrics": {"type": "array"},
            "metrics": {"type": "array"}
          }
        }
      }
    },
    "constraints": {
      "max_verify_retries": 3,
      "verify_thresholds": {
        "latency_ms": {"max": 50.0, "min_improvement": 0.30},
        "packet_loss": {"max": 0.005, "min_improvement": 0.50},
        "bandwidth_util": {"max": 0.85, "min_improvement": 0.10}
      }
    },
    "performance": {
      "avg_response_time_ms": 200
    }
  }
}
```

#### Reporter Agent

```json
{
  "agent_id": "reporter-global",
  "domain": "global",
  "capabilities": ["report"],
  "protocols": ["ioap-v1", "mcp-v2024", "a2a-v1"],
  "model": "deepseek-chat",
  "load": 0.0,
  "status": "active",
  "endpoint": "agent://reporter-global",
  "metadata": {
    "version": "1.0.0",
    "description": "全局报告 Agent - 汇总全链路数据，生成结构化报告",
    "supported_tasks": [
      "report_generation",
      "metrics_analysis",
      "narrative_generation"
    ],
    "input_schema": {
      "type": "object",
      "properties": {
        "monitor_output": {"type": "object"},
        "diagnose_output": {"type": "object"},
        "repair_output": {"type": "object"},
        "dag_id": {"type": "string"}
      }
    },
    "output_schema": {
      "type": "object",
      "properties": {
        "dag_id": {"type": "string"},
        "generated_at_ms": {"type": "integer"},
        "summary": {
          "type": "object",
          "properties": {
            "fault_type": {"type": "string"},
            "anomaly_count": {"type": "integer"},
            "diagnosis_confidence": {"type": "number"},
            "repair_success": {"type": "boolean"}
          }
        },
        "narrative": {"type": "string"},
        "improvements": {"type": "object"},
        "final_metrics": {"type": "object"}
      }
    },
    "constraints": {
      "requires_llm": false,
      "llm_enhanced": true,
      "supported_formats": ["json", "text"]
    },
    "performance": {
      "avg_response_time_ms": 300,
      "llm_response_time_ms": 1500
    }
  }
}
```

---

## 四、动态能力发现机制

### 4.1 能力查询 API

```http
GET /registry/query?capability={capability}&status={status}
```

**参数**:
- `capability`: 能力标签（必填）
- `status`: Agent 状态（可选，默认 `active`）

**响应**:
```json
{
  "agents": [
    {
      "agent_id": "monitor-east-china",
      "domain": "east-china",
      "capabilities": ["monitor"],
      "status": "active",
      "load": 0.2,
      "metadata": {
        "version": "1.0.0",
        "description": "华东域监控 Agent",
        "supported_tasks": ["metrics_collection", "anomaly_detection"]
      }
    }
  ],
  "count": 1
}
```

### 4.2 能力匹配算法

```python
def match_capability(agent: dict, required_capability: str) -> float:
    """计算 Agent 能力匹配度（0-1）。"""
    score = 0.0

    # 1. 能力标签匹配（必须）
    if required_capability not in agent["capabilities"]:
        return 0.0
    score += 0.4

    # 2. 版本兼容性
    if agent.get("metadata", {}).get("version", "0.0.0") >= "1.0.0":
        score += 0.2

    # 3. 负载因子（负载越低分越高）
    load = agent.get("load", 0.0)
    score += 0.2 * (1.0 - load)

    # 4. 域亲和性
    if agent.get("domain") == "global":
        score += 0.1  # 全域 Agent 有额外加分
    score += 0.1

    return min(score, 1.0)
```

### 4.3 语义路由评分

```python
def semantic_score(agent: dict, task_description: str) -> float:
    """计算语义相似度评分。"""
    # 关键词匹配
    keywords = {
        "monitor": ["监控", "采集", "指标", "检测"],
        "diagnose": ["诊断", "分析", "根因", "定位"],
        "repair": ["修复", "恢复", "清除", "处理"],
        "verify": ["验证", "检查", "确认"],
        "report": ["报告", "汇总", "总结"],
    }

    agent_caps = agent.get("capabilities", [])
    task_lower = task_description.lower()

    hits = 0
    total = 0
    for cap in agent_caps:
        cap_keywords = keywords.get(cap, [])
        total += len(cap_keywords)
        hits += sum(1 for kw in cap_keywords if kw in task_lower)

    return hits / max(total, 1)
```

---

## 五、能力版本管理

### 5.1 版本号规范

采用语义化版本号：`MAJOR.MINOR.PATCH`

- **MAJOR**: 不兼容的 API 变更
- **MINOR**: 向后兼容的功能新增
- **PATCH**: 向后兼容的问题修复

### 5.2 版本兼容性规则

```python
def is_compatible(agent_version: str, required_version: str) -> bool:
    """检查版本兼容性。"""
    agent_major = int(agent_version.split(".")[0])
    required_major = int(required_version.split(".")[0])

    # 主版本号必须相同
    if agent_major != required_major:
        return False

    # 次版本号必须大于等于要求
    agent_minor = int(agent_version.split(".")[1])
    required_minor = int(required_version.split(".")[1])

    return agent_minor >= required_minor
```

### 5.3 版本更新日志

| 版本 | 日期 | 变更 |
|------|------|------|
| 1.0.0 | 2026-06-03 | 初始版本，定义 6 种核心能力 |

---

## 六、扩展能力（未来规划）

### 6.1 计划中的新能力

| 标签 | 描述 | 预计版本 |
|------|------|---------|
| `predict` | 故障预测 | v1.1 |
| `optimize` | 性能优化 | v1.2 |
| `learn` | 自适应学习 | v2.0 |
| `collaborate` | 跨域协作 | v2.0 |

### 6.2 能力插件机制

```python
class CapabilityPlugin:
    """能力插件基类。"""

    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version

    def register(self, registry: AgentRegistry):
        """注册能力到注册中心。"""
        registry.register_capability(self.name, self.version, self)

    def execute(self, params: dict) -> dict:
        """执行能力。"""
        raise NotImplementedError
```

---

## 七、附录

### A. 完整能力标签列表

```yaml
capabilities:
  orchestrate:
    description: "任务编排与调度"
    version: "1.0.0"
    agents: ["orchestrator-agent"]

  monitor:
    description: "网络指标监控与采集"
    version: "1.0.0"
    agents: ["monitor-east-china", "monitor-north-china", "monitor-south-china", "monitor-west-china"]

  diagnose:
    description: "故障根因分析"
    version: "1.0.0"
    agents: ["diagnoser-global"]

  repair:
    description: "故障自动修复"
    version: "1.0.0"
    agents: ["repairer-global"]

  verify:
    description: "闭环验证"
    version: "1.0.0"
    agents: ["verifier-global"]

  report:
    description: "报告生成"
    version: "1.0.0"
    agents: ["reporter-global"]
```

### B. 相关文档

- [IoAP 协议规范](./IOAP_PROTOCOL.md)
- [API 文档](./API_DOCUMENTATION.md)
- [架构设计](./ARCHITECTURE.md)

---

*文档版本: 1.0.0*
*最后更新: 2026-06-03*
