"""
ILUMINATY - F17: Collaborative Mode
======================================
Multiples personas ven la misma pantalla en tiempo real.
Anotaciones compartidas entre todos los participantes.

Casos de uso:
  - Pair programming: 2 devs ven el mismo IDE
  - Tech support: el agente ve la pantalla del cliente
  - Teaching: profesor comparte pantalla, alumnos anotan
  - Code review: reviewer ve tu pantalla y marca issues

Arquitectura:
  Host (comparte pantalla) → Room → Viewers (ven + anotan)
  Todos los viewers ven las anotaciones de todos.
  El host ve las anotaciones de los viewers.
"""

import time
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Callable


@dataclass
class Participant:
    """Un participante en una sesion colaborativa."""
    id: str
    name: str
    role: str             # "host", "viewer"
    joined_at: float
    color: str            # color de sus anotaciones
    is_active: bool = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "color": self.color,
            "joined": time.strftime("%H:%M:%S", time.localtime(self.joined_at)),
            "active": self.is_active,
        }


@dataclass
class SharedAnnotation:
    """Una anotacion visible para todos."""
    id: str
    author_id: str
    author_name: str
    type: str
    x: int
    y: int
    width: int
    height: int
    color: str
    text: str
    timestamp: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "author": self.author_name,
            "type": self.type,
            "x": self.x, "y": self.y,
            "width": self.width, "height": self.height,
            "color": self.color,
            "text": self.text,
            "time": time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
        }


@dataclass
class CollabRoom:
    """Una sala de colaboracion."""
    id: str
    name: str
    host: Participant
    created_at: float
    viewers: list[Participant] = field(default_factory=list)
    annotations: list[SharedAnnotation] = field(default_factory=list)
    max_viewers: int = 10
    is_active: bool = True

    @property
    def participant_count(self) -> int:
        return 1 + len(self.viewers)  # host + viewers

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host.to_dict(),
            "viewers": [v.to_dict() for v in self.viewers],
            "participants": self.participant_count,
            "annotations": len(self.annotations),
            "created": time.strftime("%H:%M:%S", time.localtime(self.created_at)),
            "active": self.is_active,
        }


# Colores para asignar a participantes
PARTICIPANT_COLORS = [
    "#FF0000", "#00FF88", "#FFFF00", "#00AAFF",
    "#FF00FF", "#FF8800", "#88FF00", "#0088FF",
    "#FF0088", "#00FFFF",
]


class CollaborativeManager:
    """
    Gestiona sesiones colaborativas.
    Multiples personas pueden ver la misma pantalla
    y compartir anotaciones en tiempo real.
    """

    def __init__(self):
        self._rooms: dict[str, CollabRoom] = {}
        self._color_index: int = 0

    def _next_color(self) -> str:
        color = PARTICIPANT_COLORS[self._color_index % len(PARTICIPANT_COLORS)]
        self._color_index += 1
        return color

    def create_room(self, host_name: str, room_name: str = "") -> dict:
        """
        Crea una sala de colaboracion. Retorna room info + invite link.
        """
        room_id = secrets.token_urlsafe(8)
        host = Participant(
            id=secrets.token_urlsafe(6),
            name=host_name,
            role="host",
            joined_at=time.time(),
            color=self._next_color(),
        )

        room = CollabRoom(
            id=room_id,
            name=room_name or f"{host_name}'s screen",
            host=host,
            created_at=time.time(),
        )

        self._rooms[room_id] = room

        return {
            "room_id": room_id,
            "host_id": host.id,
            "invite_code": room_id,
            "room": room.to_dict(),
        }

    def join_room(self, room_id: str, viewer_name: str) -> Optional[dict]:
        """Un viewer se une a una sala."""
        room = self._rooms.get(room_id)
        if not room or not room.is_active:
            return None

        if room.participant_count >= room.max_viewers + 1:
            return None

        viewer = Participant(
            id=secrets.token_urlsafe(6),
            name=viewer_name,
            role="viewer",
            joined_at=time.time(),
            color=self._next_color(),
        )

        room.viewers.append(viewer)

        return {
            "viewer_id": viewer.id,
            "color": viewer.color,
            "room": room.to_dict(),
        }

    def leave_room(self, room_id: str, participant_id: str):
        """Un participante sale de la sala."""
        room = self._rooms.get(room_id)
        if not room:
            return

        room.viewers = [v for v in room.viewers if v.id != participant_id]

        # Si el host se va, cerrar la sala
        if room.host.id == participant_id:
            room.is_active = False

    def add_annotation(self, room_id: str, author_id: str,
                       type: str, x: int, y: int,
                       width: int = 100, height: int = 50,
                       text: str = "") -> Optional[dict]:
        """Agrega una anotacion compartida."""
        room = self._rooms.get(room_id)
        if not room or not room.is_active:
            return None

        # Find author
        author = None
        if room.host.id == author_id:
            author = room.host
        else:
            for v in room.viewers:
                if v.id == author_id:
                    author = v
                    break

        if not author:
            return None

        ann = SharedAnnotation(
            id=secrets.token_urlsafe(6),
            author_id=author.id,
            author_name=author.name,
            type=type,
            x=x, y=y,
            width=width, height=height,
            color=author.color,
            text=text,
            timestamp=time.time(),
        )

        room.annotations.append(ann)

        # Keep last 50 annotations
        if len(room.annotations) > 50:
            room.annotations = room.annotations[-50:]

        return ann.to_dict()

    def get_room(self, room_id: str) -> Optional[dict]:
        """Info de una sala."""
        room = self._rooms.get(room_id)
        if not room:
            return None
        return room.to_dict()

    def get_annotations(self, room_id: str) -> list[dict]:
        """Todas las anotaciones de una sala."""
        room = self._rooms.get(room_id)
        if not room:
            return []
        return [a.to_dict() for a in room.annotations]

    def clear_annotations(self, room_id: str, author_id: Optional[str] = None):
        """Limpia anotaciones. Si se pasa author_id, solo las de ese autor."""
        room = self._rooms.get(room_id)
        if not room:
            return
        if author_id:
            room.annotations = [a for a in room.annotations if a.author_id != author_id]
        else:
            room.annotations.clear()

    def close_room(self, room_id: str):
        """Cierra una sala."""
        room = self._rooms.get(room_id)
        if room:
            room.is_active = False

    def list_rooms(self) -> list[dict]:
        """Lista todas las salas activas."""
        return [r.to_dict() for r in self._rooms.values() if r.is_active]

    @property
    def stats(self) -> dict:
        active = [r for r in self._rooms.values() if r.is_active]
        return {
            "active_rooms": len(active),
            "total_participants": sum(r.participant_count for r in active),
            "total_annotations": sum(len(r.annotations) for r in active),
        }
