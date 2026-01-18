# OURS AAIF Quick Start Guide

本指南幫助團隊快速開始使用 OURS AAIF 平台。

## Overview

OURS AAIF (One Interface, Connect All Agents) 是基於 MASK Kernel 建構的企業級多 Agent 協調平台。

**Architecture**:
```
User Request → Orchestrator (內建 Agent Catalog)
                    │
                    ├── Rule-based Routing
                    │
                    └──→ Expert Agent (Team A/B/C...)
```

## Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) package manager
- MASK Kernel (`pip install mask-kernel`)

## For Platform Team: Running the Orchestrator

### 1. Clone and Setup

```bash
cd /path/to/PROJECT_A/ours-aaif
uv sync
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys:
# - ANTHROPIC_API_KEY
# - OPENAI_API_KEY (optional)
```

### 3. Start Orchestrator

```bash
python -m ours_aaif_orchestrator.main
```

Orchestrator 會在 `http://localhost:10000` 啟動。

### 4. Verify

```bash
curl http://localhost:10000/.well-known/agent.json
```

## For Teams: Creating an Expert Agent

### 1. Initialize Your Agent

```bash
# 在你的工作目錄
mask init my-expert
cd my-expert
```

### 2. Configure Your Agent

編輯 `src/my_expert/prompts/system.md` 設定 agent 的專業領域和行為。

### 3. Add Custom Tools (Optional)

在 `src/my_expert/tools/` 目錄下新增自訂工具。

### 4. Install and Run

```bash
pip install -e .
python -m my_expert.main
```

Your agent 會在 `http://localhost:10001` 啟動（port 可在環境變數設定）。

### 5. Register with Orchestrator

聯繫 Platform Team 將你的 agent 加入 `config/agents.yaml`:

```yaml
agents:
  - name: my-expert
    url: http://localhost:10001  # 或 K8s service URL
    description: "描述你的 agent 專業領域"
    tags: [tag1, tag2]
    routing_rules:
      keywords: [關鍵字1, 關鍵字2]
      patterns: ["正則表達式"]
      priority: 10
    owner: your-team
    status: active
```

## Testing Your Integration

### Send Request to Orchestrator

```bash
curl -X POST http://localhost:10000 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"text": "你的測試問題（包含關鍵字）"}]
      }
    }
  }'
```

如果 routing rules 配置正確，Orchestrator 會將請求路由到你的 Expert Agent。

## Troubleshooting

### Agent 沒有收到請求

1. 確認 agent 正在運行且 port 正確
2. 檢查 `agents.yaml` 中的 URL 是否正確
3. 確認 routing rules 包含你測試訊息中的關鍵字
4. 檢查 Orchestrator logs

### Routing 不如預期

1. 使用更具體的 keywords
2. 調整 priority（數字越大優先級越高）
3. 檢查是否有其他 agent 的 keywords 優先匹配

## Next Steps

- 詳細的 Expert Agent 開發指南: [EXPERT_AGENT.md](./EXPERT_AGENT.md)
- 查看範例 agents: `examples/pilot-agents/`
