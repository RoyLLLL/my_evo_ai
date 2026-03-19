"""
EventBus - Session-scoped event sourcing system.
Events are persisted in ADK session state for replay/time-travel.
"""

import time
import uuid
import asyncio
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional
from dataclasses import dataclass, field, asdict
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class EventType(str, Enum):
    # Core lifecycle
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    SYSTEM_MESSAGE = "system_message"

    # Routing
    ROUTE_DECISION = "route_decision"
    SKILL_INVOKED = "skill_invoked"
    SKILL_COMPLETED = "skill_completed"

    # Todo
    TODO_ADD = "todo_add"
    TODO_UPDATE = "todo_update"
    TODO_DELETE = "todo_delete"
    TODO_LIST = "todo_list"

    # Research
    RESEARCH_START = "research_start"
    RESEARCH_STEP = "research_step"
    RESEARCH_COMPLETE = "research_complete"

    # Agent coordination
    AGENT_DELEGATED = "agent_delegated"
    AGENT_RESPONSE = "agent_response"
    ERROR = "error"


@dataclass
class Event:
    type: EventType
    session_id: str
    payload: Dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    source: str = "system"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        data["type"] = EventType(data["type"])
        return cls(**data)


# Type alias for event handlers
EventHandler = Callable[[Event], Coroutine[Any, Any, Optional[List[Event]]]]


class EventBus:
    """
    In-process async event bus with session-scoped event store.
    Supports pub/sub pattern for multi-agent coordination.
    Events are stored in ADK session state for persistence.
    """

    def __init__(self):
        self._handlers: Dict[EventType, List[EventHandler]] = {}
        self._events: Dict[str, List[Event]] = {}  # session_id -> events
        self._global_handlers: List[EventHandler] = []

    def subscribe(self, event_type: EventType, handler: EventHandler):
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler):
        self._global_handlers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler):
        if event_type in self._handlers:
            self._handlers[event_type] = [
                h for h in self._handlers[event_type] if h != handler
            ]

    async def publish(self, event: Event) -> List[Event]:
        """Publish event, store it, and dispatch to handlers. Returns new events produced."""
        # Store event
        if event.session_id not in self._events:
            self._events[event.session_id] = []
        self._events[event.session_id].append(event)

        logger.info(f"[EventBus] Published {event.type.value} for session {event.session_id}")

        # Collect new events from handlers
        new_events: List[Event] = []

        # Type-specific handlers
        handlers = self._handlers.get(event.type, [])
        for handler in handlers:
            try:
                result = await handler(event)
                if result:
                    new_events.extend(result)
            except Exception as e:
                logger.error(f"[EventBus] Handler error for {event.type.value}: {e}")
                new_events.append(Event(
                    type=EventType.ERROR,
                    session_id=event.session_id,
                    payload={"error": str(e), "source_event": event.event_id},
                    source="event_bus",
                ))

        # Global handlers
        for handler in self._global_handlers:
            try:
                result = await handler(event)
                if result:
                    new_events.extend(result)
            except Exception as e:
                logger.error(f"[EventBus] Global handler error: {e}")

        return new_events

    async def dispatch_chain(self, event: Event, max_depth: int = 10) -> List[Event]:
        """
        Publish an event and recursively dispatch any events produced by handlers.
        This is the core event-driven loop - events trigger handlers that produce more events.
        """
        all_events = [event]
        queue = [event]
        depth = 0

        while queue and depth < max_depth:
            current = queue.pop(0)
            new_events = await self.publish(current)
            for e in new_events:
                all_events.append(e)
                queue.append(e)
            depth += 1

        if depth >= max_depth:
            logger.warning(f"[EventBus] Max dispatch depth reached for session {event.session_id}")

        return all_events

    def get_events(self, session_id: str) -> List[Event]:
        return self._events.get(session_id, [])

    def get_events_by_type(self, session_id: str, event_type: EventType) -> List[Event]:
        return [e for e in self.get_events(session_id) if e.type == event_type]

    def build_state(self, session_id: str) -> Dict[str, Any]:
        """Build current state from events (event sourcing pattern)."""
        events = self.get_events(session_id)
        state = {
            "messages": [],
            "todos": [],
            "research": [],
            "route_history": [],
        }

        todo_counter = 0
        for e in events:
            if e.type == EventType.USER_MESSAGE:
                state["messages"].append({
                    "role": "user",
                    "content": e.payload.get("content", ""),
                })
            elif e.type == EventType.ASSISTANT_MESSAGE:
                state["messages"].append({
                    "role": "assistant",
                    "content": e.payload.get("content", ""),
                })
            elif e.type == EventType.TODO_ADD:
                todo_counter += 1
                state["todos"].append({
                    "id": todo_counter,
                    "content": e.payload.get("content", ""),
                    "status": "pending",
                    "created_at": e.timestamp,
                })
            elif e.type == EventType.TODO_UPDATE:
                todo_id = e.payload.get("todo_id")
                for todo in state["todos"]:
                    if todo["id"] == todo_id:
                        todo.update({
                            k: v for k, v in e.payload.items()
                            if k not in ("todo_id",)
                        })
            elif e.type == EventType.TODO_DELETE:
                todo_id = e.payload.get("todo_id")
                state["todos"] = [t for t in state["todos"] if t["id"] != todo_id]
            elif e.type == EventType.RESEARCH_STEP:
                state["research"].append(e.payload)
            elif e.type == EventType.ROUTE_DECISION:
                state["route_history"].append(e.payload)

        return state

    def serialize_events(self, session_id: str) -> List[dict]:
        """Serialize events for ADK session state persistence."""
        return [e.to_dict() for e in self.get_events(session_id)]

    def load_events(self, session_id: str, event_dicts: List[dict]):
        """Load events from ADK session state (replay)."""
        self._events[session_id] = [Event.from_dict(d) for d in event_dicts]

    def clear_session(self, session_id: str):
        self._events.pop(session_id, None)
