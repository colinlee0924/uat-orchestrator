# OURS AAIF

**One Interface, Connect All Agents**

參考：[Linux Foundation Agentic AI Foundation](https://lfaidata.foundation/)、[IBM watsonx Orchestrate](https://www.ibm.com/products/watsonx-orchestrate)

---

OURS AAIF 是基於 [MASK Kernel](../mask-kernel) 建構的企業級多 Agent 協調平台。它讓多個團隊可以開發各自的 Expert Agents，由中央 Orchestrator 協調路由。

## Architecture

```
                    ┌─────────────────────────────────────┐
                    │     OURS AAIF Orchestrator          │
User Request ───────│  ┌─────────────────────────────┐   │
                    │  │   Built-in Agent Catalog    │   │
                    │  │   (from agents.yaml)        │   │
                    │  └─────────────────────────────┘   │
                    │              │                      │
                    │    Rule-based Routing               │
                    │              │                      │
                    └──────────────┼──────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
      ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
      │  HR Expert    │   │Finance Expert │   │  IT Support   │
      │  (mask init)  │   │  (mask init)  │   │  (mask init)  │
      └───────────────┘   └───────────────┘   └───────────────┘
          :10001              :10002              :10003
```

**核心設計**（參考 IBM watsonx Orchestrate Supervisor Pattern）：
- **Orchestrator 內建 Agent Catalog**
- **Parameter-based Routing**：透過 `target_agent` 參數直接指派（優先）
- **Rule-based Routing**：使用 keyword + pattern 匹配（fallback）
- **Expert Agents 用 `mask init` 建構**

## Routing

Orchestrator 支援兩種路由模式：

### 1. Parameter-based Routing（優先）

透過 A2A message metadata 傳入 `target_agent` 參數，直接指派給指定 agent：

```json
{
  "jsonrpc": "2.0",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [{"text": "建立一個 bug ticket"}],
      "metadata": {
        "handoff_context": {
          "context_data": {
            "target_agent": "jira-agent"
          }
        }
      }
    }
  }
}
```

**Use Case**: Open WebUI Filter Function 可以根據用戶選擇的 model 傳入對應的 `target_agent`。

### 2. Rule-based Routing（Fallback）

當沒有 `target_agent` 參數時，使用 `agents.yaml` 中定義的 keywords 和 patterns 進行匹配：

| Match Type | Confidence |
|------------|------------|
| Keyword match | 0.9 + priority bonus |
| Pattern match | 0.8 + priority bonus |
| No match (fallback) | 0.5 |

## Components

| Component | Description | Port |
|-----------|-------------|------|
| **Orchestrator** | Central agent with built-in catalog | 10000 |
| **Expert Agents** | Domain-specific agents developed by teams | 10001+ |

## Quick Start

### Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) package manager
- MASK Kernel installed

### Installation

```bash
# Clone the repository
cd /path/to/PROJECT_A

# Install dependencies
cd ours-aaif
uv sync

# Set up environment
cp .env.example .env
# Edit .env with your API keys
```

### Start Orchestrator

```bash
cd ours-aaif
python -m ours_aaif_orchestrator.main
```

Orchestrator 會在 `http://localhost:10000` 啟動。

### Create an Expert Agent (for Teams)

```bash
# 在你的工作目錄
mask init hr-expert
cd hr-expert

# 編輯 system prompt
vim src/hr_expert/prompts/system.md

# 加入自訂 tools
vim src/hr_expert/tools/__init__.py

# 安裝並啟動
pip install -e .
python -m hr_expert.main
```

### Register Agent

在 `config/agents.yaml` 中新增你的 agent：

```yaml
agents:
  - name: hr-expert
    url: http://localhost:10001
    description: "處理人資相關問題"
    tags: [hr, leave, salary]
    routing_rules:
      keywords: [hr, 請假, 薪資]
      priority: 10
    owner: hr-team
    status: active
```

重啟 Orchestrator 即可生效。

### Test

```bash
# Send request to Orchestrator
curl -X POST http://localhost:10000 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"text": "我想請假三天"}]
      }
    }
  }'

# Expected: Orchestrator routes to hr-expert based on keyword "請假"
```

## Project Structure

```
ours-aaif/
├── packages/
│   └── orchestrator/            # Orchestrator Agent
│       └── src/ours_aaif_orchestrator/
│           ├── agent.py         # Core logic with built-in catalog & routing
│           ├── executor.py      # Custom A2A executor with parameter routing
│           ├── models.py        # AgentConfig, RoutingRule, RoutingResult
│           ├── config_loader.py # YAML loader
│           ├── main.py          # A2A server entry point
│           └── prompts/system.md
├── config/
│   └── agents.yaml              # Agent Catalog configuration
├── docs/
│   └── onboarding/              # Team guides
├── examples/
│   └── pilot-agents/            # Reference implementations
└── deployments/
    └── kubernetes/              # K8s manifests
```

## MASK Kernel Integration

OURS AAIF 使用 MASK Kernel 的以下元件：

| Component | Usage |
|-----------|-------|
| `mask.a2a.DelegationToolFactory` | 建立 delegation tools 呼叫 Expert Agents |
| `mask.a2a.helpers.create_a2a_executor` | 包裝 agent 為 A2A server |
| `mask.models.LLMFactory` | Tier-based LLM 選擇 |
| `mask.observability.setup_openinference_tracing` | Phoenix tracing |
| `mask init` | Teams 用此建立 Expert Agents |

## Documentation

- [Quick Start Guide](docs/onboarding/QUICK_START.md)
- [Expert Agent Development](docs/onboarding/EXPERT_AGENT.md)

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AGENTS_CONFIG` | Path to agents.yaml | `config/agents.yaml` |
| `ORCHESTRATOR_HOST` | Host to bind | `0.0.0.0` |
| `ORCHESTRATOR_PORT` | Port to bind | `10000` |
| `PHOENIX_PROJECT_NAME` | Phoenix project name | `ours-orchestrator` |
| `LOG_LEVEL` | Logging level | `INFO` |

## License

MIT
