"""
SuperAgent - Event-driven multi-agent orchestrator with skill management.
Extends ADK BaseAgent to work natively with Runner and Session services.

Architecture:
  SuperAgent (LlmAgent with skills)
    ├── EventBus (event sourcing)
    ├── SkillManager (todo, research, custom)
    ├── Sub-agents (delegated via ADK AgentTool)
    └── ADK Session (persistence)

Human-in-the-loop:
  The SuperAgentSession holds the agent + runner + event_bus alive across
  multiple WebSocket turns. Mid-task user messages are routed through the
  event bus so skills (todo, research) can react to them.
"""

import asyncio
import json
from typing import Any, AsyncGenerator, Dict, List, Optional

from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents import BaseAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools import FunctionTool
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.adk.memory import InMemoryMemoryService
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.genai.types import Content, Part

from src.services.adk.super_agent.event_bus import EventBus, Event, EventType
from src.services.adk.super_agent.skill_manager import SkillManager, BUILTIN_SKILLS
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

SUPER_AGENT_SYSTEM_PROMPT = """
<super_agent_role>
You are a Super Agent - an intelligent orchestrator that can:
1. Manage todo/task lists (add, edit, delete, list)
2. Perform deep research on topics
3. Delegate tasks to specialized sub-agents
4. Coordinate multi-step workflows

You have access to skill-based tools. Use them based on user intent:
- For task/todo management: use todo_* tools
- For research/analysis: use research_* tools
- For other tasks: use your sub-agents or answer directly

IMPORTANT: When using todo or research tools, always pass the session_id parameter.
The current session_id is available in the conversation context.

<human_in_the_loop>
The user can send messages at any time during your execution.
When you receive a new user message mid-task:
- If it's a todo/task update, apply it immediately using todo_* tools
- If it's a clarification, incorporate it into your current work
- If it's a new request, acknowledge it and handle after current step
- Always confirm what you did in response to the interruption
</human_in_the_loop>
</super_agent_role>

<session_context>
Current session_id: {session_id}
</session_context>
"""


def build_super_agent(
    name: str,
    model: str,
    api_key: str,
    instruction: str = "",
    description: str = "",
    skills: Optional[List[str]] = None,
    sub_agents: Optional[List[BaseAgent]] = None,
    extra_tools: Optional[List] = None,
    session_id: str = "",
    event_bus: Optional[EventBus] = None,
) -> tuple[LlmAgent, EventBus, SkillManager]:
    """
    Build a SuperAgent as an LlmAgent with skill tools injected.

    Returns:
        (agent, event_bus, skill_manager) tuple
    """
    if event_bus is None:
        event_bus = EventBus()

    skill_manager = SkillManager(event_bus)
    skill_names = skills or list(BUILTIN_SKILLS.keys())
    skill_manager.register_all(skill_names)

    all_tools = skill_manager.get_all_tools()
    if extra_tools:
        all_tools.extend(extra_tools)

    skill_prompt = skill_manager.get_system_prompt()
    base_prompt = SUPER_AGENT_SYSTEM_PROMPT.format(session_id=session_id)

    if instruction:
        full_instruction = f"{base_prompt}\n\n{skill_prompt}\n\n<user_instruction>\n{instruction}\n</user_instruction>"
    else:
        full_instruction = f"{base_prompt}\n\n{skill_prompt}"

    agent = LlmAgent(
        name=name,
        model=LiteLlm(model=model, api_key=api_key),
        instruction=full_instruction,
        description=description or f"Super Agent: {name}",
        tools=all_tools,
        sub_agents=sub_agents or [],
    )

    logger.info(
        f"[SuperAgent] Built '{name}' with skills={skill_names}, "
        f"tools={len(all_tools)}, sub_agents={len(sub_agents or [])}"
    )

    return agent, event_bus, skill_manager


class SuperAgentSession:
    """
    Holds a persistent super agent session for human-in-the-loop WebSocket interaction.
    The Runner and EventBus stay alive across multiple user turns.
    """

    def __init__(
        self,
        agent: LlmAgent,
        event_bus: EventBus,
        skill_manager: SkillManager,
        runner: Runner,
        session_id: str,
        user_id: str,
        agent_id: str,
    ):
        self.agent = agent
        self.event_bus = event_bus
        self.skill_manager = skill_manager
        self.runner = runner
        self.session_id = session_id
        self.user_id = user_id
        self.agent_id = agent_id
        self._is_processing = False
        self._interrupt_queue: asyncio.Queue = asyncio.Queue()

    @property
    def is_processing(self) -> bool:
        return self._is_processing

    async def send_interrupt(self, message: str):
        """Queue a user message that arrived while agent is processing."""
        await self._interrupt_queue.put(message)
        # Also publish to event bus so skills can react
        await self.event_bus.publish(Event(
            type=EventType.USER_MESSAGE,
            session_id=self.session_id,
            payload={"content": message, "is_interrupt": True},
            source="user",
        ))

    async def run_turn(self, message: str) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Execute one conversation turn. Yields structured events to the WebSocket.
        Supports mid-turn interrupts via the interrupt queue.
        """
        self._is_processing = True

        try:
            # Publish user message to event bus
            await self.event_bus.publish(Event(
                type=EventType.USER_MESSAGE,
                session_id=self.session_id,
                payload={"content": message},
                source="user",
            ))

            # Run the agent
            content = Content(role="user", parts=[Part(text=message)])

            events_async = self.runner.run_async(
                user_id=self.user_id,
                session_id=self.session_id,
                new_message=content,
            )

            async for event in events_async:
                # Check for interrupts between events
                while not self._interrupt_queue.empty():
                    interrupt_msg = await self._interrupt_queue.get()
                    yield {
                        "type": "interrupt_received",
                        "content": interrupt_msg,
                        "state": self.event_bus.build_state(self.session_id),
                    }

                if not event.content or not event.content.parts:
                    continue

                # Extract text content
                text_parts = [p.text for p in event.content.parts if p.text]
                if not text_parts:
                    continue

                text = "\n".join(text_parts)
                author = getattr(event, "author", "agent")

                # Publish assistant message to event bus
                if event.content.role != "user":
                    await self.event_bus.publish(Event(
                        type=EventType.ASSISTANT_MESSAGE,
                        session_id=self.session_id,
                        payload={"content": text, "author": author},
                        source=author,
                    ))

                # Build current state from events
                current_state = self.event_bus.build_state(self.session_id)

                yield {
                    "type": "agent_message",
                    "content": text,
                    "author": author,
                    "state": {
                        "todos": current_state.get("todos", []),
                        "research": current_state.get("research", []),
                    },
                }

                # Handle escalation
                if event.actions and event.actions.escalate:
                    yield {
                        "type": "escalation",
                        "content": event.error_message or "Agent escalated",
                    }
                    break

            # Process any remaining interrupts after agent finishes
            while not self._interrupt_queue.empty():
                interrupt_msg = await self._interrupt_queue.get()
                yield {
                    "type": "interrupt_queued",
                    "content": interrupt_msg,
                }

            # Final state snapshot
            final_state = self.event_bus.build_state(self.session_id)
            yield {
                "type": "turn_complete",
                "state": final_state,
            }

        except Exception as e:
            logger.error(f"[SuperAgentSession] Error in run_turn: {e}", exc_info=True)
            yield {
                "type": "error",
                "content": str(e),
            }
        finally:
            self._is_processing = False
