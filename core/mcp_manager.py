"""Gerencia servidores MCP (Model Context Protocol) registrados pela usuária.

Estilo Claude Desktop / Claude Code: cada server tem `type` (stdio | http |
sse) + (`command`, `args`, `env`) pra stdio, ou (`url`, `headers`) pra
http/sse. A configuração local vive em `config/mcp_servers.json`.

Além dos locais, descobrimos servidores **globais** já configurados em:
- `~/.claude.json`           → `mcpServers` top-level e dentro de
                               `projects[<path>].mcpServers` (Claude Code)
- `%APPDATA%/Claude/claude_desktop_config.json` → `mcpServers` (Claude Desktop)

Globais são *read-only*: aparecem na UI com a fonte de origem, mas só são
conectados pelo Jarvis quando importados pra config local.

## Concorrência

O MCP Python SDK é construído sobre `anyio` e usa cancel scopes via
`async with` em `AsyncExitStack`. Os cancel scopes do anyio têm uma regra
estrita: quem entrou tem que sair *na mesma task*. Como o Jarvis dispara
reloads de threads diferentes (UI, importação, boot), cada chamada de
`run_coroutine_threadsafe` agenda uma **task nova** no loop — fechar o
stack montado por outra task gera:

    RuntimeError: Attempted to exit cancel scope in a different task
                  than it was entered in

Por isso, o lifecycle de conexões é executado dentro de uma única
**task supervisor** que vive no event loop. As demais threads enviam
*comandos* (reload / call_tool / shutdown) por uma `asyncio.Queue` e
esperam um future de retorno. Tudo que toca o `AsyncExitStack` ou
`session.call_tool` roda na mesma task — sem cancel scope cruzado.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VALID_TRANSPORTS = ("stdio", "http", "sse")


@dataclass
class MCPServerConfig:
    name: str
    transport: str = "stdio"
    # stdio
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # http / sse
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    auth: str = "auto"  # "auto" | "oauth" | "none" — pra http/sse
    # comum
    enabled: bool = True
    # origem (preenchida só na descoberta — não é persistida)
    source: str = "local"  # "local" | "claude_desktop" | "claude_code:<path>"


@dataclass
class _ServerRuntime:
    config: MCPServerConfig
    session: Any  # mcp.ClientSession
    tools: list[dict[str, Any]]  # já no formato Anthropic
    error: str | None = None


class MCPManager:
    """Pool de servidores MCP com fachada síncrona."""

    def __init__(self, config_path: Path, oauth_state_dir: Path | None = None) -> None:
        self._config_path = config_path
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        # Tokens OAuth de cada server HTTP/SSE — diretório separado pro
        # `.jarvis_state` não poluir o git.
        self._oauth_state_dir = oauth_state_dir or (config_path.parent.parent / ".jarvis_state" / "mcp_oauth")
        self._oauth_state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._servers: dict[str, _ServerRuntime] = {}
        self._tool_owner: dict[str, str] = {}  # tool_name → server_name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._cmd_queue: asyncio.Queue | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._stack: AsyncExitStack | None = None
        self._stack_entered: bool = False

    # ---------- Persistência ----------

    def load_configs(self) -> list[MCPServerConfig]:
        if not self._config_path.exists():
            return []
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        servers = data.get("mcpServers") or {}
        return [_parse_server(name, cfg, source="local") for name, cfg in servers.items()]

    def save_configs(self, configs: list[MCPServerConfig]) -> None:
        payload = {"mcpServers": {cfg.name: _serialize_server(cfg) for cfg in configs}}
        tmp = self._config_path.with_suffix(self._config_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._config_path)

    # ---------- Discovery ----------

    def discover_globals(self) -> list[MCPServerConfig]:
        """Lê configs do Claude Desktop/Code e devolve servers globais.

        Não conecta nada — só lista. O `source` indica a origem pra UI
        renderizar e pro endpoint de importação.
        """
        out: list[MCPServerConfig] = []
        out.extend(_discover_claude_desktop())
        out.extend(_discover_claude_code())
        # Dedup por (source, name) preservando ordem de inserção.
        seen: set[tuple[str, str]] = set()
        unique: list[MCPServerConfig] = []
        for cfg in out:
            key = (cfg.source, cfg.name)
            if key in seen:
                continue
            seen.add(key)
            unique.append(cfg)
        return unique

    def import_global(self, source: str, name: str) -> MCPServerConfig:
        """Copia um server global pra config local. Retorna o config salvo."""
        for cfg in self.discover_globals():
            if cfg.source == source and cfg.name == name:
                local = MCPServerConfig(
                    name=cfg.name,
                    transport=cfg.transport,
                    command=cfg.command,
                    args=list(cfg.args),
                    env=dict(cfg.env),
                    url=cfg.url,
                    headers=dict(cfg.headers),
                    auth=cfg.auth or "auto",
                    enabled=True,
                    source="local",
                )
                existing = [s for s in self.load_configs() if s.name != local.name]
                existing.append(local)
                self.save_configs(existing)
                self.reload()
                return local
        raise KeyError(f"server global não encontrado: source={source!r} name={name!r}")

    # ---------- Lifecycle ----------

    def start(self) -> None:
        """Sobe um loop async em thread daemon, a task supervisor e conecta os servidores."""
        if self._loop_thread is not None:
            return
        ready = threading.Event()

        def runner() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._cmd_queue = asyncio.Queue()
            self._supervisor_task = self._loop.create_task(self._supervisor())
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
            self._send_cmd("shutdown", timeout=10.0)
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
        try:
            return self._send_cmd("reconnect", configs=self.load_configs(), timeout=30.0)
        except Exception as e:
            print(f"[mcp] reload falhou: {e!r}")
            return {"_error": repr(e)}

    # ---------- Acessores ----------

    def list_status(self, *, include_globals: bool = True) -> list[dict[str, Any]]:
        """Lista todos os servidores: locais (com runtime) + globais (read-only)."""
        with self._lock:
            local_names = {rt.config.name for rt in self._servers.values()}
            rows = [_status_row(rt.config, rt) for rt in self._servers.values()]
        if include_globals:
            for cfg in self.discover_globals():
                # Esconde global que tem o mesmo nome de um local — local vence.
                if cfg.name in local_names:
                    continue
                rows.append(_status_row(cfg, None))
        return rows

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
        try:
            return self._send_cmd("call_tool", session=session, name=tool_name, args=arguments, timeout=60.0)
        except Exception as e:
            return f"[mcp] erro chamando {tool_name}: {e!r}"

    # ---------- Supervisor (task longa-vida no loop) ----------

    def _send_cmd(self, op: str, *, timeout: float, **kwargs: Any) -> Any:
        """Envia comando à supervisor e bloqueia a thread chamadora pelo resultado."""
        if self._loop is None or self._cmd_queue is None:
            raise RuntimeError("MCPManager não iniciado")
        fut = asyncio.run_coroutine_threadsafe(self._dispatch(op, kwargs), self._loop)
        return fut.result(timeout=timeout)

    async def _dispatch(self, op: str, kwargs: dict) -> Any:
        """Roda no loop. Empurra o comando na fila e aguarda o future de retorno."""
        loop = asyncio.get_running_loop()
        result_future: asyncio.Future = loop.create_future()
        await self._cmd_queue.put({"op": op, "future": result_future, **kwargs})
        return await result_future

    async def _supervisor(self) -> None:
        """Loop único que possui o AsyncExitStack e processa todos os comandos."""
        try:
            while True:
                cmd = await self._cmd_queue.get()
                op = cmd["op"]
                fut: asyncio.Future = cmd["future"]
                try:
                    if op == "reconnect":
                        result = await self._do_reconnect(cmd["configs"])
                        fut.set_result(result)
                    elif op == "call_tool":
                        result = await self._do_call_tool(
                            cmd["session"], cmd["name"], cmd["args"]
                        )
                        fut.set_result(result)
                    elif op == "shutdown":
                        await self._do_close()
                        fut.set_result(None)
                        return
                    else:
                        fut.set_exception(ValueError(f"comando desconhecido: {op!r}"))
                except Exception as e:
                    if not fut.done():
                        fut.set_exception(e)
        except asyncio.CancelledError:
            await self._do_close()
            raise

    async def _do_reconnect(self, configs: list[MCPServerConfig]) -> dict[str, str]:
        # Fecha stack anterior — sempre na mesma task (a supervisor), por isso não dá ruim.
        await self._do_close()

        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        self._stack_entered = True

        statuses: dict[str, str] = {}
        new_servers: dict[str, _ServerRuntime] = {}
        new_owner: dict[str, str] = {}

        for cfg in configs:
            if not cfg.enabled:
                statuses[cfg.name] = "disabled"
                new_servers[cfg.name] = _ServerRuntime(config=cfg, session=None, tools=[])
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

    async def _do_close(self) -> None:
        if self._stack_entered and self._stack is not None:
            try:
                await self._stack.__aexit__(None, None, None)
            except Exception as e:
                # Não relança — o objetivo é não vazar processos. Logamos e seguimos.
                print(f"[mcp] cleanup error: {e!r}")
        self._stack = None
        self._stack_entered = False
        with self._lock:
            self._servers.clear()
            self._tool_owner.clear()

    async def _aconnect_one(self, cfg: MCPServerConfig):
        from mcp import ClientSession

        if cfg.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            if not cfg.command:
                raise ValueError("transport=stdio exige `command`")
            params = StdioServerParameters(
                command=cfg.command,
                args=cfg.args,
                env=cfg.env or None,
            )
            transport = await self._stack.enter_async_context(stdio_client(params))
            read, write = transport[0], transport[1]
        elif cfg.transport == "http":
            from mcp.client.streamable_http import streamablehttp_client

            if not cfg.url:
                raise ValueError("transport=http exige `url`")
            auth = self._build_oauth(cfg)
            transport = await self._stack.enter_async_context(
                streamablehttp_client(cfg.url, headers=cfg.headers or None, auth=auth)
            )
            # streamablehttp_client retorna (read, write, get_session_id).
            read, write = transport[0], transport[1]
        elif cfg.transport == "sse":
            from mcp.client.sse import sse_client

            if not cfg.url:
                raise ValueError("transport=sse exige `url`")
            auth = self._build_oauth(cfg)
            transport = await self._stack.enter_async_context(
                sse_client(cfg.url, headers=cfg.headers or None, auth=auth)
            )
            read, write = transport[0], transport[1]
        else:
            raise ValueError(f"transport inválido: {cfg.transport!r}")

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

    def _build_oauth(self, cfg: MCPServerConfig):
        """Constrói o OAuthClientProvider, ou None se desligado.

        `auth=none` → sem OAuth (servidor público ou auth via header manual).
        `auth=oauth` ou `auto` → ativa o flow.
        """
        if (cfg.auth or "auto").lower() == "none":
            return None
        try:
            from core.mcp_oauth import make_oauth_provider
            return make_oauth_provider(
                server_name=cfg.name,
                server_url=cfg.url,
                state_dir=self._oauth_state_dir,
            )
        except Exception as e:
            print(f"[mcp] OAuth provider falhou pra {cfg.name!r}: {e!r}")
            return None

    async def _do_call_tool(self, session, name: str, arguments: dict[str, Any]) -> str:
        result = await session.call_tool(name, arguments=arguments)
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


# ---------- Helpers fora da classe ----------

def _parse_server(name: str, cfg: dict, *, source: str) -> MCPServerConfig:
    """Aceita o formato Claude Desktop/Code (com `type`) e o nosso (com `transport`)."""
    if not isinstance(cfg, dict):
        return MCPServerConfig(name=name, source=source, enabled=False)
    transport = (cfg.get("type") or cfg.get("transport") or "").strip().lower()
    if not transport:
        # Inferência: tem command → stdio; tem url → http.
        if cfg.get("command"):
            transport = "stdio"
        elif cfg.get("url"):
            transport = "http"
        else:
            transport = "stdio"
    if transport not in VALID_TRANSPORTS:
        transport = "stdio"
    return MCPServerConfig(
        name=name,
        transport=transport,
        command=cfg.get("command", "") or "",
        args=list(cfg.get("args") or []),
        env=dict(cfg.get("env") or {}),
        url=cfg.get("url", "") or "",
        headers=dict(cfg.get("headers") or {}),
        auth=(cfg.get("auth") or "auto").lower(),
        enabled=bool(cfg.get("enabled", True)),
        source=source,
    )


def _serialize_server(cfg: MCPServerConfig) -> dict[str, Any]:
    """Formato no disco — compatível com Claude Desktop/Code (`type` + campos)."""
    out: dict[str, Any] = {"type": cfg.transport, "enabled": cfg.enabled}
    if cfg.transport == "stdio":
        out["command"] = cfg.command
        if cfg.args:
            out["args"] = list(cfg.args)
        if cfg.env:
            out["env"] = dict(cfg.env)
    else:
        out["url"] = cfg.url
        if cfg.headers:
            out["headers"] = dict(cfg.headers)
        if cfg.auth and cfg.auth != "auto":
            out["auth"] = cfg.auth
    return out


def _status_row(cfg: MCPServerConfig, rt: _ServerRuntime | None) -> dict[str, Any]:
    return {
        "name": cfg.name,
        "transport": cfg.transport,
        "command": cfg.command,
        "args": cfg.args,
        "url": cfg.url,
        "auth": cfg.auth,
        "enabled": cfg.enabled,
        "source": cfg.source,
        "connected": (rt is not None and rt.error is None and rt.session is not None),
        "error": rt.error if rt else None,
        "tools": [t["name"] for t in rt.tools] if rt else [],
    }


def _discover_claude_desktop() -> list[MCPServerConfig]:
    """Lê %APPDATA%/Claude/claude_desktop_config.json (Windows) ou equivalente."""
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    home = Path.home()
    candidates.append(home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    candidates.append(home / ".config" / "Claude" / "claude_desktop_config.json")

    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        servers = data.get("mcpServers") or {}
        return [_parse_server(name, cfg, source="claude_desktop") for name, cfg in servers.items()]
    return []


def _discover_claude_code() -> list[MCPServerConfig]:
    """Lê ~/.claude.json — `mcpServers` top-level + `projects[<path>].mcpServers`.

    Esta máquina tem MCPs aninhados por projeto (atlassian/gitlab/linear).
    Usamos `source = "claude_code:<project_path>"` pra que a UI mostre de
    onde vem cada server.
    """
    path = Path.home() / ".claude.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[MCPServerConfig] = []

    top = data.get("mcpServers") or {}
    for name, cfg in top.items():
        out.append(_parse_server(name, cfg, source="claude_code"))

    projects = data.get("projects") or {}
    for project_path, pcfg in projects.items():
        if not isinstance(pcfg, dict):
            continue
        servers = pcfg.get("mcpServers") or {}
        if not servers:
            continue
        for name, cfg in servers.items():
            out.append(_parse_server(name, cfg, source=f"claude_code:{project_path}"))
    return out
