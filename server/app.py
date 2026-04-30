"""Servidor FastAPI com WebSocket pra UI ao vivo + APIs REST.

Rotas:
- GET  /                 → index.html
- GET  /static/*         → assets estáticos
- WS   /ws               → stream de eventos do EventBus
- POST /api/command      → comando texto da UI
- POST /api/voice        → áudio PCM 16kHz int16 da UI
- GET  /api/config       → dump do config/jarvis.yaml
- PATCH /api/config      → merge parcial no config (alguns campos exigem restart)
- GET  /api/mcps         → lista de servidores MCP + status
- PUT  /api/mcps         → substitui a lista inteira de MCPs
- POST /api/mcps/reload  → reconecta os servidores
- GET  /api/status       → flags de capabilities (stt/agent/wake_word/etc.)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.command_registry import Tool, serialize_catalog
from core.config_manager import ConfigManager
from core.event_bus import Event, EventBus, EventType
from core.mcp_manager import MCPManager, MCPServerConfig

UI_DIR = Path(__file__).resolve().parent / "ui"


class CommandIn(BaseModel):
    text: str


class MCPServerIn(BaseModel):
    name: str
    command: str
    args: list[str] = []
    env: dict[str, str] = {}
    enabled: bool = True


class MCPListIn(BaseModel):
    servers: list[MCPServerIn]


class VoiceCommandIn(BaseModel):
    trigger: str
    description: str = ""
    tool: str
    action: str
    params: dict[str, Any] = {}


class VoiceCommandListIn(BaseModel):
    commands: list[VoiceCommandIn]


def create_app(
    *,
    event_bus: EventBus,
    text_handler: Callable[[str], None],
    audio_handler: Callable[[bytes], None] | None = None,
    config_manager: ConfigManager | None = None,
    mcp_manager: MCPManager | None = None,
    capabilities_provider: Callable[[], dict[str, Any]] | None = None,
    tool_registry_provider: Callable[[], dict[str, Tool]] | None = None,
) -> FastAPI:
    app = FastAPI(title="Jarvis")

    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")

    @app.post("/api/command")
    async def post_command(cmd: CommandIn) -> dict:
        await asyncio.to_thread(text_handler, cmd.text)
        return {"ok": True}

    @app.post("/api/voice")
    async def post_voice(request: Request) -> dict:
        if audio_handler is None:
            raise HTTPException(status_code=503, detail="STT indisponível")
        # Recebe PCM bruto (mono 16kHz int16) no body — tipo fixo, sem multipart.
        body = await request.body()
        if not body:
            raise HTTPException(status_code=400, detail="payload vazio")
        await asyncio.to_thread(audio_handler, body)
        return {"ok": True, "bytes": len(body)}

    # ---------- Config ----------

    @app.get("/api/config")
    async def get_config() -> dict:
        if config_manager is None:
            raise HTTPException(status_code=503, detail="config indisponível")
        return await asyncio.to_thread(config_manager.load)

    @app.patch("/api/config")
    async def patch_config(payload: dict[str, Any]) -> dict:
        if config_manager is None:
            raise HTTPException(status_code=503, detail="config indisponível")
        updated = await asyncio.to_thread(config_manager.patch, payload)
        return updated

    # ---------- Voice commands ----------

    @app.get("/api/commands")
    async def list_commands() -> dict:
        if config_manager is None:
            raise HTTPException(status_code=503, detail="config indisponível")
        cfg = await asyncio.to_thread(config_manager.load)
        return {"commands": cfg.get("commands") or []}

    @app.put("/api/commands")
    async def put_commands(payload: VoiceCommandListIn) -> dict:
        if config_manager is None:
            raise HTTPException(status_code=503, detail="config indisponível")
        commands = [
            {
                "trigger": c.trigger,
                "description": c.description,
                "tool": c.tool,
                "action": c.action,
                "params": dict(c.params),
            }
            for c in payload.commands
        ]
        await asyncio.to_thread(config_manager.patch, {"commands": commands})
        return {"ok": True, "commands": commands}

    @app.get("/api/commands/catalog")
    async def commands_catalog() -> dict:
        registry = tool_registry_provider() if tool_registry_provider else {}
        return {"tools": serialize_catalog(registry)}

    # ---------- MCPs ----------

    @app.get("/api/mcps")
    async def list_mcps() -> dict:
        if mcp_manager is None:
            return {"servers": [], "available": False}
        return {"servers": mcp_manager.list_status(), "available": True}

    @app.put("/api/mcps")
    async def put_mcps(payload: MCPListIn) -> dict:
        if mcp_manager is None:
            raise HTTPException(status_code=503, detail="MCP manager indisponível")
        configs = [
            MCPServerConfig(
                name=s.name,
                command=s.command,
                args=list(s.args),
                env=dict(s.env),
                enabled=s.enabled,
            )
            for s in payload.servers
        ]
        await asyncio.to_thread(mcp_manager.save_configs, configs)
        statuses = await asyncio.to_thread(mcp_manager.reload)
        return {"ok": True, "statuses": statuses, "servers": mcp_manager.list_status()}

    @app.post("/api/mcps/reload")
    async def reload_mcps() -> dict:
        if mcp_manager is None:
            raise HTTPException(status_code=503, detail="MCP manager indisponível")
        statuses = await asyncio.to_thread(mcp_manager.reload)
        return {"ok": True, "statuses": statuses, "servers": mcp_manager.list_status()}

    # ---------- Status ----------

    @app.get("/api/status")
    async def status() -> dict:
        if capabilities_provider is None:
            return {"capabilities": {}}
        return await asyncio.to_thread(capabilities_provider)

    # ---------- WebSocket ----------

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=200)

        def on_event(event: Event) -> None:
            loop.call_soon_threadsafe(_safe_put, queue, event)

        event_bus.subscribe(on_event)
        await websocket.send_json(
            _serialize(Event(type=EventType.STATUS, data={"state": "idle"}))
        )

        try:
            while True:
                event = await queue.get()
                await websocket.send_json(_serialize(event))
        except WebSocketDisconnect:
            pass
        finally:
            event_bus.unsubscribe(on_event)

    return app


def _safe_put(queue: "asyncio.Queue[Event]", event: Event) -> None:
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


def _serialize(event: Event) -> dict:
    return {
        "type": event.type.value,
        "data": event.data,
        "timestamp": event.timestamp,
    }
