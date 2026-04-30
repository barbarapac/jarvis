"""Lê e escreve config/jarvis.yaml preservando comentários e ordem.

Usa ruamel.yaml em modo round-trip. Operações são atômicas (escreve em
arquivo temporário e renomeia) pra não corromper o YAML em caso de falha.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


class ConfigManager:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._yaml = YAML()
        self._yaml.preserve_quotes = True
        self._yaml.indent(mapping=2, sequence=4, offset=2)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any]:
        with self._lock:
            with self._path.open(encoding="utf-8") as f:
                data = self._yaml.load(f) or {}
            return _to_plain(data)

    def load_raw(self):
        """Carrega preservando o objeto ruamel (com comentários) — para escrita."""
        with self._lock:
            with self._path.open(encoding="utf-8") as f:
                return self._yaml.load(f)

    def save(self, data: Any) -> None:
        """Persiste atomicamente. `data` pode ser dict puro ou objeto ruamel."""
        with self._lock:
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                self._yaml.dump(data, f)
            os.replace(tmp, self._path)

    def patch(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Aplica updates aninhados preservando comentários do YAML original."""
        with self._lock:
            with self._path.open(encoding="utf-8") as f:
                doc = self._yaml.load(f) or {}
            _deep_merge(doc, updates)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                self._yaml.dump(doc, f)
            os.replace(tmp, self._path)
            return _to_plain(doc)


def _to_plain(obj: Any) -> Any:
    """Converte estruturas ruamel (CommentedMap/Seq) em dict/list nativos."""
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain(v) for v in obj]
    return obj


def _deep_merge(target: Any, src: dict[str, Any]) -> None:
    """Merge recursivo: dicts viram merge; resto sobrescreve."""
    for key, value in src.items():
        if (
            key in target
            and isinstance(target[key], dict)
            and isinstance(value, dict)
        ):
            _deep_merge(target[key], value)
        else:
            target[key] = value
