"""Core data models for OURS AAIF Orchestrator.

This module defines the data models used by the Orchestrator's built-in Agent Catalog:
- AgentConfig: Configuration for a registered expert agent
- RoutingRule: Rule-based routing configuration
- RoutingResult: Result of routing decision
- AgentStatus: Agent availability status

All models use Pydantic for validation and serialization.
"""

from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class AgentStatus(str, Enum):
    """Agent availability status.

    Attributes:
        ACTIVE: Agent is available and healthy
        INACTIVE: Agent is intentionally disabled
        DEGRADED: Agent is available but experiencing issues
    """

    ACTIVE = "active"
    INACTIVE = "inactive"
    DEGRADED = "degraded"


class RoutingRule(BaseModel):
    """Rule-based routing configuration for an agent.

    Defines how queries should be routed to this agent based on
    keyword matching and regex patterns.

    Attributes:
        keywords: List of keywords that trigger this agent (case-insensitive)
        patterns: List of regex patterns for more complex matching
        priority: Higher priority agents are checked first (default: 0)

    Example:
        RoutingRule(
            keywords=["hr", "請假", "薪資"],
            patterns=[".*假.*", ".*薪.*"],
            priority=10
        )
    """

    keywords: List[str] = Field(
        default_factory=list,
        description="Keywords that trigger this agent (case-insensitive)",
    )
    patterns: List[str] = Field(
        default_factory=list,
        description="Regex patterns for matching queries",
    )
    priority: int = Field(
        default=0,
        description="Higher priority = checked first",
    )


class AgentConfig(BaseModel):
    """Configuration for a registered expert agent.

    This model represents the complete configuration needed to register
    an agent in the Orchestrator's Agent Catalog.

    Attributes:
        name: Unique identifier for the agent (lowercase, hyphens allowed)
        url: A2A endpoint URL (e.g., http://hr-agent:10001)
        description: Human-readable description of agent capabilities
        tags: List of tags for categorization
        routing_rules: Rules for routing queries to this agent
        owner: Team or person responsible for this agent
        status: Current availability status

    Example:
        AgentConfig(
            name="hr-expert",
            url="http://hr-agent:10001",
            description="處理人資相關問題",
            tags=["hr", "leave", "salary"],
            routing_rules=RoutingRule(
                keywords=["hr", "請假", "薪資"],
                priority=10
            ),
            owner="hr-team",
            status=AgentStatus.ACTIVE
        )
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9-]*$",
        description="Unique agent identifier (lowercase, hyphens allowed)",
    )
    url: str = Field(
        ...,
        description="A2A endpoint URL",
    )
    description: str = Field(
        ...,
        max_length=1024,
        description="Human-readable description",
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Tags for categorization",
    )
    routing_rules: RoutingRule = Field(
        default_factory=RoutingRule,
        description="Rules for routing queries to this agent",
    )
    owner: str = Field(
        default="platform",
        description="Team or person responsible",
    )
    status: AgentStatus = Field(
        default=AgentStatus.ACTIVE,
        description="Current availability status",
    )

    class Config:
        """Pydantic config."""

        use_enum_values = True


class RoutingResult(BaseModel):
    """Result of routing decision.

    Returned by the Orchestrator's route() method to determine
    which agent should handle the request.

    Attributes:
        selected_agent: Name of the agent selected to handle the query
        confidence: Confidence score (0.0 to 1.0)
        routing_reason: Human-readable explanation of why this agent was selected
        fallback_agents: List of alternative agents if primary fails

    Example:
        RoutingResult(
            selected_agent="hr-expert",
            confidence=0.95,
            routing_reason="keyword_match:請假",
            fallback_agents=["general-assistant"]
        )
    """

    selected_agent: str = Field(
        ...,
        description="Name of the selected agent",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score (0.0 to 1.0)",
    )
    routing_reason: str = Field(
        ...,
        description="Explanation of routing decision",
    )
    fallback_agents: List[str] = Field(
        default_factory=list,
        description="Alternative agents if primary fails",
    )
