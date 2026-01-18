"""Orchestrator A2A Server entry point.

Usage:
    python -m ours_aaif_orchestrator.main

Environment variables:
    ORCHESTRATOR_HOST: Host to bind (default: 0.0.0.0)
    ORCHESTRATOR_PORT: Port to bind (default: 10000)
    AGENTS_CONFIG: Path to agents.yaml (default: config/agents.yaml)
    PHOENIX_PROJECT_NAME: Phoenix project name for tracing
    LOG_LEVEL: Logging level (default: INFO)
"""

import asyncio
import logging
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from mask.observability import setup_dual_tracing

from ours_aaif_orchestrator.agent import OrchestratorAgent
from ours_aaif_orchestrator.executor import create_orchestrator_executor

# Load environment variables
# Try to load from multiple possible locations
_possible_env_paths = [
    Path.cwd() / ".env",
    Path(__file__).parent.parent.parent.parent.parent / ".env",  # ours-aaif/.env
]
for _env_path in _possible_env_paths:
    if _env_path.exists():
        load_dotenv(_env_path, override=True)
        break
else:
    load_dotenv()  # Fall back to default behavior


def setup_logging() -> None:
    """Configure logging."""
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_format = os.environ.get("LOG_FORMAT", "text")

    if log_format == "json":
        logging.basicConfig(
            level=log_level,
            format='{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
        )
    else:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )


async def setup_orchestrator() -> OrchestratorAgent:
    """Async setup for orchestrator agent."""
    # Get config path from environment or use default
    config_path_str = os.environ.get("AGENTS_CONFIG", "config/agents.yaml")
    config_path = Path(config_path_str)

    # If relative path, resolve from project root
    if not config_path.is_absolute():
        # Try to find config relative to package or current directory
        possible_paths = [
            Path.cwd() / config_path,
            Path(__file__).parent.parent.parent.parent.parent / config_path,
        ]
        for p in possible_paths:
            if p.exists():
                config_path = p
                break

    orchestrator = OrchestratorAgent(config_path=config_path)
    await orchestrator.setup()

    return orchestrator


def main() -> None:
    """Start the Orchestrator A2A server."""
    setup_logging()
    logger = logging.getLogger(__name__)

    # Setup tracing (supports both Phoenix and Langfuse if credentials configured)
    project_name = os.environ.get("PHOENIX_PROJECT_NAME", "ours-aaif")
    setup_dual_tracing(project_name=project_name)

    # Configuration
    host = os.environ.get("ORCHESTRATOR_HOST", "0.0.0.0")
    port = int(os.environ.get("ORCHESTRATOR_PORT", "10000"))

    logger.info("Starting OURS AAIF Orchestrator")
    logger.info("Host: %s, Port: %d", host, port)

    # Create orchestrator (async)
    orchestrator = asyncio.run(setup_orchestrator())

    # Create A2A executor with parameter-based routing support
    executor = create_orchestrator_executor(
        orchestrator=orchestrator,
        server_name="ours-orchestrator",
        stream=True,
    )

    # Create agent card
    agent_card = AgentCard(
        name="ours-orchestrator",
        description="OURS AAIF Orchestrator - routes requests to expert agents",
        url=f"http://{host}:{port}/",
        version="1.0.0",
        skills=[
            AgentSkill(
                id="routing",
                name="Request Routing",
                description="Routes user requests to specialized expert agents",
                tags=["routing", "orchestration"],
            ),
            AgentSkill(
                id="general",
                name="General Assistant",
                description="Answers general questions directly",
                tags=["general", "assistant"],
            ),
        ],
        capabilities=AgentCapabilities(streaming=True),
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
    )

    # Create handler
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
    )

    # Create application
    app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=handler,
    )

    logger.info("Server starting on http://%s:%d", host, port)
    logger.info("Available agents: %s", orchestrator.available_agents)

    uvicorn.run(
        app.build(),
        host=host,
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
