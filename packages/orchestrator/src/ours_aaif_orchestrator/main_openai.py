"""OpenAI-compatible API wrapper for Orchestrator.

This provides an OpenAI-compatible API endpoint that wraps the A2A orchestrator,
enabling integration with Open WebUI and other OpenAI-compatible clients.

Usage:
    # First start the A2A orchestrator (in another terminal):
    python -m ours_aaif_orchestrator.main

    # Then start this wrapper:
    python -m ours_aaif_orchestrator.main_openai

Environment variables:
    A2A_BASE_URL: URL of the A2A orchestrator (default: http://localhost:10030)
    OPENAI_COMPAT_HOST: Host to bind (default: 0.0.0.0)
    OPENAI_COMPAT_PORT: Port to bind (default: 11434)
    MODEL_NAME: Model name to expose (default: ours-orchestrator)

Open WebUI Configuration:
    1. Go to Settings > Connections > OpenAI API
    2. Add new connection:
       - URL: http://localhost:11434/v1 (or http://host.docker.internal:11434/v1 if Open WebUI runs in Docker)
       - API Key: any-value (not validated)
    3. Select "ours-orchestrator" model in chat
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from mask.a2a import run_openai_compat_server

# Load environment variables
_possible_env_paths = [
    Path.cwd() / ".env",
    Path(__file__).parent.parent.parent.parent.parent / ".env",  # ours-aaif/.env
]
for _env_path in _possible_env_paths:
    if _env_path.exists():
        load_dotenv(_env_path, override=True)
        break
else:
    load_dotenv()


def main() -> None:
    """Start OpenAI-compatible wrapper server."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

    # Configuration
    a2a_base_url = os.environ.get("A2A_BASE_URL", "http://localhost:10030")
    host = os.environ.get("OPENAI_COMPAT_HOST", "0.0.0.0")
    port = int(os.environ.get("OPENAI_COMPAT_PORT", "11434"))
    model_name = os.environ.get("MODEL_NAME", "ours-orchestrator")

    logger.info("Starting OpenAI-compatible wrapper")
    logger.info("A2A Backend: %s", a2a_base_url)
    logger.info("OpenAI API: http://%s:%d/v1", host, port)
    logger.info("Model name: %s", model_name)
    logger.info("")
    logger.info("Open WebUI Configuration:")
    logger.info("  URL: http://localhost:%d/v1", port)
    logger.info("  (Docker: http://host.docker.internal:%d/v1)", port)
    logger.info("  API Key: any-value")

    run_openai_compat_server(
        a2a_base_url=a2a_base_url,
        model_name=model_name,
        host=host,
        port=port,
    )


if __name__ == "__main__":
    main()
