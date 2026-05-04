"""Gerenciamento do .env via API (escopo localhost).

Catálogo hardcoded documenta as secrets que o Jarvis usa, pra que servem
e onde obter cada uma. A UI consulta `.status()` (apenas metadados, nunca
valores) e faz `.update()` pra escrever no `.env`.

Decisões intencionais:
- O `.env` é a fonte da verdade pra runtime — após salvar, atualizamos
  também `os.environ` pra que componentes que não foram reiniciados (ex:
  o servidor FastAPI em si) enxerguem o novo valor. Mas componentes que
  cacheiam (LLMClient, watchers) ainda exigem restart — a UI avisa.
- Preservamos comentários e ordem do arquivo. Linhas KEY=value que estão
  no catálogo são editadas in-place; chaves novas vão pro fim.
- Escrita atômica via tmp + replace, com lock pra evitar corrida em
  patches concorrentes (raro em uso pessoal, mas barato).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any


@dataclass(frozen=True)
class SecretSpec:
    key: str
    label: str
    description: str
    docs_url: str = ""
    sensitive: bool = True
    required: bool = False


# Catálogo de secrets que o Jarvis conhece. Acrescentar aqui quando uma
# nova feature precisar de credencial — a UI puxa direto desta lista.
CATALOG: tuple[SecretSpec, ...] = (
    SecretSpec(
        key="ANTHROPIC_API_KEY",
        label="Anthropic API Key",
        description=(
            "Necessária se o motor de raciocínio estiver em provider=anthropic. "
            "Com Ollama (provider=ollama), pode ficar vazia."
        ),
        docs_url="https://console.anthropic.com/settings/keys",
    ),
    SecretSpec(
        key="GITLAB_TOKEN",
        label="GitLab Personal Access Token",
        description=(
            "Pro watcher de MRs e o tool de revisão. "
            "Scope mínimo: read_api, read_repository."
        ),
        docs_url="https://gitlab.com/-/user_settings/personal_access_tokens",
    ),
    SecretSpec(
        key="GITLAB_URL",
        label="GitLab Base URL",
        description="https://gitlab.com (público) ou URL da instância self-hosted da Softplan.",
        sensitive=False,
    ),
    SecretSpec(
        key="SPOTIFY_CLIENT_ID",
        label="Spotify Client ID",
        description="Crie um app em Spotify Developer Dashboard e copie o Client ID.",
        docs_url="https://developer.spotify.com/dashboard",
    ),
    SecretSpec(
        key="SPOTIFY_CLIENT_SECRET",
        label="Spotify Client Secret",
        description="No mesmo app do Spotify Developer Dashboard.",
        docs_url="https://developer.spotify.com/dashboard",
    ),
    SecretSpec(
        key="SPOTIFY_REDIRECT_URI",
        label="Spotify Redirect URI",
        description=(
            "Use http://127.0.0.1:8888/callback. Adicione exatamente isso "
            "no Spotify Dashboard → Edit settings → Redirect URIs."
        ),
        sensitive=False,
    ),
)


def get_spec(key: str) -> SecretSpec | None:
    for s in CATALOG:
        if s.key == key:
            return s
    return None


class SecretsManager:
    def __init__(self, env_path: Path) -> None:
        self._path = env_path
        self._lock = Lock()

    def get_value(self, key: str) -> str:
        """Retorna o valor cru de UMA secret específica.

        Usado APENAS pelo endpoint /api/secrets/{key}/reveal, que é
        localhost-only. Levanta ValueError pra chave fora do catálogo.
        """
        spec = get_spec(key)
        if spec is None:
            raise ValueError(f"chave desconhecida: {key}")
        existing = self._read_existing()
        return existing.get(key) or os.environ.get(key, "")

    def status(self) -> list[dict[str, Any]]:
        """Lista metadados + status de cada secret. Nunca expõe valor cru
        de chaves marcadas sensitive — só preview ('••••••XXXX')."""
        existing = self._read_existing()
        out: list[dict[str, Any]] = []
        for spec in CATALOG:
            value = existing.get(spec.key) or os.environ.get(spec.key, "")
            out.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "description": spec.description,
                    "docs_url": spec.docs_url,
                    "sensitive": spec.sensitive,
                    "required": spec.required,
                    "defined": bool(value.strip()),
                    "preview": _preview(value, sensitive=spec.sensitive),
                }
            )
        return out

    def update(self, updates: dict[str, str | None]) -> None:
        """Aplica patches no .env. value=None ou "" remove a chave.

        Levanta ValueError pra chaves fora do catálogo — evita escrever
        coisa aleatória no .env via API.
        """
        valid = {s.key for s in CATALOG}
        for k in updates:
            if k not in valid:
                raise ValueError(f"chave desconhecida: {k}")

        with self._lock:
            lines = self._read_lines()
            seen: set[str] = set()
            new_lines: list[str] = []

            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    new_lines.append(line)
                    continue
                key = stripped.split("=", 1)[0].strip()
                if key in updates:
                    seen.add(key)
                    new_value = updates[key]
                    if not new_value:
                        # Remove a linha (chave excluída).
                        continue
                    new_lines.append(f"{key}={_format(new_value)}\n")
                    continue
                new_lines.append(line)

            # Chaves novas (ainda não no .env) vão pro fim.
            for key, value in updates.items():
                if key in seen or not value:
                    continue
                new_lines.append(f"{key}={_format(value)}\n")

            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text("".join(new_lines), encoding="utf-8")
            os.replace(tmp, self._path)

            # Sincroniza ENV do processo. Componentes que cacheiam clientes
            # (LLMClient, watchers, narrator) ainda precisam de restart —
            # a UI mostra esse aviso depois do save.
            for key, value in updates.items():
                if not value:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    # ---------- Internals ----------

    def _read_lines(self) -> list[str]:
        if not self._path.is_file():
            return []
        return self._path.read_text(encoding="utf-8").splitlines(keepends=True)

    def _read_existing(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in self._read_lines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            out[k.strip()] = _unquote(v.strip())
        return out


def _preview(value: str, *, sensitive: bool) -> str:
    value = value.strip()
    if not value:
        return ""
    if not sensitive:
        return value
    if len(value) <= 8:
        return "••••••••"
    return f"••••{value[-4:]}"


def _format(value: str) -> str:
    """Aspas duplas se tiver espaço/#; escapa aspas internas."""
    if any(c in value for c in ' \t#"'):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value
