"""Interface base para watchers (fontes de eventos)."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod


class Watcher(ABC):
    name: str

    def __init__(self, poll_interval_seconds: int) -> None:
        self._poll_interval = poll_interval_seconds
        self._last_poll: float = 0.0

    def tick(self) -> None:
        """Chamado pelo event loop. No-op se ainda não é hora de pollar."""
        now = time.monotonic()
        if now - self._last_poll < self._poll_interval:
            return
        self._last_poll = now
        try:
            self.poll()
        except Exception as e:
            # Watcher não deve quebrar o event loop. Loga e segue.
            print(f"[{self.name}] erro no poll: {e!r}")

    @abstractmethod
    def poll(self) -> None: ...
