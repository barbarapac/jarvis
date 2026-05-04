"""Gerencia servidores MCP (Model Context Protocol) registrados pela usuária.

Estilo Claude Desktop: cada server tem `command`, `args`, `env`. A
configuração vive em `config/mcp_servers.json`. O manager mantém um pool
de conexões stdio e expõe a lista agregada de tools no formato esperado
pela Anthropic API (Claude tool-use).

Async por dentro (mcp Python SDK é async), com fachada síncrona usando
um event loop dedicado em thread separada — pra não ter que adoecer o
restante do código com `asyncio`.
"""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class _ServerRuntime:
    config: MCPServerConfig
    session: Any  # mcp.ClientSession
    tools: list[dict[str, Any]]  # já no formato Anthropic
    error: str | None = None


class MCPManager:
    """Pool de servidores MCP com fachada síncrona."""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._servers: dict[str, _ServerRuntime] = {}
        self._tool_owner: dict[str, str] = {}  # tool_name → server_name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._stack: AsyncExitStack | None = None

    # ---------- Persistência ----------

    def load_configs(self) -> list[MCPServerConfig]:
        if not self._config_path.exists():
            return []
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        servers = data.get("mcpServers") or {}
        result = []
        for name, cfg in servers.items():
            result.append(
                MCPServerConfig(
                    name=name,
                    command=cfg.get("command", ""),
                    args=list(cfg.get("args") or []),
                    env=dict(cfg.get("env") or {}),
                    enabled=bool(cfg.get("enabled", True)),
                )
            )
        return result

    def save_configs(self, configs: list[MCPServerConfig]) -> None:
        payload = {
            "mcpServers": {
                cfg.name: {
                    "command": cfg.command,
                    "args": cfg.args,
                    "env": cfg.env,
                    "enabled": cfg.enabled,
                }
                for cfg in configs
            }
        }
        tmp = self._config_path.with_suffix(self._config_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._config_path)

    # ---------- Lifecycle ----------

    def start(self) -> None:
        """Sobe um loop async em thread daemon e conecta os servidores ativos."""
        if self._loop_thread is not None:
            return
        ready = threading.Event()

        def runner() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            ready.set()
            self._loop.run_forever()

        self._loop_thread = threading.Thread(target=runner, daemon=True, name="mcp-loop")
        self._loop_thread.start()
        ready.wait(timeout=5.0)

        # Conecta servidores configurados.
        self.reload()

    def stop(self) -> None:
        if self._loop is None:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self._aclose_all(), self._loop)
            fut.result(timeout=5.0)
        except Exception as e:
            print(f"[mcp] erro fechando sessões: {e!r}")
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread:
            self._loop_thread.join(timeout=3.0)
        self._loop = None
        self._loop_thread = None

    def reload(self) -> dict[str, str]:
        """Reconecta a partir do JSON. Retorna {server_name: status_or_error}."""
        if self._loop is None:
            return {}
        configs = self.load_configs()
        fut = asyncio.run_coroutine_threadsafe(self._areconnect_all(configs), self._loop)
        try:
            return fut.result(timeout=20.0)
        except Exception as e:
            print(f"[mcp] reload falhou: {e!r}")
            return {"_error": repr(e)}

    # ---------- Acessores ----------

    def list_status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "name": rt.config.name,
                    "command": rt.config.command,
                    "args": rt.config.args,
                    "enabled": rt.config.enabled,
                    "connected": rt.error is None,
                    "error": rt.error,
                    "tools": [t["name"] for t in rt.tools],
                }
                for rt in self._servers.values()
            ]

    def list_tools_for_anthropic(self) -> list[dict[str, Any]]:
        """Lista de tools no formato esperado pelo SDK Anthropic.

        Dedupa por nome — a API da Anthropic rejeita o request inteiro se
        houver tools com nomes repetidos. Mantém a primeira ocorrência e
        loga as colisões pra ficar visível qual server foi ignorado.
        """
        with self._lock:
            tools: list[dict[str, Any]] = []
            seen: set[str] = set()
            for rt in self._servers.values():
                if rt.error is not None or not rt.config.enabled:
                    continue
                for t in rt.tools:
                    name = t["name"]
                    if name in seen:
                        print(
                            f"[mcp] tool duplicada ignorada: {name!r} "
                            f"de {rt.config.name!r}"
                        )
                        continue
                    seen.add(name)
                    tools.append(t)
            return tools

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Chama uma tool MCP pelo nome. Retorna texto agregado da resposta."""
        if self._loop is None:
            return "[mcp] loop não iniciado"
        with self._lock:
            owner = self._tool_owner.get(tool_name)
            if not owner or owner not in self._servers:
                return f"[mcp] tool desconhecida: {tool_name}"
            session = self._servers[owner].session
        fut = asyncio.run_coroutine_threadsafe(
            self._acall_tool(session, tool_name, arguments), self._loop
        )
        try:
            return fut.result(timeout=60.0)
        except Exception as e:
            return f"[mcp] erro chamando {tool_name}: {e!r}"

    # ---------- Async internals ----------

    async def _areconnect_all(
        self, configs: list[MCPServerConfig]
    ) -> dict[str, str]:
        await self._aclose_all()
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()

        statuses: dict[str, str] = {}
        new_servers: dict[str, _ServerRuntime] = {}
        new_owner: dict[str, str] = {}

        for cfg in configs:
            if not cfg.enabled:
                statuses[cfg.name] = "disabled"
                continue
            try:
                session, tools = await self._aconnect_one(cfg)
                rt = _ServerRuntime(config=cfg, session=session, tools=tools)
                new_servers[cfg.name] = rt
                for t in tools:
                    new_owner[t["name"]] = cfg.name
                statuses[cfg.name] = "ok"
            except Exception as e:
                statuses[cfg.name] = f"error: {e!r}"
                new_servers[cfg.name] = _ServerRuntime(
                    config=cfg, session=None, tools=[], error=repr(e)
                )

        with self._lock:
            self._servers = new_servers
            self._tool_owner = new_owner
        return statuses

    async def _aconnect_one(self, cfg: MCPServerConfig):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=cfg.command,
            args=cfg.args,
            env=cfg.env or None,
        )
        # `stdio_client` e `ClientSession` são context managers; mantemos vivos
        # via AsyncExitStack pra serem fechados juntos no shutdown.
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        listed = await session.list_tools()
        tools = []
        for t in listed.tools:
            tools.append(
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema or {"type": "object", "properties": {}},
                }
            )
        return session, tools

    async def _aclose_all(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.__aexit__(None, None, None)
            except Exception as e:
                print(f"[mcp] cleanup error: {e!r}")
            self._stack = None
        with self._lock:
            self._servers.clear()
            self._tool_owner.clear()

    async def _acall_tool(self, session, name: str, arguments: dict[str, Any]) -> str:
        result = await session.call_tool(name, arguments=arguments)
        # Concat blocks de texto da resposta MCP
        out_parts: list[str] = []
        for block in (result.content or []):
            text = getattr(block, "text", None)
            if text is not None:
                out_parts.append(text)
            else:
                out_parts.append(repr(block))
        if getattr(result, "isError", False):
            return f"[tool error] {' '.join(out_parts) or 'erro desconhecido'}"
        return "\n".join(out_parts) if out_parts else "(sem retorno)"
