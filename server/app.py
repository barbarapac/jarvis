"""Servidor FastAPI com WebSocket pra UI ao vivo.

- GET  /              → index.html
- GET  /static/*      → assets estáticos
- WS   /ws            → stream de eventos (Event do EventBus → JSON)
- POST /api/command   → comando texto da UI (chama VoiceCommander.handle_text)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.event_bus import Event, EventBus, EventType

UI_DIR = Path(__file__).resolve().parent / "ui"


class CommandIn(BaseModel):
    text: str


def create_app(
    event_bus: EventBus,
    text_handler: Callable[[str], None],
) -> FastAPI:
    app = FastAPI(title="Jarvis")

    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(UI_DIR / "index.html")

    @app.post("/api/command")
    async def post_command(cmd: CommandIn) -> dict:
        # Roda o handler em thread separada pra não bloquear o event loop async.
        await asyncio.to_thread(text_handler, cmd.text)
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=200)

        def on_event(event: Event) -> None:
            # EventBus chama isso de threads diversas; agendamos no loop async.
            loop.call_soon_threadsafe(_safe_put, queue, event)

        event_bus.subscribe(on_event)
        # Boas-vindas: avisa cliente que conectou.
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
        # Drop oldest, put new — UI não precisa de tudo histórico.
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
