"""Configuration loader for OURS AAIF Orchestrator.

This module provides utilities for loading agent configurations
from YAML files into the Orchestrator's built-in Agent Catalog.

Usage:
    from ours_aaif_orchestrator.config_loader import load_agents_config
    from pathlib import Path

    # Load agents from config file
    agents = load_agents_config(Path("config/agents.yaml"))

    # Access agent by name
    hr_agent = agents.get("hr-expert")
"""

import logging
from pathlib import Path
from typing import Dict

import yaml

from ours_aaif_orchestrator.models import AgentConfig, RoutingRule

logger = logging.getLogger(__name__)


def load_agents_config(
    config_path: Path,
    validate: bool = True,
) -> Dict[str, AgentConfig]:
    """Load agent configurations from YAML file.

    Args:
        config_path: Path to agents.yaml file
        validate: Whether to validate configs (default: True)

    Returns:
        Dict mapping agent names to AgentConfig

    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If YAML parsing fails
        pydantic.ValidationError: If config validation fails

    Example:
        agents = load_agents_config(Path("config/agents.yaml"))
        for name, config in agents.items():
            print(f"{name}: {config.url}")
    """
    if not config_path.exists():
        logger.warning("Config file not found: %s", config_path)
        return {}

    logger.info("Loading agents config from: %s", config_path)

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        logger.warning("Empty config file: %s", config_path)
        return {}

    agents: Dict[str, AgentConfig] = {}
    agents_data = data.get("agents", [])

    for agent_data in agents_data:
        try:
            # Handle routing_rules if it's a dict
            if "routing_rules" in agent_data and isinstance(
                agent_data["routing_rules"], dict
            ):
                agent_data["routing_rules"] = RoutingRule(**agent_data["routing_rules"])

            config = AgentConfig(**agent_data)
            agents[config.name] = config
            logger.debug("Loaded agent config: %s", config.name)
        except Exception as e:
            if validate:
                raise
            logger.warning(
                "Skipping invalid agent config: %s - %s",
                agent_data.get("name", "unknown"),
                e,
            )

    logger.info("Loaded %d agent configs", len(agents))
    return agents
