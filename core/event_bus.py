"""EventBus pub/sub interno — usado pra alimentar a UI em tempo real.

Publishers (narrator, watchers, voice commander) emitem eventos. Subscribers
(o servidor FastAPI) recebem e repassam via WebSocket.

Thread-safe: subscribers são chamados sob lock. Falhas em subscribers não
derrubam o publisher.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class EventType(str, Enum):
    BOOTED = "booted"
    STATUS = "status"  # idle | listening | speaking | working
    SPEAKING_STARTED = "speaking_started"
    SPEAKING_ENDED = "speaking_ended"
    LISTENING_STARTED = "listening_started"
    LISTENING_ENDED = "listening_ended"
    USER_VOICE_TRANSCRIBED = "user_voice_transcribed"
    USER_TEXT_INPUT = "user_text_input"
    GITLAB_ANNOUNCE = "gitlab_announce"
    COMMAND_DISPATCHED = "command_dispatched"
    REVIEW_STARTED = "review_started"
    REVIEW_FINISHED = "review_finished"
    LOG = "log"  # mensagem genérica


@dataclass(frozen=True)
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: Subscriber) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: Subscriber) -> None:
        with self._lock:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

    def publish(self, type_: EventType, **data: Any) -> None:
        event = Event(type=type_, data=data)
        with self._lock:
            subs = list(self._subscribers)
        for sub in subs:
            try:
                sub(event)
            except Exception as e:
                print(f"[event_bus] subscriber falhou: {e!r}")
