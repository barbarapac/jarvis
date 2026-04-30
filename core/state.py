"""Persistência simples de estado entre execuções do Jarvis.

Cada watcher armazena seus IDs já vistos sob uma chave própria.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock


class WatcherState:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._data: dict[str, list[int | str]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open(encoding="utf-8") as f:
                self._data = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._data = {}

    def has_seen(self, namespace: str, item_id: int | str) -> bool:
        with self._lock:
            return item_id in self._data.get(namespace, [])

    def mark_seen(self, namespace: str, item_id: int | str) -> None:
        with self._lock:
            bucket = self._data.setdefault(namespace, [])
            if item_id not in bucket:
                bucket.append(item_id)

    def is_namespace_initialized(self, namespace: str) -> bool:
        """True se já vimos algo nesse namespace antes (não é primeira execução)."""
        with self._lock:
            return namespace in self._data

    def initialize_namespace(self, namespace: str) -> None:
        """Garante que o namespace existe (mesmo vazio)."""
        with self._lock:
            self._data.setdefault(namespace, [])

    def save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            tmp.replace(self._path)
