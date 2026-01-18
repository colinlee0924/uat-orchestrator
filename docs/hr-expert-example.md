# HR Expert Agent 範例指南

本文檔說明如何基於 `mask init` 模板建立一個支援 A2A Streaming 的 Agent，以 hr-expert 為例。

## 概述

hr-expert 是一個人資專家 Agent，展示了以下功能：
- **A2A Streaming**: 即時串流回應到 Open WebUI
- **Event Propagation**: 工具調用事件傳播到 Orchestrator
- **Custom Tools**: 自定義業務工具（員工查詢、假期管理）

## 快速開始

### 1. 使用 mask init 建立專案

```bash
mask init hr-expert
cd hr-expert
```

### 2. 修改檔案以支援 Streaming

#### `src/hr_expert/agent.py`

添加 `A2AStreamingMiddleware` 支援：

```python
# 新增導入
from mask.middleware.a2a_streaming import A2AStreamingMiddleware

# 建立 middleware 實例（在 module level）
streaming_middleware = A2AStreamingMiddleware(agent_name="hr-expert")

# 在 create_agent() 中添加到 middleware 列表
async def create_agent(...):
    # ... 其他代碼 ...

    return langchain_create_agent(
        model=model,
        tools=tools,
        system_prompt=load_system_prompt(),
        middleware=[skill_middleware, streaming_middleware],  # 添加 streaming_middleware
        checkpointer=checkpointer,
    )
```

#### `src/hr_expert/main.py`

傳遞 middleware 到 executor：

```python
# 新增導入
from hr_expert.agent import create_agent, streaming_middleware

# 傳遞給 executor
executor = create_a2a_executor(
    agent,
    server_name="hr-expert",
    stream=True,
    streaming_middleware=streaming_middleware,  # 關鍵：傳遞 middleware
)
```

### 3. 添加自定義工具

建立 `src/hr_expert/tools/hr_tools.py`：

```python
from langchain_core.tools import tool

# Mock 資料
_EMPLOYEES = {
    "E001": {"name": "Alice Chen", "department": "Engineering", ...},
    "E002": {"name": "Bob Wang", "department": "Engineering", ...},
}

_LEAVE_BALANCES = {
    "E001": {"annual": 12, "sick": 10, "personal": 3},
    "E002": {"annual": 15, "sick": 10, "personal": 3},
}

@tool
def check_leave_balance(employee_id: str) -> str:
    """查詢員工假期餘額。

    Args:
        employee_id: 員工編號 (e.g., E001, E002)

    Returns:
        格式化的假期餘額資訊
    """
    if employee_id not in _LEAVE_BALANCES:
        return f"找不到員工 {employee_id}"

    balance = _LEAVE_BALANCES[employee_id]
    return (
        f"📅 假期餘額:\n"
        f"  • 年假: {balance['annual']} 天\n"
        f"  • 病假: {balance['sick']} 天\n"
        f"  • 事假: {balance['personal']} 天"
    )

@tool
def get_employee_info(employee_id: str) -> str:
    """查詢員工詳細資訊。

    Args:
        employee_id: 員工編號

    Returns:
        員工詳細資訊
    """
    if employee_id not in _EMPLOYEES:
        return f"找不到員工 {employee_id}"

    emp = _EMPLOYEES[employee_id]
    return (
        f"👤 員工資訊:\n"
        f"  • 姓名: {emp['name']}\n"
        f"  • 部門: {emp['department']}"
    )

@tool
def submit_leave_request(employee_id: str, leave_type: str, days: int) -> str:
    """提交請假申請。

    Args:
        employee_id: 員工編號
        leave_type: 假期類型 (annual, sick, personal)
        days: 請假天數

    Returns:
        請假申請結果
    """
    # 實作請假邏輯...
    return f"✅ 請假申請已提交"
```

更新 `src/hr_expert/tools/__init__.py`：

```python
from hr_expert.tools.hr_tools import (
    check_leave_balance,
    get_employee_info,
    submit_leave_request,
)

def get_custom_tools() -> list:
    return [
        check_leave_balance,
        get_employee_info,
        submit_leave_request,
    ]
```

## 架構說明

### Event Flow（事件流）

```
User Query
    │
    ▼
┌─────────────────┐
│   Orchestrator  │ ← 📤 Delegating to hr-expert...
│   (Port 10030)  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    hr-expert    │ ← 🚀 hr-expert started
│   (Port 10001)  │ ← 🤔 Analyzing...
│                 │ ← 🔧 get_employee_info()
│                 │ ← ✅ get_employee_info (2ms)
│                 │ ← 🤔 Synthesizing...
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Open WebUI    │ ← 顯示 Trajectory + 回答
│   (Port 3000)   │
└─────────────────┘
```

### Streaming Middleware 工作原理

1. **Agent 啟動**: `before_agent()` → 發送 `agent_start` 事件
2. **LLM 思考**: `before_model()` → 發送 `llm_thinking` 事件
3. **工具調用**: `wrap_tool_call()` → 發送 `tool_start` / `tool_end` 事件
4. **Agent 完成**: `after_agent()` → 發送 `agent_end` 事件

### 狀態訊息說明

| Round | 英文 | 中文 | 說明 |
|-------|------|------|------|
| 1 | Analyzing | 分析中 | 理解請求 |
| 2 | Synthesizing | 合成中 | 整合工具結果 |
| 3 | Refining | 精煉中 | 潤飾回覆 |
| 4 | Deliberating | 深思中 | 複雜多步推理 |
| 5+ | Cogitating | 思索中 | 深度思考 |

## 測試

### 啟動服務

```bash
# Terminal 1: 啟動 hr-expert
cd hr-expert
source .venv/bin/activate
python -m hr_expert.main

# Terminal 2: 啟動 Orchestrator
cd ours-aaif
source .venv/bin/activate
python -m ours_aaif_orchestrator.main
```

### 測試查詢

在 Open WebUI 中選擇 "HR Expert" 模型，輸入：

```
查詢員工 E001 的基本資料和假期餘額
```

預期 Trajectory 顯示：

```
🔍 Agent Trajectory (2.5s)
├─ 📤 Delegating to hr-expert...
├─ 🚀 [hr-expert] Agent started
├─ 🤔 [hr-expert] Analyzing...
├─ 🔧 [hr-expert] `get_employee_info(employee_id='E001')`
├─ 🔧 [hr-expert] `check_leave_balance(employee_id='E001')`
├─ ✅ [hr-expert] `get_employee_info` (2ms)
├─ ✅ [hr-expert] `check_leave_balance` (2ms)
└─ 🤔 [hr-expert] Synthesizing...
```

## Observability

### Phoenix (http://localhost:6006)

- Sessions 頁面可看到按對話分組的 traces
- 每個 session 包含多次請求的完整記錄

### Langfuse (http://localhost:3001)

- Sessions 頁面可追蹤用戶對話
- 可分析 token 使用量和成本

## 常見問題

### Q: 為什麼看不到工具調用事件？

確認以下設定：
1. `agent.py` 中有建立 `streaming_middleware` 實例
2. `main.py` 中有傳遞 `streaming_middleware` 給 executor
3. `create_a2a_executor()` 的 `stream=True`

### Q: Session 沒有正確記錄到 Phoenix/Langfuse？

確認 executor 有設置 `session.id` 屬性。OrchestratorExecutor 會自動從 `contextId` 提取 session ID。

### Q: 如何添加更多工具？

1. 在 `tools/` 目錄新增工具檔案
2. 使用 `@tool` 裝飾器定義工具
3. 在 `tools/__init__.py` 中導出並添加到 `get_custom_tools()`

## 相關文件

- [MASK Kernel CLAUDE.md](../../mask-kernel/CLAUDE.md) - 核心框架文檔
- [A2A Protocol](https://github.com/anthropics/anthropic-cookbook/tree/main/misc/agent-to-agent) - Agent-to-Agent 協議
- [Open WebUI Pipe Functions](https://docs.openwebui.com/tutorials/features/pipe-functions) - Pipe Function 文檔
