"""Listener de hotkey global via pynput.

A combinação roda em thread daemon. O callback é envolvido em try/except
pra não derrubar a thread se o handler do usuário falhar.
"""

from __future__ import annotations

from typing import Callable

from pynput import keyboard


class HotkeyListener:
    def __init__(self, combo: str, callback: Callable[[], None]) -> None:
        self._combo = combo
        self._callback = callback
        self._listener: keyboard.GlobalHotKeys | None = None

    @property
    def combo(self) -> str:
        return self._combo

    def start(self) -> None:
        self._listener = keyboard.GlobalHotKeys({self._combo: self._safe_call})
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _safe_call(self) -> None:
        try:
            self._callback()
        except Exception as e:
            print(f"[hotkey] erro no callback: {e!r}")
