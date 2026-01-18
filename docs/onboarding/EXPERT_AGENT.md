# Expert Agent Development Guide

本指南詳細說明如何使用 MASK Kernel 開發 Expert Agent 並整合到 OURS AAIF 平台。

## Overview

Expert Agent 是專注於特定領域的智能助手，由各團隊使用 `mask init` 建立和維護。Orchestrator 會根據用戶請求中的關鍵字和模式，將請求路由到適當的 Expert Agent。

## Creating an Expert Agent

### Step 1: Initialize Project

```bash
mask init hr-expert
cd hr-expert
```

生成的專案結構:

```
hr-expert/
├── src/hr_expert/
│   ├── __init__.py
│   ├── main.py              # A2A Server entry point
│   ├── agent.py             # Agent 核心邏輯
│   ├── prompts/
│   │   └── system.md        # System prompt
│   ├── tools/
│   │   └── __init__.py      # Custom tools
│   └── skills/              # Progressive Disclosure skills
├── config/
│   └── mcp.json             # MCP server 配置
├── pyproject.toml
└── README.md
```

### Step 2: Define System Prompt

編輯 `src/hr_expert/prompts/system.md`:

```markdown
# HR Expert Agent

You are an HR expert assistant for [Company Name].

## Capabilities

- Answer questions about company HR policies
- Help employees with leave requests
- Provide information about benefits and compensation
- Guide through onboarding processes

## Guidelines

1. Always be professional and empathetic
2. Refer to official policies when answering questions
3. For sensitive matters, recommend consulting HR directly
4. Protect employee privacy at all times

## Available Tools

- `check_leave_balance`: Check employee's remaining leave days
- `submit_leave_request`: Submit a new leave request
- `get_policy_info`: Retrieve HR policy information
```

### Step 3: Implement Custom Tools

在 `src/hr_expert/tools/__init__.py` 中新增工具:

```python
from langchain_core.tools import tool
from typing import Annotated

@tool
def check_leave_balance(
    employee_id: Annotated[str, "Employee ID"]
) -> str:
    """Check the remaining leave balance for an employee."""
    # 實際實作會連接到 HR 系統
    return f"Employee {employee_id} has 10 days of annual leave remaining."

@tool
def submit_leave_request(
    employee_id: Annotated[str, "Employee ID"],
    start_date: Annotated[str, "Start date (YYYY-MM-DD)"],
    end_date: Annotated[str, "End date (YYYY-MM-DD)"],
    reason: Annotated[str, "Reason for leave"],
) -> str:
    """Submit a leave request for an employee."""
    # 實際實作會提交到 HR 系統
    return f"Leave request submitted for {employee_id} from {start_date} to {end_date}."

def get_tools():
    """Return all tools for this agent."""
    return [check_leave_balance, submit_leave_request]
```

### Step 4: Configure Agent

編輯 `src/hr_expert/agent.py` 以使用你的 tools:

```python
from mask.agent import create_mask_agent
from mask.models import ModelTier
from hr_expert.tools import get_tools

async def create_hr_agent(checkpointer=None):
    tools = get_tools()

    agent = await create_mask_agent(
        tier=ModelTier.THINKING,
        tools=tools,
        prompt_path="src/hr_expert/prompts/system.md",
        checkpointer=checkpointer,
    )

    return agent
```

### Step 5: Configure A2A Server

編輯 `src/hr_expert/main.py`:

```python
import os
import asyncio
import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from mask.a2a.helpers import create_a2a_executor
from mask.observability import setup_openinference_tracing

from hr_expert.agent import create_hr_agent

def main():
    # Setup tracing
    setup_openinference_tracing(project_name="hr-expert")

    # Configuration
    host = os.environ.get("A2A_HOST", "0.0.0.0")
    port = int(os.environ.get("A2A_PORT", "10001"))

    # Create agent
    agent = asyncio.run(create_hr_agent())

    # Create executor
    executor = create_a2a_executor(
        agent,
        server_name="hr-expert",
        stream=True,
    )

    # Create agent card
    agent_card = AgentCard(
        name="hr-expert",
        description="HR Expert Agent - handles HR-related questions",
        url=f"http://{host}:{port}/",
        version="1.0.0",
        skills=[
            AgentSkill(
                id="leave",
                name="Leave Management",
                description="Handle leave requests and balance inquiries",
                tags=["hr", "leave"],
            ),
            AgentSkill(
                id="policy",
                name="HR Policy",
                description="Answer questions about HR policies",
                tags=["hr", "policy"],
            ),
        ],
        capabilities=AgentCapabilities(streaming=True),
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
    )

    # Create handler and app
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
    )

    app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=handler,
    )

    uvicorn.run(app.build(), host=host, port=port)

if __name__ == "__main__":
    main()
```

### Step 6: Install and Test

```bash
# Install
pip install -e .

# Run
python -m hr_expert.main
```

測試:

```bash
curl -X POST http://localhost:10001 \
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
```

## Registering with Orchestrator

### 1. Prepare Registration Info

收集以下資訊:

| Field | Example | Description |
|-------|---------|-------------|
| name | `hr-expert` | 唯一識別名稱 |
| url | `http://localhost:10001` | Agent A2A endpoint |
| description | 處理人資相關問題 | 簡短描述 |
| tags | `[hr, leave, salary]` | 分類標籤 |
| keywords | `[請假, 薪資, 人資]` | Routing 關鍵字 |
| patterns | `[".*假.*", ".*薪.*"]` | Routing 正則表達式 |
| priority | `10` | 優先級 (數字越大越優先) |
| owner | `hr-team` | 負責團隊 |

### 2. Submit to Platform Team

將以上資訊提交給 Platform Team，他們會更新 `config/agents.yaml`:

```yaml
agents:
  - name: hr-expert
    url: http://hr-agent:10001  # K8s service URL
    description: "處理人資相關問題"
    tags: [hr, leave, salary]
    routing_rules:
      keywords: [hr, 請假, 薪資, 人資, 出勤, 休假, 年假]
      patterns: [".*假.*", ".*薪.*", ".*出勤.*"]
      priority: 10
    owner: hr-team
    status: active
```

### 3. Deployment

在 Kubernetes 環境中部署你的 agent:

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hr-expert
spec:
  replicas: 2
  selector:
    matchLabels:
      app: hr-expert
  template:
    metadata:
      labels:
        app: hr-expert
    spec:
      containers:
      - name: hr-expert
        image: your-registry/hr-expert:latest
        ports:
        - containerPort: 10001
        env:
        - name: A2A_PORT
          value: "10001"
        - name: ANTHROPIC_API_KEY
          valueFrom:
            secretKeyRef:
              name: llm-secrets
              key: anthropic-api-key
---
apiVersion: v1
kind: Service
metadata:
  name: hr-agent
spec:
  selector:
    app: hr-expert
  ports:
  - port: 10001
    targetPort: 10001
```

## Best Practices

### Routing Rules Design

1. **使用具體的 keywords**: 避免太通用的詞彙
2. **考慮用戶常用語**: 包含口語化的表達方式
3. **使用 patterns 補充**: 正則表達式可以捕捉變化形式
4. **設定合理的 priority**: 專業度越高的 agent 優先級應越高

### Tool Design

1. **單一職責**: 每個 tool 只做一件事
2. **清晰的描述**: Tool description 會影響 LLM 的選擇
3. **類型註解**: 使用 `Annotated` 提供參數說明
4. **錯誤處理**: 返回有意義的錯誤訊息

### Observability

確保啟用 Phoenix tracing:

```python
from mask.observability import setup_openinference_tracing

setup_openinference_tracing(
    project_name="hr-expert",
    endpoint="http://phoenix:6006",  # Phoenix endpoint
)
```

這樣可以在 Phoenix UI 中追蹤 agent 的行為和性能。

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| Tool 沒有被呼叫 | 檢查 tool description 是否清晰描述功能 |
| Routing 失敗 | 確認 keywords 包含用戶可能使用的詞彙 |
| 回應太慢 | 考慮使用 `ModelTier.FAST` 或優化 tools |
| 連線錯誤 | 確認 A2A endpoint URL 和 port 正確 |

### Debug Mode

在開發環境啟用 debug logging:

```bash
LOG_LEVEL=DEBUG python -m hr_expert.main
```

## Support

- Platform Team: platform-team@company.com
- Documentation: 本文件及 MASK Kernel 文件
- Issues: 在內部 GitLab 開 issue
