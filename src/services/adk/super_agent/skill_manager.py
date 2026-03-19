"""
SkillManager - Manages skills (tools) that the SuperAgent can invoke.
Skills are ADK-compatible FunctionTool wrappers with event bus integration.
"""

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type
from google.adk.tools import FunctionTool
from src.services.adk.super_agent.event_bus import EventBus, Event, EventType
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class BaseSkill(ABC):
    """Base class for all skills. Each skill produces ADK-compatible tools."""

    name: str = ""
    description: str = ""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus

    @abstractmethod
    def get_tools(self) -> List[FunctionTool]:
        """Return list of ADK FunctionTools this skill provides."""
        ...

    @abstractmethod
    def get_system_prompt_fragment(self) -> str:
        """Return instruction fragment to inject into the agent's system prompt."""
        ...


class TodoSkill(BaseSkill):
    """
    Todo list management skill.
    Provides tools: todo_add, todo_list, todo_update, todo_delete.
    State is derived from EventBus events (event sourcing).
    """

    name = "todo"
    description = "Manage a todo/task list for the user"

    def get_tools(self) -> List[FunctionTool]:
        return [
            FunctionTool(self.todo_add),
            FunctionTool(self.todo_list),
            FunctionTool(self.todo_update),
            FunctionTool(self.todo_delete),
        ]

    def get_system_prompt_fragment(self) -> str:
        return (
            "\n<skill_todo>\n"
            "You have a TODO list skill. Use these tools to manage tasks:\n"
            "- todo_add(session_id, content, priority): Add a new todo item\n"
            "- todo_list(session_id): List all todo items with their status\n"
            "- todo_update(session_id, todo_id, status, content): Update a todo item\n"
            "- todo_delete(session_id, todo_id): Delete a todo item\n"
            "When the user asks to manage tasks, create lists, or track work items, use these tools.\n"
            "Always show the updated list after modifications.\n"
            "</skill_todo>\n"
        )

    async def todo_add(self, session_id: str, content: str, priority: str = "medium") -> str:
        """Add a new todo item to the task list.

        Args:
            session_id: The current session ID.
            content: The todo item description.
            priority: Priority level (low, medium, high). Defaults to medium.

        Returns:
            Confirmation message with the added todo.
        """
        event = Event(
            type=EventType.TODO_ADD,
            session_id=session_id,
            payload={"content": content, "priority": priority},
            source="todo_skill",
        )
        await self.event_bus.publish(event)
        state = self.event_bus.build_state(session_id)
        new_todo = state["todos"][-1] if state["todos"] else None
        if new_todo:
            return json.dumps({
                "status": "added",
                "todo": new_todo,
                "total_count": len(state["todos"]),
            })
        return json.dumps({"status": "error", "message": "Failed to add todo"})

    async def todo_list(self, session_id: str) -> str:
        """List all todo items in the current session.

        Args:
            session_id: The current session ID.

        Returns:
            JSON list of all todo items.
        """
        state = self.event_bus.build_state(session_id)
        todos = state.get("todos", [])
        return json.dumps({
            "todos": todos,
            "total": len(todos),
            "pending": len([t for t in todos if t.get("status") == "pending"]),
            "done": len([t for t in todos if t.get("status") == "done"]),
        })

    async def todo_update(
        self, session_id: str, todo_id: int, status: str = "", content: str = ""
    ) -> str:
        """Update an existing todo item.

        Args:
            session_id: The current session ID.
            todo_id: The ID of the todo to update.
            status: New status (pending, in_progress, done). Leave empty to keep current.
            content: New content. Leave empty to keep current.

        Returns:
            Updated todo item.
        """
        payload: Dict[str, Any] = {"todo_id": todo_id}
        if status:
            payload["status"] = status
        if content:
            payload["content"] = content

        event = Event(
            type=EventType.TODO_UPDATE,
            session_id=session_id,
            payload=payload,
            source="todo_skill",
        )
        await self.event_bus.publish(event)
        state = self.event_bus.build_state(session_id)
        updated = next((t for t in state["todos"] if t["id"] == todo_id), None)
        if updated:
            return json.dumps({"status": "updated", "todo": updated})
        return json.dumps({"status": "error", "message": f"Todo {todo_id} not found"})

    async def todo_delete(self, session_id: str, todo_id: int) -> str:
        """Delete a todo item.

        Args:
            session_id: The current session ID.
            todo_id: The ID of the todo to delete.

        Returns:
            Confirmation of deletion.
        """
        event = Event(
            type=EventType.TODO_DELETE,
            session_id=session_id,
            payload={"todo_id": todo_id},
            source="todo_skill",
        )
        await self.event_bus.publish(event)
        return json.dumps({"status": "deleted", "todo_id": todo_id})


class ResearchSkill(BaseSkill):
    """
    Deep research skill. Breaks down research queries into steps,
    executes them via sub-agents, and aggregates results.
    """

    name = "research"
    description = "Perform deep research on a topic with multi-step analysis"

    def get_tools(self) -> List[FunctionTool]:
        return [
            FunctionTool(self.research_start),
            FunctionTool(self.research_add_step),
            FunctionTool(self.research_get_progress),
        ]

    def get_system_prompt_fragment(self) -> str:
        return (
            "\n<skill_research>\n"
            "You have a Deep Research skill. Use these tools for research tasks:\n"
            "- research_start(session_id, topic, steps): Start a research task with planned steps\n"
            "- research_add_step(session_id, step_name, result): Record a research step result\n"
            "- research_get_progress(session_id): Get current research progress and findings\n"
            "When the user asks for deep research, analysis, or investigation:\n"
            "1. Break the topic into research steps\n"
            "2. Execute each step and record findings\n"
            "3. Synthesize a final report\n"
            "</skill_research>\n"
        )

    async def research_start(self, session_id: str, topic: str, steps: str = "") -> str:
        """Start a new research task.

        Args:
            session_id: The current session ID.
            topic: The research topic or question.
            steps: Comma-separated list of planned research steps.

        Returns:
            Research plan confirmation.
        """
        step_list = [s.strip() for s in steps.split(",") if s.strip()] if steps else []
        event = Event(
            type=EventType.RESEARCH_START,
            session_id=session_id,
            payload={"topic": topic, "planned_steps": step_list},
            source="research_skill",
        )
        await self.event_bus.publish(event)
        return json.dumps({
            "status": "started",
            "topic": topic,
            "planned_steps": step_list,
        })

    async def research_add_step(self, session_id: str, step_name: str, result: str) -> str:
        """Record a research step result.

        Args:
            session_id: The current session ID.
            step_name: Name/description of this research step.
            result: The findings from this step.

        Returns:
            Step recording confirmation.
        """
        event = Event(
            type=EventType.RESEARCH_STEP,
            session_id=session_id,
            payload={"step": step_name, "result": result},
            source="research_skill",
        )
        await self.event_bus.publish(event)
        state = self.event_bus.build_state(session_id)
        return json.dumps({
            "status": "recorded",
            "step": step_name,
            "total_steps_completed": len(state["research"]),
        })

    async def research_get_progress(self, session_id: str) -> str:
        """Get current research progress and all findings.

        Args:
            session_id: The current session ID.

        Returns:
            Research progress with all step results.
        """
        state = self.event_bus.build_state(session_id)
        research = state.get("research", [])
        # Find the research_start event for topic info
        start_events = self.event_bus.get_events_by_type(session_id, EventType.RESEARCH_START)
        topic = start_events[-1].payload.get("topic", "unknown") if start_events else "unknown"
        return json.dumps({
            "topic": topic,
            "steps_completed": len(research),
            "findings": research,
        })


# Registry of built-in skills
BUILTIN_SKILLS: Dict[str, Type[BaseSkill]] = {
    "todo": TodoSkill,
    "research": ResearchSkill,
}


class SkillManager:
    """
    Manages skill lifecycle and provides tools to the SuperAgent.
    Skills are registered by name and instantiated with the shared EventBus.
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._skills: Dict[str, BaseSkill] = {}

    def register(self, skill_name: str, skill_class: Optional[Type[BaseSkill]] = None):
        """Register a skill by name. Uses builtin registry if no class provided."""
        if skill_class is None:
            skill_class = BUILTIN_SKILLS.get(skill_name)
        if skill_class is None:
            raise ValueError(f"Unknown skill: {skill_name}")
        self._skills[skill_name] = skill_class(self.event_bus)
        logger.info(f"[SkillManager] Registered skill: {skill_name}")

    def register_all(self, skill_names: List[str]):
        for name in skill_names:
            self.register(name)

    def get_all_tools(self) -> List[FunctionTool]:
        """Get all ADK FunctionTools from all registered skills."""
        tools = []
        for skill in self._skills.values():
            tools.extend(skill.get_tools())
        return tools

    def get_system_prompt(self) -> str:
        """Build combined system prompt fragment from all skills."""
        fragments = []
        for skill in self._skills.values():
            fragments.append(skill.get_system_prompt_fragment())
        return "\n".join(fragments)

    def get_skill(self, name: str) -> Optional[BaseSkill]:
        return self._skills.get(name)

    @property
    def skill_names(self) -> List[str]:
        return list(self._skills.keys())
