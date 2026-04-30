"""Spotify ducking — abaixa o volume da música enquanto o Jarvis fala.

Inscreve-se nos eventos SPEAKING_STARTED/SPEAKING_ENDED do EventBus.
- Em STARTED: lê volume atual e baixa pra `duck_volume_percent`.
- Em ENDED: restaura o volume original.

Tolerante a falhas: se nada estiver tocando, se for conta Free (403) ou
se a API do Spotify cair, apenas loga e segue. Nunca propaga exceção
pro event bus.
"""

from __future__ import annotations

import threading
from typing import Optional

from core.event_bus import Event, EventBus, EventType
from tools.spotify import SpotifyTool


class SpotifyDucker:
    def __init__(
        self,
        spotify: SpotifyTool,
        event_bus: EventBus,
        duck_volume_percent: int = 25,
    ) -> None:
        self._spotify = spotify
        self._duck_volume = max(0, min(100, duck_volume_percent))
        self._lock = threading.Lock()
        self._original_volume: Optional[int] = None
        self._original_device_id: Optional[str] = None
        event_bus.subscribe(self._on_event)

    def _on_event(self, event: Event) -> None:
        if event.type == EventType.SPEAKING_STARTED:
            self._duck()
        elif event.type == EventType.SPEAKING_ENDED:
            self._restore()

    def _duck(self) -> None:
        with self._lock:
            if self._original_volume is not None:
                # Já em modo duck (fala encavalou outra) — mantém o original
                # capturado antes pra restaurar corretamente no fim.
                return
            try:
                device = self._spotify.active_device()
                if not device:
                    return
                current = device.get("volume_percent")
                if current is None or current <= self._duck_volume:
                    return
                self._original_volume = int(current)
                self._original_device_id = device.get("id")
                self._spotify.set_volume(self._duck_volume, device_id=self._original_device_id)
            except Exception as e:
                print(f"[duck] falha abaixando volume: {e!r}")
                self._original_volume = None
                self._original_device_id = None

    def _restore(self) -> None:
        with self._lock:
            if self._original_volume is None:
                return
            try:
                self._spotify.set_volume(self._original_volume, device_id=self._original_device_id)
            except Exception as e:
                print(f"[duck] falha restaurando volume: {e!r}")
            finally:
                self._original_volume = None
                self._original_device_id = None
