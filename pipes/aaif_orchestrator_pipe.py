"""AAIF Orchestrator Pipe Function for Open WebUI.

This Pipe Function connects Open WebUI to the AAIF Orchestrator, supporting:
1. Multiple model selection (hr-expert, finance-expert, auto)
2. Parameter-based routing via target_agent
3. Real-time SSE streaming responses
4. Tree visualization of agent hierarchy

When you select a specific expert (e.g., hr-expert), the pipe sends
target_agent in the handoff_context metadata, triggering direct delegation.
When you select "auto", the orchestrator uses LLM-based routing.

Installation:
1. Open WebUI Admin -> Functions -> Add Function
2. Paste this code
3. Save and Enable the function
4. In chat, select the model you want (hr-expert, finance-expert, or auto)

Configuration:
- ORCHESTRATOR_URL: Your orchestrator A2A endpoint (default: host.docker.internal:10030)
"""

import asyncio
import json
import time
import uuid
from typing import Any, AsyncGenerator, Callable, Dict, Generator, List, Optional

import requests
from pydantic import BaseModel, Field


class Pipe:
    """AAIF Orchestrator Pipe with SSE streaming and tree visualization."""

    class Valves(BaseModel):
        """Configuration options exposed in Open WebUI admin."""

        ORCHESTRATOR_URL: str = Field(
            default="http://host.docker.internal:10030",
            description="Orchestrator A2A server URL (use host.docker.internal if Open WebUI runs in Docker)",
        )
        TIMEOUT: int = Field(
            default=120,
            description="Request timeout in seconds",
        )
        DEBUG: bool = Field(
            default=False,
            description="Enable debug logging to console",
        )
        SHOW_TREE: bool = Field(
            default=False,
            description="Show agent tree visualization (for debugging)",
        )
        SHOW_DURATION: bool = Field(
            default=False,
            description="Show response duration (for debugging)",
        )
        SHOW_STATUS_MESSAGES: bool = Field(
            default=False,
            description="Show internal status messages like 'Delegating to...' (for debugging)",
        )

    def __init__(self):
        """Initialize the pipe."""
        self.valves = self.Valves()

    def pipes(self) -> List[Dict[str, str]]:
        """Return available models.

        These will appear as model options in Open WebUI.
        Selecting a specific expert triggers parameter-based routing.
        Selecting 'auto' uses LLM-based routing.
        """
        return [
            {"id": "auto", "name": "🤖 AAIF Auto (LLM Routing)"},
            {"id": "hr-expert", "name": "👤 HR Expert (人資專家)"},
            {"id": "finance-expert", "name": "💰 Finance Expert (財務專家)"},
            # Add more experts here as needed
        ]

    async def pipe(
        self,
        body: Dict[str, Any],
        __user__: Optional[Dict[str, Any]] = None,
        __metadata__: Optional[Dict[str, Any]] = None,
        __event_emitter__: Optional[Callable] = None,
    ) -> AsyncGenerator[str, None]:
        """Handle chat requests with SSE streaming.

        Args:
            body: Request body containing messages and model selection.
            __user__: User info from Open WebUI.
            __metadata__: Request metadata including chat_id.
            __event_emitter__: Open WebUI event emitter for status updates.

        Yields:
            Response text chunks for streaming display.
        """
        start_time = time.time()
        messages = body.get("messages", [])

        if not messages:
            yield "No messages provided."
            return

        # Check if this is a system request (title, tags, follow-up generation)
        # These should not emit status events
        is_system_request = self._is_system_request(body, messages)

        # Get selected model (determines routing)
        model_id = body.get("model", "auto")
        # Strip pipe prefix if present (e.g., "aaif_orchestrator_pipe.hr-expert" -> "hr-expert")
        if "." in model_id:
            model_id = model_id.split(".")[-1]

        # Get chat context
        metadata = __metadata__ or {}
        chat_id = metadata.get("chat_id") or body.get("chat_id") or str(uuid.uuid4())
        user_message = messages[-1].get("content", "")

        if self.valves.DEBUG:
            print(f"[AAIF Pipe] model_id={model_id}, chat_id={chat_id}")
            print(f"[AAIF Pipe] user_message={user_message[:100]}...")

        # Build A2A message
        message = {
            "role": "user",
            "parts": [{"kind": "text", "text": user_message}],
            "messageId": str(uuid.uuid4()),
            "contextId": chat_id,
        }

        # Build request params with optional routing metadata
        params: Dict[str, Any] = {"message": message}

        # If a specific expert is selected (not "auto"), add target_agent for parameter routing
        if model_id != "auto":
            params["metadata"] = {
                "handoff_context": {
                    "context_data": {
                        "target_agent": model_id,
                    }
                }
            }
            if self.valves.DEBUG:
                print(f"[AAIF Pipe] Parameter routing to: {model_id}")

        # Build JSON-RPC request using message/stream for SSE
        request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/stream",  # Changed from message/send
            "params": params,
        }

        # Track state for tree visualization and trajectory
        agent_tree: Dict[str, Any] = {
            "root": model_id if model_id != "auto" else "orchestrator",
            "agents": {},
            "current_path": [],
            "has_status": False,  # Track if we've shown status messages
            "answer_started": False,  # Track if final answer has started
            "trajectory": [],  # Track thinking/tool events for collapsible section
            "start_time": start_time,  # For duration calculation
        }
        accumulated_text = ""
        has_yielded_content = False

        # Use event emitter only for user requests, not system requests
        event_emitter = __event_emitter__ if not is_system_request else None

        try:
            # Emit initial status (only for user requests)
            if event_emitter:
                target_desc = model_id if model_id != "auto" else "orchestrator"
                await self._emit_status(event_emitter, f"📤 Sending to {target_desc}...", done=False)

            # Send request to orchestrator with streaming
            response = requests.post(
                self.valves.ORCHESTRATOR_URL,
                json=request,
                timeout=self.valves.TIMEOUT,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
                stream=True,  # Enable streaming
            )
            response.raise_for_status()

            # Check if response is SSE
            content_type = response.headers.get("Content-Type", "")
            is_sse = "text/event-stream" in content_type

            if is_sse:
                # Process SSE stream
                async for chunk in self._process_sse_stream(response, agent_tree, event_emitter):
                    if chunk:
                        accumulated_text += chunk
                        has_yielded_content = True
                        yield chunk
            else:
                # Fallback to non-streaming response
                result = response.json()

                if self.valves.DEBUG:
                    print(f"[AAIF Pipe] Non-SSE Response: {json.dumps(result, indent=2)[:500]}...")

                # Check for errors
                if "error" in result:
                    error = result["error"]
                    yield f"**Error:** {error.get('message', 'Unknown error')}"
                    return

                # Extract text from response
                for chunk in self._extract_response_text(result):
                    if chunk:
                        accumulated_text += chunk
                        has_yielded_content = True
                        yield chunk

            # Add duration and tree visualization at the end
            end_time = time.time()
            duration = end_time - start_time

            footer_parts = []

            if self.valves.SHOW_TREE and agent_tree["agents"]:
                tree_viz = self._render_tree(agent_tree)
                if tree_viz:
                    footer_parts.append(f"\n\n---\n{tree_viz}")

            if self.valves.SHOW_DURATION:
                footer_parts.append(f"\n\n⏱️ *Duration: {duration:.2f}s*")

            if footer_parts and has_yielded_content:
                yield "".join(footer_parts)

        except requests.exceptions.Timeout:
            yield "**Error:** Request timed out. The agent may be processing a complex request."
        except requests.exceptions.ConnectionError:
            yield f"**Error:** Cannot connect to orchestrator at {self.valves.ORCHESTRATOR_URL}. Is it running?"
        except Exception as e:
            if self.valves.DEBUG:
                import traceback
                print(f"[AAIF Pipe] Exception: {traceback.format_exc()}")
            yield f"**Error:** {str(e)}"

    async def _emit_status(
        self,
        event_emitter: Optional[Callable],
        description: str,
        done: bool = False,
    ) -> None:
        """Emit a status event to Open WebUI.

        Args:
            event_emitter: Open WebUI event emitter function.
            description: Status message to display.
            done: Whether this is the final status (auto-hide when done).
        """
        if not event_emitter:
            return

        try:
            await event_emitter({
                "type": "status",
                "data": {
                    "description": description,
                    "done": done,
                    "hidden": done,  # Auto-hide when completed
                },
            })
        except Exception as e:
            if self.valves.DEBUG:
                print(f"[AAIF Pipe] Failed to emit status: {e}")

    async def _process_sse_stream(
        self,
        response: requests.Response,
        agent_tree: Dict[str, Any],
        event_emitter: Optional[Callable] = None,
    ) -> AsyncGenerator[str, None]:
        """Process SSE event stream.

        Args:
            response: The streaming HTTP response.
            agent_tree: Tree structure to track agent hierarchy.
            event_emitter: Open WebUI event emitter for status updates.

        Yields:
            Text chunks from the stream.
        """
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue

            # SSE format: "data: {...}"
            if line.startswith("data:"):
                data_str = line[5:].strip()
                if not data_str:
                    continue

                try:
                    data = json.loads(data_str)
                    async for chunk in self._handle_sse_event(data, agent_tree, event_emitter):
                        yield chunk
                except json.JSONDecodeError as e:
                    if self.valves.DEBUG:
                        print(f"[AAIF Pipe] JSON decode error: {e}, line: {line}")
                    continue

    async def _handle_sse_event(
        self,
        data: Dict[str, Any],
        agent_tree: Dict[str, Any],
        event_emitter: Optional[Callable] = None,
    ) -> AsyncGenerator[str, None]:
        """Handle a single SSE event.

        Args:
            data: Parsed SSE event data.
            agent_tree: Tree structure to track agent hierarchy.
            event_emitter: Open WebUI event emitter for status updates.

        Yields:
            Text chunks from the event.
        """
        # Check if it's a JSON-RPC response
        if "result" in data:
            result = data["result"]
            async for chunk in self._extract_from_result(result, agent_tree, event_emitter):
                yield chunk
            return

        if "error" in data:
            error = data["error"]
            yield f"**Error:** {error.get('message', 'Unknown error')}"
            return

        # Handle A2A streaming events
        event_kind = data.get("kind")

        if event_kind == "status-update":
            # Track agent status
            status = data.get("status", {})
            state = status.get("state", "")
            agent_name = data.get("agentName") or data.get("taskId", "unknown")

            # Update agent tree
            if agent_name not in agent_tree["agents"]:
                agent_tree["agents"][agent_name] = {
                    "state": state,
                    "tools_called": [],
                }
            else:
                agent_tree["agents"][agent_name]["state"] = state

            # Extract text from status message if any
            status_msg = status.get("message", {})
            if status_msg:
                parts = status_msg.get("parts", [])
                for part in parts:
                    text, metadata = self._get_part_info(part)
                    if text:
                        # Check event type
                        if self._is_hidden_event(metadata):
                            # Hidden events (like agent_end) - don't show
                            pass
                        elif self._is_thinking_event(metadata):
                            # Emit as status via event_emitter (proper Open WebUI way)
                            agent_tree["has_status"] = True
                            await self._emit_status(event_emitter, text, done=False)
                            # Track in trajectory for collapsible section
                            agent_tree["trajectory"].append({
                                "text": text,
                                "event_type": metadata.get("event_type", ""),
                                "metadata": metadata,
                            })
                            # Track tool calls from metadata for tree visualization
                            if metadata and metadata.get("event_type") == "tool_start":
                                tool_name = metadata.get("tool_name", "unknown")
                                if agent_name in agent_tree["agents"]:
                                    agent_tree["agents"][agent_name]["tools_called"].append(tool_name)
                        else:
                            # Regular text - yield normally
                            yield text

        elif event_kind == "artifact-update":
            # Stream artifact content (final answer)
            artifact = data.get("artifact", {})
            parts = artifact.get("parts", [])

            # Clear status and render trajectory when answer starts
            if agent_tree["has_status"] and not agent_tree["answer_started"]:
                agent_tree["answer_started"] = True
                await self._emit_status(event_emitter, "Completed", done=True)

                # Yield collapsible trajectory section before answer
                trajectory_section = self._render_trajectory(agent_tree)
                if trajectory_section:
                    yield trajectory_section

            for part in parts:
                text = self._get_part_text(part)
                if text:
                    yield text

        elif event_kind == "tool-call":
            # Track tool calls for tree
            tool_name = data.get("toolName", "unknown")
            agent_name = data.get("agentName", "unknown")

            if agent_name in agent_tree["agents"]:
                agent_tree["agents"][agent_name]["tools_called"].append(tool_name)

        elif event_kind == "message":
            # Direct message event
            parts = data.get("parts", [])
            for part in parts:
                text = self._get_part_text(part)
                if text:
                    yield text

    async def _extract_from_result(
        self,
        result: Dict[str, Any],
        agent_tree: Dict[str, Any],
        event_emitter: Optional[Callable] = None,
    ) -> AsyncGenerator[str, None]:
        """Extract content from a result object.

        Args:
            result: Result object from SSE or response.
            agent_tree: Tree structure to track agent hierarchy.
            event_emitter: Open WebUI event emitter for status updates.

        Yields:
            Text chunks from the result.
        """
        kind = result.get("kind")

        if kind == "message":
            parts = result.get("parts", [])
            for part in parts:
                text = self._get_part_text(part)
                if text:
                    yield text

        elif kind == "task":
            # Track task in tree
            task_id = result.get("id", "unknown")
            status = result.get("status", {})
            state = status.get("state", "")

            agent_tree["agents"][task_id] = {
                "state": state,
                "tools_called": [],
            }

            # Extract from artifacts
            artifacts = result.get("artifacts", [])
            for artifact in artifacts:
                parts = artifact.get("parts", [])
                for part in parts:
                    text = self._get_part_text(part)
                    if text:
                        yield text

            # Fallback to status message
            if not artifacts:
                status_msg = status.get("message", {})
                if status_msg:
                    parts = status_msg.get("parts", [])
                    for part in parts:
                        text = self._get_part_text(part)
                        if text:
                            yield text

        elif kind == "status-update":
            # Handle status events based on type
            status = result.get("status", {})
            status_msg = status.get("message", {})
            if status_msg:
                parts = status_msg.get("parts", [])
                for part in parts:
                    text, metadata = self._get_part_info(part)
                    if text:
                        if self._is_hidden_event(metadata):
                            # Hidden events (like agent_end) - don't show
                            pass
                        elif self._is_thinking_event(metadata):
                            # Emit as status via event_emitter
                            agent_tree["has_status"] = True
                            await self._emit_status(event_emitter, text, done=False)
                            # Track in trajectory for collapsible section
                            agent_tree["trajectory"].append({
                                "text": text,
                                "event_type": metadata.get("event_type", ""),
                                "metadata": metadata,
                            })
                        else:
                            # Regular text - yield normally
                            yield text

        elif kind == "artifact-update":
            # Clear status and render trajectory when answer starts
            if agent_tree["has_status"] and not agent_tree["answer_started"]:
                agent_tree["answer_started"] = True
                await self._emit_status(event_emitter, "Completed", done=True)

                # Yield collapsible trajectory section before answer
                trajectory_section = self._render_trajectory(agent_tree)
                if trajectory_section:
                    yield trajectory_section

            artifact = result.get("artifact", {})
            parts = artifact.get("parts", [])
            for part in parts:
                text = self._get_part_text(part)
                if text:
                    yield text

    def _extract_response_text(self, result: Dict[str, Any]) -> Generator[str, None, None]:
        """Extract text content from A2A response (non-streaming fallback).

        Args:
            result: JSON-RPC response result.

        Yields:
            Text chunks from the response.
        """
        response_result = result.get("result", {})

        # Handle direct message response
        if response_result.get("kind") == "message":
            parts = response_result.get("parts", [])
            for part in parts:
                text = self._get_part_text(part)
                if text:
                    yield text
            return

        # Handle task response with artifacts
        if response_result.get("kind") == "task":
            # First check artifacts
            artifacts = response_result.get("artifacts", [])
            for artifact in artifacts:
                parts = artifact.get("parts", [])
                for part in parts:
                    text = self._get_part_text(part)
                    if text:
                        yield text

            # Also check status message if no artifacts
            if not artifacts:
                status = response_result.get("status", {})
                status_message = status.get("message", {})
                parts = status_message.get("parts", [])
                for part in parts:
                    text = self._get_part_text(part)
                    if text:
                        yield text

    # Internal event types to show in trajectory (thinking process)
    _THINKING_EVENT_TYPES = {
        "agent_start",
        "llm_thinking",
        "tool_decision",
        "tool_start",
        "tool_end",
        # Sub-agent propagated events (same types, just from sub-agent)
        "sub_agent_status",
    }

    # Internal event types to hide completely (redundant after answer)
    _HIDDEN_EVENT_TYPES = {
        "agent_end",  # Don't show "completed" - answer speaks for itself
    }

    def _is_thinking_event(self, metadata: Optional[Dict[str, Any]]) -> bool:
        """Check if this is a thinking event (show in trajectory).

        Args:
            metadata: Part metadata dict.

        Returns:
            True if this should be shown as thinking process.
        """
        if not metadata:
            return False

        event_type = metadata.get("event_type", "")

        # Check for propagated events from sub-agents
        is_propagated = metadata.get("is_propagated", False)
        if is_propagated:
            # Propagated events use the same event_type as original
            return event_type in self._THINKING_EVENT_TYPES

        return event_type in self._THINKING_EVENT_TYPES

    def _get_part_info(self, part: Dict[str, Any]) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Extract text and metadata from a message part.

        Args:
            part: Message part dict.

        Returns:
            Tuple of (text, metadata). Both can be None.
        """
        if not isinstance(part, dict):
            return None, None

        text = None
        metadata = None

        # Handle TextPart with metadata (new format)
        if part.get("kind") == "text":
            text = part.get("text")
            metadata = part.get("metadata")

        # Nested root structure (wrapped Part)
        elif "root" in part:
            root = part["root"]
            if isinstance(root, dict):
                text = root.get("text")
                metadata = root.get("metadata")

        # Fallback to text field
        else:
            text = part.get("text")
            metadata = part.get("metadata")

        return text, metadata

    def _is_hidden_event(self, metadata: Optional[Dict[str, Any]]) -> bool:
        """Check if this event should be hidden completely.

        Args:
            metadata: Part metadata dict.

        Returns:
            True if this should be hidden.
        """
        if not metadata:
            return False
        event_type = metadata.get("event_type", "")
        return event_type in self._HIDDEN_EVENT_TYPES

    def _is_internal_event(self, metadata: Optional[Dict[str, Any]]) -> bool:
        """Check if metadata indicates an internal event (thinking or hidden).

        Args:
            metadata: Part metadata dict.

        Returns:
            True if this is an internal event.
        """
        if not metadata:
            return False

        # Check explicit is_internal flag
        if metadata.get("is_internal"):
            return True

        return self._is_thinking_event(metadata) or self._is_hidden_event(metadata)

    def _get_part_text(self, part: Dict[str, Any]) -> Optional[str]:
        """Extract text from a message part (legacy compatibility).

        Args:
            part: Message part dict.

        Returns:
            Text content or None.
        """
        text, _ = self._get_part_info(part)
        return text

    def _render_trajectory(self, agent_tree: Dict[str, Any]) -> str:
        """Render collapsible trajectory section for thinking process.

        Creates a <details> block that users can expand to see the full
        agent execution trajectory (tool calls, thinking process, etc.).

        UI/UX Design Principles:
        1. Clean visual hierarchy - agent prefix first, then action
        2. No duplicate emojis - use metadata for formatting, not raw text
        3. Consistent formatting - same pattern for all event types
        4. Scannable - users can quickly find what they're looking for

        Args:
            agent_tree: Tree structure with trajectory information.

        Returns:
            Formatted HTML/Markdown string with collapsible section.
        """
        trajectory = agent_tree.get("trajectory", [])
        if not trajectory:
            return ""

        # Calculate duration
        start_time = agent_tree.get("start_time", time.time())
        duration = time.time() - start_time

        # Build trajectory items with clean formatting
        items = []
        for event in trajectory:
            text = event.get("text", "")
            event_type = event.get("event_type", "")
            metadata = event.get("metadata", {})
            source_agent = metadata.get("source_agent")  # For propagated sub-agent events
            is_propagated = metadata.get("is_propagated", False)

            # Build agent prefix for sub-agent events (e.g., "[hr-expert]")
            agent_prefix = f"[{source_agent}] " if source_agent else ""

            # Format based on event type - use metadata for clean formatting
            if event_type == "tool_start":
                tool_name = metadata.get("tool_name", "unknown")
                tool_input = metadata.get("input", {})
                if isinstance(tool_input, dict) and tool_input:
                    input_preview = ", ".join(f"{k}={v!r}" for k, v in list(tool_input.items())[:2])
                    items.append(f"🔧 {agent_prefix}`{tool_name}({input_preview})`")
                else:
                    items.append(f"🔧 {agent_prefix}`{tool_name}()`")

            elif event_type == "tool_end":
                tool_name = metadata.get("tool_name", "unknown")
                duration_ms = metadata.get("duration_ms", 0)
                items.append(f"✅ {agent_prefix}`{tool_name}` ({duration_ms}ms)")

            elif event_type == "agent_start":
                # Extract agent name from metadata or text
                agent_name = metadata.get("agent_name", "")
                if is_propagated and source_agent:
                    items.append(f"🚀 [{source_agent}] Agent started")
                elif "Delegating" in text or "📤" in text:
                    # This is a delegation event, preserve with 📤 emoji
                    clean_text = self._strip_leading_emoji(text)
                    items.append(f"📤 {clean_text}")
                elif agent_name:
                    items.append(f"🚀 {agent_name} started")
                else:
                    # Fallback: strip emoji from text to avoid duplication
                    clean_text = self._strip_leading_emoji(text)
                    items.append(f"🚀 {clean_text}")

            elif event_type == "llm_thinking":
                # Extract the thinking phase description without emoji
                # Text is now like "Analyzing..." or "Synthesizing..." (no "round N")
                if is_propagated and source_agent:
                    clean_text = self._strip_agent_prefix(text, source_agent)
                    clean_text = self._strip_leading_emoji(clean_text)
                    items.append(f"🤔 [{source_agent}] {clean_text}")
                else:
                    clean_text = self._strip_leading_emoji(text)
                    items.append(f"🤔 {clean_text}")

            elif event_type == "tool_decision":
                if is_propagated and source_agent:
                    clean_text = self._strip_agent_prefix(text, source_agent)
                    clean_text = self._strip_leading_emoji(clean_text)
                    items.append(f"💡 [{source_agent}] {clean_text}")
                else:
                    clean_text = self._strip_leading_emoji(text)
                    items.append(f"💡 {clean_text}")

            elif event_type == "sub_agent_status":
                # Generic sub-agent status - display as-is but clean up
                clean_text = self._strip_leading_emoji(text)
                items.append(f"📋 {clean_text}")

            else:
                # Unknown event type - display text as-is
                items.append(f"• {text}")

        # Build the collapsible section
        lines = [
            "<details>",
            f"<summary>🔍 Agent Trajectory ({duration:.1f}s)</summary>",
            "",
        ]

        for item in items:
            lines.append(f"- {item}")

        lines.extend([
            "",
            "</details>",
            "",
        ])

        return "\n".join(lines)

    def _strip_leading_emoji(self, text: str) -> str:
        """Strip leading emoji from text to avoid duplication.

        Args:
            text: Text that may start with emoji.

        Returns:
            Text with leading emoji removed.
        """
        if not text:
            return text

        # Common emoji prefixes used in events
        emoji_prefixes = ["🚀 ", "🤔 ", "💡 ", "🔧 ", "✅ ", "📤 ", "📋 "]
        for prefix in emoji_prefixes:
            if text.startswith(prefix):
                return text[len(prefix):]

        return text

    def _strip_agent_prefix(self, text: str, agent_name: str) -> str:
        """Strip agent prefix like '[hr-expert] ' from text.

        Args:
            text: Text that may have agent prefix.
            agent_name: Agent name to strip.

        Returns:
            Text with agent prefix removed.
        """
        if not text or not agent_name:
            return text

        prefix = f"[{agent_name}] "
        if text.startswith(prefix):
            return text[len(prefix):]

        return text

    def _render_tree(self, agent_tree: Dict[str, Any]) -> str:
        """Render agent tree visualization.

        Args:
            agent_tree: Tree structure with agent information.

        Returns:
            Formatted tree visualization string.
        """
        if not agent_tree["agents"]:
            return ""

        lines = ["**🌳 Agent Tree:**"]
        lines.append("```")
        lines.append(f"📦 {agent_tree['root']}")

        agents = list(agent_tree["agents"].items())
        for i, (agent_name, info) in enumerate(agents):
            is_last = i == len(agents) - 1
            prefix = "└──" if is_last else "├──"
            state_emoji = self._get_state_emoji(info.get("state", ""))

            lines.append(f"  {prefix} {state_emoji} {agent_name}")

            # Show tools called
            tools = info.get("tools_called", [])
            if tools:
                tool_prefix = "    " if is_last else "│   "
                for j, tool in enumerate(tools[:3]):  # Limit to 3 tools
                    tool_marker = "└──" if j == len(tools[:3]) - 1 else "├──"
                    lines.append(f"  {tool_prefix}{tool_marker} 🔧 {tool}")
                if len(tools) > 3:
                    lines.append(f"  {tool_prefix}    ... and {len(tools) - 3} more")

        lines.append("```")
        return "\n".join(lines)

    def _get_state_emoji(self, state: str) -> str:
        """Get emoji for agent state.

        Args:
            state: Agent state string.

        Returns:
            Emoji representing the state.
        """
        state_emojis = {
            "working": "⚙️",
            "completed": "✅",
            "failed": "❌",
            "input-required": "❓",
            "pending": "⏳",
        }
        return state_emojis.get(state.lower(), "🔹")

    def _is_system_request(
        self,
        body: Dict[str, Any],
        messages: List[Dict[str, Any]],
    ) -> bool:
        """Check if this is a system request (title, tags, follow-up generation).

        Open WebUI sends automatic requests for:
        - Chat title generation
        - Tag generation
        - Follow-up suggestions

        These have a specific format starting with "### Task:" and containing
        structured sections like "### Guidelines:", "### Output:", "### Chat History:".

        These should not trigger status emissions.

        Args:
            body: Request body.
            messages: List of messages.

        Returns:
            True if this is a system request.
        """
        if not messages:
            return False

        last_message = messages[-1].get("content", "")

        # Open WebUI system requests have this specific format:
        # ### Task: ...
        # ### Guidelines: ...
        # ### Output: ...
        # ### Chat History: ...
        if last_message.startswith("### Task:"):
            if self.valves.DEBUG:
                # Extract task type from first line
                first_line = last_message.split("\n")[0]
                print(f"[AAIF Pipe] Detected Open WebUI system request: {first_line[:80]}")
            return True

        # Also check for the structured sections
        if "### Guidelines:" in last_message and "### Output:" in last_message:
            if self.valves.DEBUG:
                print("[AAIF Pipe] Detected structured system request")
            return True

        # Check metadata for task type (backup check)
        metadata = body.get("metadata", {})
        task_type = metadata.get("task", "")
        if task_type in ["title_generation", "tag_generation", "query_generation"]:
            if self.valves.DEBUG:
                print(f"[AAIF Pipe] Detected system task type: {task_type}")
            return True

        return False
