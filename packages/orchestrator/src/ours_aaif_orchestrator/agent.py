"""Orchestrator Agent - routes user requests to Expert Agents.

This module provides the OrchestratorAgent class that coordinates requests
across multiple Expert Agents using rule-based routing.

Architecture (inspired by IBM watsonx Orchestrate Supervisor Pattern):
- Built-in Agent Catalog loaded from agents.yaml
- Parameter-based routing (direct agent assignment via target_agent)
- Rule-based routing (keyword + pattern matching as fallback)
- DelegationToolFactory for A2A communication

Routing Priority:
1. Parameter-based: If target_agent is specified in handoff_context, use it directly
2. Rule-based: Keyword and pattern matching from agents.yaml

Usage:
    orchestrator = OrchestratorAgent(
        config_path=Path("config/agents.yaml")
    )
    await orchestrator.setup()

    # With parameter routing (from Open WebUI Filter)
    handoff = HandoffContext(context_data={"target_agent": "jira-agent"})
    async for chunk in orchestrator.stream("建立 ticket", handoff_context=handoff):
        print(chunk, end="")

    # With rule-based routing (fallback)
    async for chunk in orchestrator.stream("我想請假三天"):
        print(chunk, end="")
"""

import logging
import re
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional

from langchain.agents import create_agent as langchain_create_agent
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from mask.a2a import DelegationToolFactory
from mask.core.state import HandoffContext
from mask.models import LLMFactory, ModelTier

from ours_aaif_orchestrator.models import AgentConfig, AgentStatus, RoutingResult
from ours_aaif_orchestrator.config_loader import load_agents_config

logger = logging.getLogger(__name__)


class OrchestratorAgent:
    """AAIF Orchestrator - coordinates user requests across Expert Agents.

    Inspired by IBM watsonx Orchestrate Supervisor Pattern:
    - Built-in Agent Catalog (loaded from agents.yaml)
    - Rule-based routing (keyword + pattern matching)
    - DelegationToolFactory for A2A communication

    Attributes:
        config_path: Path to agents.yaml configuration file
        tier: Model tier for the orchestrator LLM
        checkpointer: LangGraph checkpointer for persistence
        fallback_agent: Default agent when no match found

    Example:
        orchestrator = OrchestratorAgent(
            config_path=Path("config/agents.yaml")
        )
        await orchestrator.setup()

        # Invoke
        response = await orchestrator.invoke("我想請假三天")
        print(response)

        # Stream
        async for chunk in orchestrator.stream("我想請假三天"):
            print(chunk, end="")
    """

    def __init__(
        self,
        config_path: Path = Path("config/agents.yaml"),
        tier: ModelTier = ModelTier.THINKING,
        checkpointer: Optional[BaseCheckpointSaver] = None,
        fallback_agent: str = "general-assistant",
    ) -> None:
        """Initialize the Orchestrator.

        Args:
            config_path: Path to agents.yaml configuration file
            tier: Model tier for the orchestrator LLM
            checkpointer: Optional checkpoint saver for persistence
            fallback_agent: Default agent when no match found
        """
        self._config_path = config_path
        self.tier = tier
        self.checkpointer = checkpointer or MemorySaver()
        self._fallback_agent = fallback_agent

        # Built-in Agent Catalog
        self._agents: Dict[str, AgentConfig] = {}
        self._delegation_factory: Optional[DelegationToolFactory] = None
        self._graph: Optional[CompiledStateGraph] = None

    async def setup(self) -> None:
        """Initialize orchestrator.

        This method:
        1. Loads agents from agents.yaml (built-in Catalog)
        2. Registers each with DelegationToolFactory
        3. Builds the LangChain agent graph
        """
        logger.info("Setting up Orchestrator (config: %s)", self._config_path)

        # Load agent catalog from YAML
        self._agents = load_agents_config(self._config_path)
        logger.info("Loaded %d agents from catalog", len(self._agents))

        # Create delegation factory and register agents
        self._delegation_factory = DelegationToolFactory()

        for name, config in self._agents.items():
            if config.status != AgentStatus.ACTIVE:
                logger.info("Skipping inactive agent: %s", name)
                continue

            try:
                await self._delegation_factory.register_agent(
                    url=config.url,
                    name=name,
                    description=config.description,
                )
                logger.info("Registered delegation tool for: %s", name)
            except Exception as e:
                logger.warning("Failed to register %s: %s", name, e)

        # Build the agent graph
        await self._build_graph()

        logger.info(
            "Orchestrator setup complete (%d active agents)",
            len([a for a in self._agents.values() if a.status == AgentStatus.ACTIVE]),
        )

    def route(self, query: str) -> RoutingResult:
        """Route query to best agent using rule-based matching.

        Matching priority:
        1. Explicit keyword match (case-insensitive) → confidence 0.9
        2. Regex pattern match → confidence 0.8
        3. Fallback to general assistant → confidence 0.5

        Higher priority agents are checked first within each category.

        Args:
            query: User query text

        Returns:
            RoutingResult with selected agent and confidence

        Example:
            result = orchestrator.route("我想請假三天")
            # Returns: RoutingResult(
            #     selected_agent="hr-expert",
            #     confidence=0.91,
            #     routing_reason="keyword_match:請假",
            #     fallback_agents=["general-assistant"]
            # )
        """
        query_lower = query.lower()
        candidates: List[tuple] = []  # (agent_name, confidence, reason)

        # Sort agents by priority (higher first)
        sorted_agents = sorted(
            self._agents.items(),
            key=lambda x: x[1].routing_rules.priority,
            reverse=True,
        )

        for name, config in sorted_agents:
            # Skip inactive agents
            if config.status != AgentStatus.ACTIVE:
                continue

            rules = config.routing_rules

            # Check keyword match (higher confidence)
            keyword_matched = False
            for keyword in rules.keywords:
                if keyword.lower() in query_lower:
                    # Base 0.9 + priority bonus (max 0.09)
                    score = 0.9 + min(rules.priority * 0.01, 0.09)
                    candidates.append((name, score, f"keyword_match:{keyword}"))
                    keyword_matched = True
                    break

            # Check pattern match (only if no keyword match)
            if not keyword_matched:
                for pattern in rules.patterns:
                    try:
                        if re.search(pattern, query, re.IGNORECASE):
                            score = 0.8 + min(rules.priority * 0.01, 0.09)
                            candidates.append((name, score, f"pattern_match:{pattern}"))
                            break
                    except re.error as e:
                        logger.warning(
                            "Invalid regex pattern for %s: %s - %s",
                            name, pattern, e
                        )

        # Sort candidates by confidence (descending)
        candidates.sort(key=lambda x: x[1], reverse=True)

        if candidates:
            selected, confidence, reason = candidates[0]
            # Build fallback list (next 2 candidates + fallback agent)
            fallbacks = [c[0] for c in candidates[1:3]]
            if self._fallback_agent and self._fallback_agent not in fallbacks:
                fallbacks.append(self._fallback_agent)

            logger.debug(
                "Routed query to %s (confidence: %.2f, reason: %s)",
                selected, confidence, reason
            )

            return RoutingResult(
                selected_agent=selected,
                confidence=confidence,
                routing_reason=reason,
                fallback_agents=fallbacks,
            )

        # No match - use fallback
        logger.debug("No match found, using fallback: %s", self._fallback_agent)
        return RoutingResult(
            selected_agent=self._fallback_agent,
            confidence=0.5,
            routing_reason="no_match:fallback",
            fallback_agents=[],
        )

    def route_by_parameter(
        self,
        handoff_context: Optional[HandoffContext],
    ) -> Optional[RoutingResult]:
        """Route to agent specified in handoff_context.context_data.target_agent.

        This method enables parameter-based routing from external systems
        like Open WebUI Filter Functions. When target_agent is specified,
        the orchestrator bypasses keyword matching and directly assigns
        the request to the specified agent.

        Args:
            handoff_context: Context from A2A message metadata containing
                             routing parameters in context_data

        Returns:
            RoutingResult if target_agent is valid, None otherwise

        Example:
            # From Open WebUI Filter Function
            handoff = HandoffContext(
                context_data={"target_agent": "jira-agent"}
            )
            result = orchestrator.route_by_parameter(handoff)
            # Returns: RoutingResult(
            #     selected_agent="jira-agent",
            #     confidence=1.0,
            #     routing_reason="parameter:target_agent",
            #     ...
            # )
        """
        if not handoff_context:
            return None

        context_data = handoff_context.context_data or {}
        target_agent = context_data.get("target_agent")

        if not target_agent:
            logger.debug("No target_agent in handoff_context")
            return None

        # Validate agent exists and is active
        if target_agent not in self._agents:
            logger.warning(
                "Parameter routing: agent '%s' not found in catalog",
                target_agent,
            )
            return None

        agent_config = self._agents[target_agent]
        if agent_config.status != AgentStatus.ACTIVE:
            logger.warning(
                "Parameter routing: agent '%s' is not active (status: %s)",
                target_agent,
                agent_config.status,
            )
            return None

        logger.info(
            "Parameter routing: directly assigned to '%s'",
            target_agent,
        )

        return RoutingResult(
            selected_agent=target_agent,
            confidence=1.0,  # Direct assignment = 100% confidence
            routing_reason="parameter:target_agent",
            fallback_agents=[self._fallback_agent] if self._fallback_agent else [],
        )

    def smart_route(
        self,
        query: str,
        handoff_context: Optional[HandoffContext] = None,
    ) -> RoutingResult:
        """Smart routing with parameter priority over rule-based matching.

        Routing priority:
        1. Parameter-based: If target_agent in handoff_context, use it directly
        2. Rule-based: Keyword and pattern matching from agents.yaml

        Args:
            query: User query text
            handoff_context: Optional context containing target_agent parameter

        Returns:
            RoutingResult with selected agent

        Example:
            # With parameter (direct assignment)
            handoff = HandoffContext(context_data={"target_agent": "jira"})
            result = orchestrator.smart_route("任何訊息", handoff)
            # → jira agent (bypasses keyword matching)

            # Without parameter (rule-based)
            result = orchestrator.smart_route("我想請假")
            # → hr-expert (keyword match)
        """
        # Priority 1: Parameter-based routing
        param_result = self.route_by_parameter(handoff_context)
        if param_result:
            return param_result

        # Priority 2: Rule-based routing
        return self.route(query)

    async def _build_graph(self) -> None:
        """Build the LangChain agent graph."""
        model = LLMFactory().get_model(tier=self.tier)

        # Get delegation tools
        delegation_tools = (
            self._delegation_factory.get_tools()
            if self._delegation_factory
            else []
        )

        # Create routing tool (uses built-in route method)
        orchestrator = self  # Capture for closure

        @tool
        def route_to_expert(query: str) -> str:
            """Determine which expert agent should handle a query.

            Use this tool to find the best expert agent for a user's request.
            The routing system will analyze the query and suggest the most
            appropriate agent based on keywords and patterns.

            Args:
                query: The user's query to analyze

            Returns:
                Information about the selected agent and routing confidence
            """
            result = orchestrator.route(query)
            return (
                f"Recommended agent: {result.selected_agent}\n"
                f"Confidence: {result.confidence:.0%}\n"
                f"Reason: {result.routing_reason}\n"
                f"Fallbacks: {', '.join(result.fallback_agents) or 'none'}"
            )

        # Build available experts description
        experts_desc = self._build_experts_description()

        # Load and format system prompt
        system_prompt = self._load_system_prompt(experts_desc)

        # Combine all tools
        tools = [route_to_expert, *delegation_tools]

        # Create agent
        self._graph = langchain_create_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            checkpointer=self.checkpointer,
        )

    def _build_experts_description(self) -> str:
        """Build description of available experts for system prompt."""
        active_agents = [
            (name, config)
            for name, config in self._agents.items()
            if config.status == AgentStatus.ACTIVE
        ]

        if not active_agents:
            return "No expert agents available."

        lines = []
        for name, config in active_agents:
            tags_str = ", ".join(config.tags) if config.tags else "general"
            keywords_str = ", ".join(config.routing_rules.keywords[:5])
            lines.append(
                f"- **{name}**: {config.description}\n"
                f"  - Tags: {tags_str}\n"
                f"  - Keywords: {keywords_str}"
            )

        return "\n".join(lines)

    def _load_system_prompt(self, experts_desc: str) -> str:
        """Load and format system prompt."""
        prompt_file = Path(__file__).parent / "prompts" / "system.md"

        if prompt_file.exists():
            template = prompt_file.read_text(encoding="utf-8")
            return template.replace("{available_experts}", experts_desc)

        # Fallback prompt
        return f"""You are the OURS AAIF Orchestrator.

## Available Experts
{experts_desc}

## Guidelines
1. Route domain-specific requests to the appropriate expert using delegation tools.
2. For general questions, answer directly.
3. If unsure, use route_to_expert to find the best agent.
"""

    async def invoke(
        self,
        message: str,
        thread_id: str = "default",
        handoff_context: Optional[HandoffContext] = None,
    ) -> str:
        """Invoke orchestrator with a message.

        Args:
            message: User message
            thread_id: Thread ID for conversation persistence
            handoff_context: Optional context with routing parameters

        Returns:
            Agent response text
        """
        if not self._graph:
            raise RuntimeError("Orchestrator not initialized. Call setup() first.")

        # Check for parameter-based routing first
        if handoff_context:
            routing_result = self.route_by_parameter(handoff_context)
            if routing_result:
                # Direct delegation to target agent
                return await self._delegate_directly(
                    message=message,
                    target_agent=routing_result.selected_agent,
                    thread_id=thread_id,
                )

        # Fall back to LLM-based routing
        config = {"configurable": {"thread_id": thread_id}}
        result = await self._graph.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config=config,
        )

        messages = result.get("messages", [])
        if messages:
            return messages[-1].content
        return ""

    async def stream(
        self,
        message: str,
        thread_id: str = "default",
        handoff_context: Optional[HandoffContext] = None,
    ) -> AsyncIterator[str]:
        """Stream orchestrator response.

        Args:
            message: User message
            thread_id: Thread ID for conversation persistence
            handoff_context: Optional context with routing parameters

        Yields:
            Response text chunks
        """
        if not self._graph:
            raise RuntimeError("Orchestrator not initialized. Call setup() first.")

        # Check for parameter-based routing first
        if handoff_context:
            routing_result = self.route_by_parameter(handoff_context)
            if routing_result:
                # Direct delegation to target agent (streaming)
                async for chunk in self._delegate_directly_stream(
                    message=message,
                    target_agent=routing_result.selected_agent,
                    thread_id=thread_id,
                ):
                    yield chunk
                return

        # Fall back to LLM-based routing
        config = {"configurable": {"thread_id": thread_id}}

        async for event in self._graph.astream_events(
            {"messages": [HumanMessage(content=message)]},
            config=config,
            version="v2",
        ):
            kind = event.get("event", "")
            data = event.get("data", {})

            if kind == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk and hasattr(chunk, "content"):
                    content = chunk.content
                    if isinstance(content, str) and content:
                        yield content

    async def _delegate_directly(
        self,
        message: str,
        target_agent: str,
        thread_id: str,
    ) -> str:
        """Delegate message directly to a specific agent (non-streaming).

        Args:
            message: User message
            target_agent: Name of the target agent
            thread_id: Thread ID for conversation persistence

        Returns:
            Agent response text
        """
        if not self._delegation_factory:
            raise RuntimeError("DelegationFactory not initialized")

        logger.info("Direct delegation to '%s'", target_agent)

        # Get the delegation tool for this agent
        tools = self._delegation_factory.get_tools()
        tool_name = f"delegate_to_{target_agent.replace('-', '_')}"

        for tool in tools:
            if tool.name == tool_name:
                # Invoke the delegation tool directly
                result = await tool.ainvoke({"task": message})
                return str(result)

        raise ValueError(f"No delegation tool found for agent: {target_agent}")

    async def _delegate_directly_stream(
        self,
        message: str,
        target_agent: str,
        thread_id: str,
    ) -> AsyncIterator[str]:
        """Delegate message directly to a specific agent (streaming).

        Args:
            message: User message
            target_agent: Name of the target agent
            thread_id: Thread ID for conversation persistence

        Yields:
            Response text chunks
        """
        if not self._delegation_factory:
            raise RuntimeError("DelegationFactory not initialized")

        logger.info("Direct delegation (streaming) to '%s'", target_agent)

        # For now, fall back to non-streaming delegation
        # TODO: Implement streaming delegation when DelegationToolFactory supports it
        result = await self._delegate_directly(message, target_agent, thread_id)
        yield result

    @property
    def available_agents(self) -> List[str]:
        """Get list of available agent names."""
        return [
            name for name, config in self._agents.items()
            if config.status == AgentStatus.ACTIVE
        ]

    @property
    def delegation_factory(self) -> Optional[DelegationToolFactory]:
        """Get delegation factory for A2A executor integration."""
        return self._delegation_factory

    @property
    def graph(self) -> Optional[CompiledStateGraph]:
        """Get compiled agent graph."""
        return self._graph

    def reload_config(self) -> None:
        """Reload agent catalog from config file.

        Note: This only updates the internal catalog. Call setup() again
        to re-register delegation tools.
        """
        logger.info("Reloading config from: %s", self._config_path)
        self._agents = load_agents_config(self._config_path)

    async def close(self) -> None:
        """Clean up resources."""
        if self._delegation_factory:
            await self._delegation_factory.close()
