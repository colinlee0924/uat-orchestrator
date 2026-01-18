"""OURS AAIF Orchestrator Agent.

This package provides the central Orchestrator Agent that coordinates
requests across multiple Expert Agents using parameter-based and rule-based routing.

The Orchestrator contains a built-in Agent Catalog loaded from agents.yaml,
inspired by IBM watsonx Orchestrate's Supervisor Pattern.

Routing Priority:
1. Parameter-based: If target_agent in metadata.handoff_context.context_data
2. Rule-based: Keyword and pattern matching from agents.yaml

Usage:
    from ours_aaif_orchestrator import OrchestratorAgent, HandoffContext
    from pathlib import Path

    orchestrator = OrchestratorAgent(config_path=Path("config/agents.yaml"))
    await orchestrator.setup()

    # With parameter routing (from Open WebUI Filter)
    handoff = HandoffContext(context_data={"target_agent": "jira-agent"})
    async for chunk in orchestrator.stream("建立 ticket", handoff_context=handoff):
        print(chunk, end="")

    # Without parameter (rule-based routing)
    async for chunk in orchestrator.stream("我想請假三天", thread_id="test"):
        print(chunk, end="")
"""

from mask.core.state import HandoffContext

from ours_aaif_orchestrator.agent import OrchestratorAgent
from ours_aaif_orchestrator.config_loader import load_agents_config
from ours_aaif_orchestrator.executor import (
    OrchestratorExecutor,
    create_orchestrator_executor,
)
from ours_aaif_orchestrator.models import (
    AgentConfig,
    AgentStatus,
    RoutingResult,
    RoutingRule,
)

__version__ = "0.1.0"

__all__ = [
    # Core
    "OrchestratorAgent",
    "OrchestratorExecutor",
    "create_orchestrator_executor",
    # Routing
    "HandoffContext",
    "RoutingResult",
    "RoutingRule",
    # Config
    "AgentConfig",
    "AgentStatus",
    "load_agents_config",
]
