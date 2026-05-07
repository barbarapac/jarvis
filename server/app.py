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
- GET  /api/skills       → catálogo de skills/commands do Claude Code (anotado)
- POST /api/skills/toggle→ ativa/desativa uma skill no awareness do agente
- GET  /api/claude-code/projects → projetos cadastrados pra abrir Claude Code (CLI)
- PUT  /api/claude-code/projects → substitui o dict inteiro de projetos
- GET  /api/terminal     → terminal preferido pra janelas interativas
- PUT  /api/terminal     → atualiza terminal.preferred
- GET  /api/status       → flags de capabilities (stt/agent/wake_word/etc.)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.command_registry import Tool, serialize_catalog
from core.config_manager import ConfigManager
from core.event_bus import Event, EventBus, EventType
from core.mcp_manager import MCPManager, MCPServerConfig
from core.secrets_manager import SecretsManager
from core.terminal import VALID_TERMINALS, normalize_preferred
from core.vault import Vault

UI_DIR = Path(__file__).resolve().parent / "ui"


class CommandIn(BaseModel):
    text: str


class ConversationSendIn(BaseModel):
    text: str
    mode: str | None = None  # 'critico' | 'sintetizador' | 'advogado' | None


class MCPServerIn(BaseModel):
    name: str
    transport: str = "stdio"  # "stdio" | "http" | "sse"
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}
    url: str = ""
    headers: dict[str, str] = {}
    auth: str = "auto"  # "auto" | "oauth" | "none"
    enabled: bool = True


class MCPListIn(BaseModel):
    servers: list[MCPServerIn]


class MCPImportIn(BaseModel):
    source: str  # "claude_desktop" | "claude_code" | "claude_code:<path>"
    name: str


class VoiceCommandIn(BaseModel):
    trigger: str
    description: str = ""
    tool: str
    action: str
    params: dict[str, Any] = {}


class VoiceCommandListIn(BaseModel):
    commands: list[VoiceCommandIn]


class VaultApplyIn(BaseModel):
    file: str
    action: str  # "append" | "replace" | "create"
    content: str


class SkillToggleIn(BaseModel):
    name: str
    enabled: bool


class ClaudeProjectsIn(BaseModel):
    # Mapeia chave amigável → caminho. Substitui o dict inteiro (chaves
    # antigas que não estiverem aqui são REMOVIDAS).
    projects: dict[str, str]


class TerminalIn(BaseModel):
    preferred: str


class SecretsUpdateIn(BaseModel):
    # value=None ou "" remove a chave do .env
    updates: dict[str, str | None]


class VaultIngestIn(BaseModel):
    title: str | None = None
    content: str
    tags: list[str] = []
    source: str | None = None
    # Pasta de destino: perfil / projetos / decisoes / conhecimento.
    # Default cai no "conhecimento" — o agente NÃO consome essa pasta automaticamente,
    # então é o lugar mais seguro pra notas avulsas.
    category: str = "conhecimento"


def create_app(
    *,
    event_bus: EventBus,
    text_handler: Callable[[str], None],
    audio_handler: Callable[[bytes], None] | None = None,
    config_manager: ConfigManager | None = None,
    mcp_manager: MCPManager | None = None,
    secrets_manager: SecretsManager | None = None,
    capabilities_provider: Callable[[], dict[str, Any]] | None = None,
    tool_registry_provider: Callable[[], dict[str, Tool]] | None = None,
    vault_provider: Callable[[], Vault | None] | None = None,
    synthesizer_provider: Callable[[], Any | None] | None = None,
    briefing_provider: Callable[[], Any | None] | None = None,
    agent_invoker: Callable[[str, str | None], None] | None = None,
) -> FastAPI:
    app = FastAPI(title="Jarvis")

    # No-cache em todos os assets servidos: o Jarvis é dev local e ninguém
    # quer ficar batendo Ctrl+F5 quando o CSS/JS muda. Sem service worker
    # registrado, basta dizer ao browser pra não guardar nada.
    @app.middleware("http")
    async def _no_cache_static(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    app.mount("/static", StaticFiles(directory=str(UI_DIR / "static")), name="static")

    @app.get("/")
    async def index() -> HTMLResponse:
        # Lê o index e injeta ?v=<mtime> nos links de style/app pra que o
        # WebView2 sempre pegue a versão atual mesmo se algum cache local
        # decidir ignorar o no-store.
        html = (UI_DIR / "index.html").read_text(encoding="utf-8")
        for asset in ("style.css", "app.js"):
            mtime = int((UI_DIR / "static" / asset).stat().st_mtime)
            html = html.replace(f"/static/{asset}", f"/static/{asset}?v={mtime}")
        return HTMLResponse(html)

    @app.post("/api/command")
    async def post_command(cmd: CommandIn) -> dict:
        await asyncio.to_thread(text_handler, cmd.text)
        return {"ok": True}

    @app.post("/api/conversation/send")
    async def post_conversation_send(payload: ConversationSendIn) -> dict:
        # Vai DIRETO no agente, sem dispatch local. A view Conversa é "falar
        # com o agente"; comandos rápidos ficam na Home (/api/command).
        if agent_invoker is None:
            raise HTTPException(
                status_code=503,
                detail="agente indisponível (precisa ANTHROPIC_API_KEY)",
            )
        await asyncio.to_thread(agent_invoker, payload.text, payload.mode)
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

    # ---------- Secrets (.env) ----------

    @app.get("/api/secrets")
    async def list_secrets() -> dict:
        if secrets_manager is None:
            raise HTTPException(status_code=503, detail="secrets indisponível")
        items = await asyncio.to_thread(secrets_manager.status)
        return {"items": items}

    @app.get("/api/secrets/{key}/reveal")
    async def reveal_secret(key: str, request: Request) -> dict:
        # Mesmo guard do PATCH — só localhost pode pedir o valor cru. Cada
        # chamada retorna o valor uma vez; a UI usa pra preencher o input
        # quando o usuário clica em "Trocar" e re-esconde ao cancelar.
        client_host = request.client.host if request.client else ""
        if client_host not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(status_code=403, detail="apenas localhost pode revelar secrets")
        if secrets_manager is None:
            raise HTTPException(status_code=503, detail="secrets indisponível")
        try:
            value = await asyncio.to_thread(secrets_manager.get_value, key)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"key": key, "value": value}

    @app.patch("/api/secrets")
    async def patch_secrets(payload: SecretsUpdateIn, request: Request) -> dict:
        # Localhost-only — defense in depth (o servidor já bind em 127.0.0.1,
        # mas se alguém mudar o host pra 0.0.0.0 sem querer, esse guard segura).
        client_host = request.client.host if request.client else ""
        if client_host not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(status_code=403, detail="apenas localhost pode editar secrets")
        if secrets_manager is None:
            raise HTTPException(status_code=503, detail="secrets indisponível")
        try:
            await asyncio.to_thread(secrets_manager.update, payload.updates)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        # Retorna status atualizado pra UI re-renderizar sem novo GET.
        items = await asyncio.to_thread(secrets_manager.status)
        return {"ok": True, "items": items}

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

    # ---------- Skills (Claude Code awareness) ----------

    @app.get("/api/skills")
    async def list_skills() -> dict:
        from core.skill_catalog import annotate, discover_skills

        entries = await asyncio.to_thread(discover_skills)
        disabled: set[str] = set()
        if config_manager is not None:
            cfg = await asyncio.to_thread(config_manager.load)
            disabled = set((cfg.get("skills") or {}).get("disabled") or [])
        items = annotate(entries, disabled=disabled)
        return {
            "skills": items,
            "total": len(items),
            "enabled_count": sum(1 for x in items if x["enabled"]),
        }

    @app.post("/api/skills/toggle")
    async def toggle_skill(payload: SkillToggleIn) -> dict:
        if config_manager is None:
            raise HTTPException(status_code=503, detail="config indisponível")
        cfg = await asyncio.to_thread(config_manager.load)
        skills_cfg = (cfg.get("skills") or {})
        disabled = set(skills_cfg.get("disabled") or [])
        if payload.enabled:
            disabled.discard(payload.name)
        else:
            disabled.add(payload.name)
        # Lista ordenada pra YAML estável (diff amigável).
        new_disabled = sorted(disabled)
        await asyncio.to_thread(
            config_manager.patch, {"skills": {"disabled": new_disabled}}
        )
        # Avisa a UI pra recarregar — qualquer cliente conectado redesenha.
        try:
            event_bus.publish(EventType.SKILLS_UPDATED)
        except Exception as e:
            print(f"[skills] falha emitindo skills_updated: {e!r}")
        return {"ok": True, "name": payload.name, "enabled": payload.enabled}

    # ---------- Claude Code (projetos cadastrados) ----------

    @app.get("/api/claude-code/projects")
    async def list_claude_projects() -> dict:
        if config_manager is None:
            return {"projects": []}
        cfg = await asyncio.to_thread(config_manager.load)
        projects_cfg = (
            ((cfg.get("tools") or {}).get("claude_code") or {}).get("projects") or {}
        )
        items = []
        for name, raw in projects_cfg.items():
            resolved = Path(str(raw)).expanduser()
            items.append(
                {
                    "name": name,
                    "path": str(raw),
                    "resolved_path": str(resolved),
                    "exists": resolved.is_dir(),
                }
            )
        return {"projects": items}

    @app.put("/api/claude-code/projects")
    async def put_claude_projects(payload: ClaudeProjectsIn) -> dict:
        if config_manager is None:
            raise HTTPException(status_code=503, detail="config indisponível")
        # Valida nomes: sem strings vazias / espaços só.
        cleaned: dict[str, str] = {}
        for raw_name, raw_path in payload.projects.items():
            name = (raw_name or "").strip()
            path = (raw_path or "").strip()
            if not name or not path:
                continue
            cleaned[name] = path
        # _deep_merge interpreta None como "delete key" — usamos isso pra
        # remover projetos que sumiram do payload, garantindo replace total
        # do dict (em vez do merge default).
        cfg = await asyncio.to_thread(config_manager.load)
        old = (
            ((cfg.get("tools") or {}).get("claude_code") or {}).get("projects") or {}
        )
        deletions = {name: None for name in old if name not in cleaned}
        await asyncio.to_thread(
            config_manager.patch,
            {"tools": {"claude_code": {"projects": {**deletions, **cleaned}}}},
        )
        return {"ok": True, "count": len(cleaned)}

    # ---------- Terminal preferido ----------

    @app.get("/api/terminal")
    async def get_terminal() -> dict:
        if config_manager is None:
            return {"preferred": "auto", "options": list(VALID_TERMINALS)}
        cfg = await asyncio.to_thread(config_manager.load)
        preferred = normalize_preferred(
            (cfg.get("terminal") or {}).get("preferred")
        )
        return {"preferred": preferred, "options": list(VALID_TERMINALS)}

    @app.put("/api/terminal")
    async def put_terminal(payload: TerminalIn) -> dict:
        if config_manager is None:
            raise HTTPException(status_code=503, detail="config indisponível")
        pref = (payload.preferred or "").strip().lower()
        if pref not in VALID_TERMINALS:
            raise HTTPException(
                status_code=422,
                detail=f"preferred inválido: {pref!r}. opções: {list(VALID_TERMINALS)}",
            )
        await asyncio.to_thread(
            config_manager.patch, {"terminal": {"preferred": pref}}
        )
        return {"ok": True, "preferred": pref}

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
                transport=(s.transport or "stdio").lower(),
                command=s.command,
                args=list(s.args),
                env=dict(s.env),
                url=s.url,
                headers=dict(s.headers),
                auth=(s.auth or "auto").lower(),
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

    @app.post("/api/mcps/import")
    async def import_mcp(payload: MCPImportIn) -> dict:
        """Copia um server global (Claude Desktop / Code) pra config local."""
        if mcp_manager is None:
            raise HTTPException(status_code=503, detail="MCP manager indisponível")
        try:
            cfg = await asyncio.to_thread(mcp_manager.import_global, payload.source, payload.name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"falha importando: {e!r}")
        return {"ok": True, "imported": cfg.name, "servers": mcp_manager.list_status()}

    # ---------- Conversa / Vault ----------

    @app.get("/api/conversations/today")
    async def conversations_today() -> dict:
        vault = vault_provider() if vault_provider else None
        if vault is None:
            return {"turns": [], "available": False}
        turns = await asyncio.to_thread(vault.read_today_turns)
        return {"turns": turns, "available": True}

    @app.post("/api/vault/synthesize")
    async def vault_synthesize() -> dict:
        vault = vault_provider() if vault_provider else None
        synthesizer = synthesizer_provider() if synthesizer_provider else None
        if vault is None:
            raise HTTPException(status_code=503, detail="vault indisponível")
        if synthesizer is None:
            raise HTTPException(
                status_code=503,
                detail="synthesizer indisponível (precisa ANTHROPIC_API_KEY)",
            )
        try:
            proposals = await asyncio.to_thread(synthesizer.synthesize)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"falha sintetizando: {e!r}")
        return {"proposals": proposals}

    @app.get("/api/vault/stats")
    async def vault_stats() -> dict:
        vault = vault_provider() if vault_provider else None
        if vault is None:
            raise HTTPException(status_code=503, detail="vault indisponível")
        stats = await asyncio.to_thread(vault.stats)
        return {"stats": stats, "root": str(vault.root)}

    @app.get("/api/vault/graph")
    async def vault_graph() -> dict:
        vault = vault_provider() if vault_provider else None
        if vault is None:
            raise HTTPException(status_code=503, detail="vault indisponível")
        return await asyncio.to_thread(vault.graph)

    @app.post("/api/vault/ingest")
    async def vault_ingest(payload: VaultIngestIn) -> dict:
        vault = vault_provider() if vault_provider else None
        if vault is None:
            raise HTTPException(status_code=503, detail="vault indisponível")

        category = (payload.category or "conhecimento").strip().lower()
        title = (payload.title or "").strip()

        # Sem título → heurística simples (primeiras palavras), sem IA.
        if not title:
            import re
            cleaned = re.sub(r"\s+", " ", payload.content.strip())
            title = cleaned[:60].rstrip(".,;:!?-—") or "Nota sem título"

        try:
            path = await asyncio.to_thread(
                vault.ingest_knowledge,
                title,
                payload.content,
                tags=list(payload.tags),
                source=payload.source,
                category=category,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        try:
            rel = path.relative_to(vault.root).as_posix()
        except ValueError:
            rel = path.name
        return {
            "ok": True,
            "path": str(path),
            "rel_path": rel,
            "category": category,
            "title": title,
        }

    @app.post("/api/vault/ingest_file")
    async def vault_ingest_file(
        file: UploadFile = File(...),
        title: str | None = Form(None),
        category: str = Form("conhecimento"),
        tags: str = Form(""),  # CSV
    ) -> dict:
        """Ingestão a partir de arquivo binário (PDF) ou texto (md/txt).

        Extrai texto do PDF com pypdf no servidor e reusa `vault.ingest_knowledge`.
        Limite de 200 MB pra evitar abuso. PDF escaneado (sem camada de texto)
        retorna warning porque pypdf não faz OCR.
        """
        vault = vault_provider() if vault_provider else None
        if vault is None:
            raise HTTPException(status_code=503, detail="vault indisponível")

        MAX_BYTES = 200 * 1024 * 1024
        raw = await file.read()
        if len(raw) > MAX_BYTES:
            raise HTTPException(status_code=413, detail="arquivo > 200 MB")
        if not raw:
            raise HTTPException(status_code=400, detail="arquivo vazio")

        filename = file.filename or "arquivo"
        suffix = Path(filename).suffix.lower()
        warning: str | None = None

        if suffix == ".pdf" or (file.content_type or "").lower() == "application/pdf":
            content, warning = await asyncio.to_thread(_extract_pdf_text, raw)
            if not content.strip():
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "PDF sem texto extraível (provavelmente escaneado). "
                        "OCR não está disponível — converta manualmente ou cole o texto."
                    ),
                )
        elif suffix in (".md", ".txt") or (file.content_type or "").startswith("text/"):
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                content = raw.decode("latin-1", errors="replace")
        else:
            raise HTTPException(
                status_code=415,
                detail=f"tipo de arquivo não suportado: {suffix or file.content_type}",
            )

        cat = (category or "conhecimento").strip().lower()
        suggested = (title or "").strip() or Path(filename).stem
        clean_tags = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

        try:
            path = await asyncio.to_thread(
                vault.ingest_knowledge,
                suggested,
                content,
                tags=clean_tags,
                source=f"arquivo:{filename}",
                category=cat,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        try:
            rel = path.relative_to(vault.root).as_posix()
        except ValueError:
            rel = path.name

        return {
            "ok": True,
            "path": str(path),
            "rel_path": rel,
            "category": cat,
            "title": suggested,
            "warning": warning,
            "chars": len(content),
        }

    @app.post("/api/vault/apply")
    async def vault_apply(payload: VaultApplyIn) -> dict:
        vault = vault_provider() if vault_provider else None
        if vault is None:
            raise HTTPException(status_code=503, detail="vault indisponível")
        try:
            written = await asyncio.to_thread(
                vault.write_curated_file,
                payload.file,
                payload.content,
                mode=payload.action,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "path": str(written)}

    @app.post("/api/briefing/run")
    async def briefing_run() -> dict:
        briefing = briefing_provider() if briefing_provider else None
        if briefing is None:
            raise HTTPException(
                status_code=503,
                detail="briefing indisponível (precisa ANTHROPIC_API_KEY e agente habilitado)",
            )
        try:
            text = await asyncio.to_thread(briefing.run)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"falha gerando briefing: {e!r}")
        return {"ok": True, "text": text}

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


def _extract_pdf_text(raw: bytes) -> tuple[str, str | None]:
    """Extrai texto de um PDF em bytes. Retorna (texto, warning_ou_None).

    pypdf não faz OCR — PDFs escaneados/só-imagem retornam texto vazio.
    O warning sinaliza se o aproveitamento foi baixo (poucos chars/página)
    pra UI poder avisar o usuário sem bloquear.
    """
    import io

    from pypdf import PdfReader  # import lazy: só carrega quando alguém envia PDF

    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"PDF inválido: {e}")

    parts: list[str] = []
    for page in reader.pages:
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        if txt.strip():
            parts.append(txt.strip())

    text = "\n\n".join(parts)
    pages = max(1, len(reader.pages))
    warning: str | None = None
    if text and len(text) / pages < 50:
        warning = (
            f"texto extraído curto (~{len(text) // pages} chars/página) — "
            "PDF pode ser escaneado/parcialmente imagem"
        )
    return text, warning
