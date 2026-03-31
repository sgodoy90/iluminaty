"""
ILUMINATY - Multi-Agent Workbench
==================================
Foundation for multiple AI agents with different roles connecting simultaneously.

Roles:
  OBSERVER  — watches all monitors, generates perception events (read-only)
  PLANNER   — receives perception + context, plans tasks (read + write plans)
  EXECUTOR  — executes actions on screen (click, type, navigate)
  VERIFIER  — checks results after execution (read-only + verification)

Architecture:
  AgentSession     — per-agent identity, role, autonomy, monitor subscriptions
  AgentMessageBus  — in-memory async queue between agents
  AgentCoordinator — lifecycle, orchestration, perception routing, tool scoping
"""

import asyncio
import time
import uuid
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AgentRole(Enum):
    OBSERVER = "observer"
    PLANNER = "planner"
    EXECUTOR = "executor"
    VERIFIER = "verifier"


@dataclass
class AgentSession:
    """Per-agent identity and configuration."""
    agent_id: str
    name: str
    role: AgentRole
    autonomy: str = "suggest"  # suggest, confirm, auto
    connected_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    monitors: list[int] = field(default_factory=list)  # [] = all monitors
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role.value,
            "autonomy": self.autonomy,
            "connected_at": self.connected_at,
            "last_heartbeat": self.last_heartbeat,
            "monitors": self.monitors,
            "uptime_s": round(time.time() - self.connected_at, 1),
            "metadata": self.metadata,
        }


# ─── Tool scoping per role ───

OBSERVER_TOOLS = frozenset({
    "see_screen", "see_changes", "read_screen_text", "perception",
    "perception_world", "perception_trace",
    "screen_status", "get_context", "get_audio_level", "token_status",
    "set_token_mode", "set_token_budget",
})

PLANNER_TOOLS = OBSERVER_TOOLS | frozenset({
    "action_precheck",
    "list_windows", "find_ui_element", "monitor_info", "see_monitor",
})

EXECUTOR_TOOLS = frozenset({
    "do_action", "raw_action", "action_precheck", "verify_action",
    "set_operating_mode",
    "click_element", "type_text", "run_command",
    "keyboard", "scroll", "click_screen", "browser_navigate",
    "focus_window", "read_file", "write_file", "get_clipboard",
}) | OBSERVER_TOOLS

VERIFIER_TOOLS = OBSERVER_TOOLS | frozenset({
    "verify_action",
    "read_file", "get_clipboard", "find_ui_element", "list_windows",
})

ROLE_TOOLS: dict[AgentRole, frozenset[str]] = {
    AgentRole.OBSERVER: OBSERVER_TOOLS,
    AgentRole.PLANNER: PLANNER_TOOLS,
    AgentRole.EXECUTOR: EXECUTOR_TOOLS,
    AgentRole.VERIFIER: VERIFIER_TOOLS,
}


# ─── Message Bus ───

@dataclass
class AgentMessage:
    """Inter-agent message."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    from_agent: str = ""
    to_agent: str = ""  # empty = broadcast
    msg_type: str = ""  # plan, action, verification, perception, heartbeat
    payload: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "from": self.from_agent,
            "to": self.to_agent,
            "type": self.msg_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


class AgentMessageBus:
    """
    In-memory message queue between agents.
    Each agent has its own inbox (deque).
    Supports direct messages and broadcasts.
    """

    def __init__(self, max_per_inbox: int = 100):
        self._inboxes: dict[str, deque[AgentMessage]] = {}
        self._max = max_per_inbox
        self._lock = threading.Lock()

    def register(self, agent_id: str) -> None:
        with self._lock:
            self._inboxes[agent_id] = deque(maxlen=self._max)

    def unregister(self, agent_id: str) -> None:
        with self._lock:
            self._inboxes.pop(agent_id, None)

    def send(self, from_id: str, to_id: str, msg_type: str, payload: dict) -> AgentMessage:
        """Send direct message to a specific agent."""
        msg = AgentMessage(from_agent=from_id, to_agent=to_id,
                           msg_type=msg_type, payload=payload)
        with self._lock:
            inbox = self._inboxes.get(to_id)
            if inbox is not None:
                inbox.append(msg)
        return msg

    def broadcast(self, from_id: str, msg_type: str, payload: dict) -> AgentMessage:
        """Broadcast message to all agents except sender."""
        msg = AgentMessage(from_agent=from_id, to_agent="*",
                           msg_type=msg_type, payload=payload)
        with self._lock:
            for aid, inbox in self._inboxes.items():
                if aid != from_id:
                    inbox.append(msg)
        return msg

    def receive(self, agent_id: str, max_messages: int = 10) -> list[AgentMessage]:
        """Poll messages for an agent (non-blocking)."""
        with self._lock:
            inbox = self._inboxes.get(agent_id)
            if not inbox:
                return []
            messages = []
            while inbox and len(messages) < max_messages:
                messages.append(inbox.popleft())
            return messages

    def pending_count(self, agent_id: str) -> int:
        with self._lock:
            inbox = self._inboxes.get(agent_id)
            return len(inbox) if inbox else 0


# ─── Perception Stream ───

class PerceptionStream:
    """Per-agent filtered perception event stream."""

    def __init__(self, agent_id: str, role: AgentRole,
                 monitors: list[int], max_events: int = 50):
        self.agent_id = agent_id
        self.role = role
        self.monitors = monitors  # [] = all
        self._events: deque = deque(maxlen=max_events)
        self._lock = threading.Lock()

    def push(self, event) -> bool:
        """Push a perception event if it matches this agent's filters."""
        # Monitor filter
        if self.monitors and event.monitor not in self.monitors:
            return False

        # Role-based filtering
        if self.role == AgentRole.PLANNER:
            # Planner only gets composites and high-importance events
            if event.event_type != "composite" and event.importance < 0.5:
                return False
        elif self.role == AgentRole.EXECUTOR:
            # Executor gets action-relevant events only
            if event.importance < 0.3:
                return False

        with self._lock:
            self._events.append(event)
        return True

    def get_events(self, max_count: int = 20) -> list:
        with self._lock:
            return list(self._events)[-max_count:]

    def drain(self, max_count: int = 20) -> list:
        """Get and remove events."""
        with self._lock:
            events = []
            while self._events and len(events) < max_count:
                events.append(self._events.popleft())
            return events


# ─── Agent Coordinator ───

class AgentCoordinator:
    """
    Manages agent lifecycle and orchestration.

    - Register/unregister agents with roles
    - Route perception events to subscribed agents
    - Enforce tool scoping per role
    - Inter-agent messaging
    - Heartbeat-based session cleanup (60s timeout)
    """

    HEARTBEAT_TIMEOUT = 60.0

    def __init__(self):
        self._sessions: dict[str, AgentSession] = {}
        self._message_bus = AgentMessageBus()
        self._perception_streams: dict[str, PerceptionStream] = {}
        self._lock = threading.Lock()

    def register(self, name: str, role: str, autonomy: str = "suggest",
                 monitors: Optional[list[int]] = None,
                 metadata: Optional[dict] = None) -> AgentSession:
        """Register a new agent. Returns AgentSession with generated ID."""
        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        agent_role = AgentRole(role)

        # Default autonomy per role
        if autonomy == "suggest":
            role_autonomy = {
                AgentRole.OBSERVER: "suggest",
                AgentRole.PLANNER: "suggest",
                AgentRole.EXECUTOR: "confirm",
                AgentRole.VERIFIER: "suggest",
            }
            autonomy = role_autonomy.get(agent_role, "suggest")

        session = AgentSession(
            agent_id=agent_id,
            name=name,
            role=agent_role,
            autonomy=autonomy,
            monitors=monitors or [],
            metadata=metadata or {},
        )

        with self._lock:
            self._sessions[agent_id] = session
            self._message_bus.register(agent_id)
            self._perception_streams[agent_id] = PerceptionStream(
                agent_id=agent_id, role=agent_role, monitors=monitors or [],
            )

        return session

    def unregister(self, agent_id: str) -> bool:
        with self._lock:
            if agent_id not in self._sessions:
                return False
            del self._sessions[agent_id]
            self._message_bus.unregister(agent_id)
            self._perception_streams.pop(agent_id, None)
            return True

    def heartbeat(self, agent_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(agent_id)
            if session:
                session.last_heartbeat = time.time()
                return True
            return False

    def get_session(self, agent_id: str) -> Optional[AgentSession]:
        return self._sessions.get(agent_id)

    def list_sessions(self) -> list[AgentSession]:
        return list(self._sessions.values())

    def get_allowed_tools(self, agent_id: str) -> frozenset[str]:
        """Get the set of tools this agent is allowed to use."""
        session = self._sessions.get(agent_id)
        if not session:
            return frozenset()
        return ROLE_TOOLS.get(session.role, OBSERVER_TOOLS)

    def is_tool_allowed(self, agent_id: str, tool_name: str) -> bool:
        return tool_name in self.get_allowed_tools(agent_id)

    # ─── Perception routing ───

    def push_perception_event(self, event) -> int:
        """Fan-out perception event to all subscribed agents. Returns count."""
        count = 0
        for stream in self._perception_streams.values():
            if stream.push(event):
                count += 1
        return count

    def get_perception_events(self, agent_id: str, max_count: int = 20) -> list:
        stream = self._perception_streams.get(agent_id)
        if stream:
            return stream.get_events(max_count)
        return []

    def drain_perception_events(self, agent_id: str, max_count: int = 20) -> list:
        stream = self._perception_streams.get(agent_id)
        if stream:
            return stream.drain(max_count)
        return []

    # ─── Messaging ───

    def send_message(self, from_id: str, to_id: str,
                     msg_type: str, payload: dict) -> Optional[AgentMessage]:
        if from_id not in self._sessions:
            return None
        if to_id != "*" and to_id not in self._sessions:
            return None
        if to_id == "*":
            return self._message_bus.broadcast(from_id, msg_type, payload)
        return self._message_bus.send(from_id, to_id, msg_type, payload)

    def get_messages(self, agent_id: str, max_count: int = 10) -> list[AgentMessage]:
        return self._message_bus.receive(agent_id, max_count)

    # ─── Cleanup ───

    def reap_stale_sessions(self) -> list[str]:
        """Remove sessions with no heartbeat for HEARTBEAT_TIMEOUT seconds."""
        now = time.time()
        stale = []
        with self._lock:
            for aid, session in list(self._sessions.items()):
                if (now - session.last_heartbeat) > self.HEARTBEAT_TIMEOUT:
                    stale.append(aid)
            for aid in stale:
                self._sessions.pop(aid, None)
                self._message_bus.unregister(aid)
                self._perception_streams.pop(aid, None)
        return stale

    def to_dict(self) -> dict:
        return {
            "agents": [s.to_dict() for s in self._sessions.values()],
            "count": len(self._sessions),
            "roles": {
                role.value: sum(1 for s in self._sessions.values() if s.role == role)
                for role in AgentRole
            },
        }
