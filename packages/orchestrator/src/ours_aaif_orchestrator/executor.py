"""Custom A2A Executor for Orchestrator with parameter-based routing.

This module provides OrchestratorExecutor that extends MaskAgentExecutor
to support parameter-based routing via handoff_context.context_data.target_agent.

The executor intercepts incoming A2A requests and:
1. Extracts target_agent from metadata.handoff_context.context_data
2. If target_agent is specified, bypasses LLM routing and delegates directly
3. Otherwise, falls back to standard LLM-based routing

Native A2A SDK Integration:
When directly delegating to a sub-agent, the executor uses the native A2A SDK's
ClientFactory and Client for reliable communication, avoiding the event loop
issues encountered with custom SSE parsing in uvicorn environments.

Usage:
    from ours_aaif_orchestrator.executor import OrchestratorExecutor

    orchestrator = OrchestratorAgent(...)
    await orchestrator.setup()

    executor = OrchestratorExecutor(
        agent=orchestrator.graph,
        orchestrator=orchestrator,
        server_name="ours-orchestrator",
    )
"""

import logging
import uuid
from typing import TYPE_CHECKING, Optional

from a2a.server.agent_execution import RequestContext
from a2a.server.events import EventQueue
from a2a.types import (
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    Message,
    Part,
    Role,
)

from mask.a2a.executor import MaskAgentExecutor, _create_text_artifact
from mask.core.state import HandoffContext

if TYPE_CHECKING:
    from ours_aaif_orchestrator.agent import OrchestratorAgent

logger = logging.getLogger(__name__)


class OrchestratorExecutor(MaskAgentExecutor):
    """A2A Executor with parameter-based routing using native SDK.

    Extends MaskAgentExecutor to check for target_agent in handoff_context
    before invoking the LLM. When target_agent is specified, the request
    is delegated directly to the specified agent, bypassing LLM routing.

    Native SDK Integration:
    Uses DelegationToolFactory.send_message_direct() which internally uses
    the native A2A SDK's ClientFactory and Client for reliable communication.

    Attributes:
        orchestrator: Reference to OrchestratorAgent for direct delegation
    """

    def __init__(
        self,
        orchestrator: "OrchestratorAgent",
        **kwargs,
    ) -> None:
        """Initialize OrchestratorExecutor.

        Args:
            orchestrator: OrchestratorAgent instance for parameter routing
            **kwargs: Additional arguments passed to MaskAgentExecutor
        """
        super().__init__(**kwargs)
        self._orchestrator = orchestrator

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Execute request with parameter-based routing check.

        Overrides MaskAgentExecutor.execute() to:
        1. Extract handoff_context from A2A message metadata
        2. Check for target_agent parameter
        3. If found, delegate directly using native SDK (bypass LLM)
        4. Otherwise, fall back to standard execution
        """
        # Extract handoff context from message metadata
        handoff_context = self._extract_handoff_context(context)

        # Check for parameter-based routing
        if handoff_context and self._orchestrator:
            routing_result = self._orchestrator.route_by_parameter(handoff_context)

            if routing_result:
                # Direct delegation - bypass LLM routing
                logger.info(
                    "Parameter routing: delegating to '%s' (confidence: %.0f%%)",
                    routing_result.selected_agent,
                    routing_result.confidence * 100,
                )

                await self._execute_direct_delegation_native(
                    context=context,
                    event_queue=event_queue,
                    target_agent=routing_result.selected_agent,
                    handoff_context=handoff_context,
                )
                return

        # Fall back to standard LLM-based execution
        await super().execute(context, event_queue)

    async def _execute_direct_delegation_native(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        target_agent: str,
        handoff_context: Optional[HandoffContext] = None,
    ) -> None:
        """Execute direct delegation using native A2A SDK.

        Uses DelegationToolFactory.send_message_direct() which internally uses
        the native A2A SDK's ClientFactory and Client for reliable communication.

        Args:
            context: A2A request context
            event_queue: Event queue for streaming responses
            target_agent: Name of the target agent
            handoff_context: Handoff context from request
        """
        # Extract message and IDs
        message = self._extract_user_message(context)
        context_id = self._extract_context_id(context) or str(uuid.uuid4())
        task_id = self._extract_task_id(context) or str(uuid.uuid4())

        logger.debug(
            "Direct delegation (native SDK): message='%s...', target='%s'",
            message[:50] if len(message) > 50 else message,
            target_agent,
        )

        # Emit "delegating" status
        await self._emit_status(
            event_queue=event_queue,
            context_id=context_id,
            task_id=task_id,
            text=f"📤 Delegating to {target_agent}...",
        )

        try:
            # Use native SDK via DelegationToolFactory
            delegation_factory = self._orchestrator.delegation_factory
            if not delegation_factory:
                await self._emit_error(
                    event_queue, context_id, task_id,
                    "DelegationFactory not initialized"
                )
                return

            # Send message using native SDK (this is reliable in uvicorn)
            result = await delegation_factory.send_message_direct(
                agent_name=target_agent,
                message=message,
                context_id=context_id,
                task_id=task_id,
            )

            logger.info(
                "Direct delegation to '%s' completed, response length: %d",
                target_agent,
                len(result) if result else 0,
            )

            # Emit result as artifact
            await self._emit_text_artifact(
                event_queue=event_queue,
                context_id=context_id,
                task_id=task_id,
                text=result,
            )

            # Emit completion status
            await self._emit_status(
                event_queue=event_queue,
                context_id=context_id,
                task_id=task_id,
                text=f"✅ {target_agent} completed",
                final=True,
            )

        except Exception as e:
            logger.error("Direct delegation failed: %s", e)
            await self._emit_error(
                event_queue, context_id, task_id,
                f"Delegation to {target_agent} failed: {e}"
            )

    async def _emit_status(
        self,
        event_queue: EventQueue,
        context_id: str,
        task_id: str,
        text: str,
        final: bool = False,
    ) -> None:
        """Emit a status update event.

        Args:
            event_queue: A2A event queue
            context_id: A2A context ID
            task_id: A2A task ID
            text: Status text to display
            final: Whether this is the final status
        """
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                contextId=context_id,
                taskId=task_id,
                final=final,
                status=TaskStatus(
                    state=TaskState.completed if final else TaskState.working,
                    message=Message(
                        messageId=str(uuid.uuid4()),
                        role=Role.agent,
                        parts=[Part(root={"text": text})],
                    ),
                ),
            )
        )

    async def _emit_text_artifact(
        self,
        event_queue: EventQueue,
        context_id: str,
        task_id: str,
        text: str,
    ) -> None:
        """Emit text as an artifact.

        Args:
            event_queue: A2A event queue
            context_id: A2A context ID
            task_id: A2A task ID
            text: Text content to emit
        """
        artifact_id = str(uuid.uuid4())
        artifact = _create_text_artifact(artifact_id, "response", text)
        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                artifact=artifact,
                contextId=context_id,
                taskId=task_id,
                append=False,
            )
        )

    async def _emit_error(
        self,
        event_queue: EventQueue,
        context_id: str,
        task_id: str,
        error_message: str,
    ) -> None:
        """Emit error as artifact.

        Args:
            event_queue: A2A event queue
            context_id: A2A context ID
            task_id: A2A task ID
            error_message: Error message to display
        """
        artifact_id = str(uuid.uuid4())
        artifact = _create_text_artifact(artifact_id, "error", error_message)
        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                artifact=artifact,
                contextId=context_id,
                taskId=task_id,
                append=False,
            )
        )


def create_orchestrator_executor(
    orchestrator: "OrchestratorAgent",
    stream: bool = True,
    server_name: str = "ours-orchestrator",
) -> OrchestratorExecutor:
    """Create OrchestratorExecutor with parameter-based routing.

    Convenience function to create an OrchestratorExecutor configured
    for the AAIF Orchestrator with native SDK integration.

    Args:
        orchestrator: OrchestratorAgent instance
        stream: Whether to enable streaming (default True)
        server_name: Server name for tracing

    Returns:
        Configured OrchestratorExecutor

    Example:
        orchestrator = OrchestratorAgent(...)
        await orchestrator.setup()

        executor = create_orchestrator_executor(
            orchestrator,
            server_name="ours-orchestrator",
        )

        handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=InMemoryTaskStore(),
        )
    """
    return OrchestratorExecutor(
        orchestrator=orchestrator,
        agent=orchestrator.graph,
        stream=stream,
        server_name=server_name,
        delegation_factory=orchestrator.delegation_factory,
    )
