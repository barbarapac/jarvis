"""Entry point do Jarvis. Boot + event loop + UI server + watchers + hotkey."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path

# Força UTF-8 no stdout/stderr no Windows pra evitar mojibake nos prints.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

import uvicorn
from dotenv import load_dotenv

from core.command_registry import Tool, build_gitlab_tool, build_persona_tool, build_spotify_tool
from core.config_manager import ConfigManager
from core.event_bus import EventBus, EventType
from core.hotkey import HotkeyListener
from core.mcp_manager import MCPManager
from core.narrator import Narrator
from core.persona import Persona
from core.spotify_ducker import SpotifyDucker
from core.state import WatcherState
from core.stt import VoskSTT
from core.tts import build_engine
from core.ui_launcher import launch_app_window
from core.voice_command import VoiceCommander
from core.wake_word import WakeWordListener
from core.workspace import WorkspaceManager
from server.app import create_app
from tools.code_review import CodeReviewTool, project_configs_from_yaml
from tools.gitlab import GitLabTool
from tools.spotify import SpotifyTool
from watchers.base import Watcher
from watchers.gitlab import GitLabWatcher

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config" / "jarvis.yaml"
MCP_CONFIG_PATH = ROOT / "config" / "mcp_servers.json"
STATE_PATH = ROOT / ".jarvis_state" / "watchers.json"

LOOP_TICK_SECONDS = 1.0


def build_persona(config: dict) -> Persona:
    user = config.get("user", {})
    return Persona(
        user_name=user.get("name", "Usuário"),
        honorific=user.get("honorific", "Senhor(a)"),
    )


def build_vosk_stt(config: dict) -> VoskSTT | None:
    """Constrói VoskSTT se modelo estiver disponível. Devolve None se ausente."""
    stt_cfg = config.get("stt", {}) or {}
    model_dir_str = stt_cfg.get("model_dir")
    if not model_dir_str:
        return None
    model_dir = ROOT / model_dir_str if not Path(model_dir_str).is_absolute() else Path(model_dir_str)
    if not model_dir.is_dir():
        print(f"[jarvis] modelo Vosk ausente em {model_dir}.", file=sys.stderr)
        print(f"[jarvis] rode `py scripts/setup_stt.py` pra baixá-lo.", file=sys.stderr)
        return None
    return VoskSTT(model_dir)


def build_stt(config: dict):
    """STT escolhido pra transcrição de comandos.

    engine="whisper" → faster-whisper (preciso, mais lento)
    engine="vosk" (default legado) → Vosk (rápido, fraco)

    Em qualquer caso, se Whisper falhar ou faltar deps, cai pro Vosk.
    """
    stt_cfg = config.get("stt", {}) or {}
    engine = (stt_cfg.get("engine") or "vosk").lower()
    if engine == "whisper":
        from core.stt_whisper import try_build_whisper
        whisper = try_build_whisper(stt_cfg.get("whisper") or {})
        if whisper is not None:
            return whisper
        print("[jarvis] caindo no Vosk como fallback.", file=sys.stderr)
    return build_vosk_stt(config)


def build_gitlab_tool_inst() -> GitLabTool | None:
    """Constrói a Tool GitLab se houver token. Compartilha auth com o watcher."""
    token = os.environ.get("GITLAB_TOKEN", "")
    if not token:
        return None
    base_url = os.environ.get("GITLAB_URL", "https://gitlab.com")
    try:
        return GitLabTool(token=token, base_url=base_url)
    except Exception as e:
        print(f"[jarvis] GitLab tool desativado: {e!r}", file=sys.stderr)
        return None


def build_spotify(config: dict) -> SpotifyTool | None:
    cfg = (config.get("tools") or {}).get("spotify") or {}
    if not cfg.get("enabled"):
        return None
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
    if not (client_id and client_secret):
        print("[jarvis] Spotify desativado: SPOTIFY_CLIENT_ID/SECRET ausentes no .env", file=sys.stderr)
        return None
    cache_path = ROOT / ".jarvis_state" / "spotify_cache"
    try:
        tool = SpotifyTool(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            cache_path=cache_path,
        )
        tool.ensure_auth()
        return tool
    except Exception as e:
        print(f"[jarvis] Spotify desativado por erro de auth: {e!r}", file=sys.stderr)
        print(f"[jarvis] rode `py scripts/setup_spotify.py` pra autorizar.", file=sys.stderr)
        return None


def build_workspace(config: dict) -> WorkspaceManager | None:
    cfg = (config.get("tools") or {}).get("code_review") or {}
    if not cfg.get("auto_clone", True):
        return None
    token = os.environ.get("GITLAB_TOKEN", "")
    if not token:
        return None
    base_url = os.environ.get("GITLAB_URL", "https://gitlab.com")
    return WorkspaceManager(
        root=ROOT / ".jarvis_state" / "repos",
        gitlab_base_url=base_url,
        gitlab_token=token,
    )


def build_code_review(
    config: dict,
    narrator: Narrator,
    persona: Persona,
    workspace: WorkspaceManager | None,
) -> CodeReviewTool | None:
    cfg = (config.get("tools") or {}).get("code_review") or {}
    if not cfg.get("enabled"):
        return None
    return CodeReviewTool(
        project_dirs=project_configs_from_yaml(cfg.get("project_dirs") or {}),
        commands=cfg.get("commands") or {},
        narrator=narrator,
        persona=persona,
        dry_run=bool(cfg.get("dry_run", True)),
        workspace=workspace,
    )


def build_agent(config: dict, persona: Persona, mcp_manager: MCPManager | None):
    """Tenta construir o agente Claude. Devolve None se faltar API key/SDK."""
    agent_cfg = (config.get("agent") or {})
    if not agent_cfg.get("enabled", True):
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[jarvis] agente Claude desativado: ANTHROPIC_API_KEY ausente.", file=sys.stderr)
        return None
    try:
        from core.agent import JarvisAgent
    except ImportError as e:
        print(f"[jarvis] agente Claude desativado: SDK ausente — {e}", file=sys.stderr)
        return None

    def tools_provider() -> list[dict]:
        return mcp_manager.list_tools_for_anthropic() if mcp_manager else []

    def tool_caller(name: str, args: dict) -> str:
        if mcp_manager is None:
            return "[mcp não disponível]"
        return mcp_manager.call_tool(name, args)

    try:
        return JarvisAgent(
            system_prompt=persona.system_prompt,
            tool_list_provider=tools_provider,
            tool_caller=tool_caller,
            model=agent_cfg.get("model", "claude-opus-4-7"),
        )
    except Exception as e:
        print(f"[jarvis] agente Claude falhou na inicialização: {e!r}", file=sys.stderr)
        return None


def build_watchers(
    config: dict,
    narrator: Narrator,
    persona: Persona,
    state: WatcherState,
    stt: VoskSTT | None,
    code_review: CodeReviewTool | None,
    event_bus: EventBus,
) -> list[Watcher]:
    watchers: list[Watcher] = []
    cfg = config.get("watchers", {}) or {}

    gitlab_cfg = cfg.get("gitlab", {}) or {}
    if gitlab_cfg.get("enabled"):
        token = os.environ.get("GITLAB_TOKEN", "")
        base_url = os.environ.get("GITLAB_URL", "https://gitlab.com")
        try:
            watchers.append(
                GitLabWatcher(
                    token=token,
                    base_url=base_url,
                    narrator=narrator,
                    persona=persona,
                    state=state,
                    poll_interval_seconds=int(gitlab_cfg.get("poll_interval_seconds", 30)),
                    stt=stt,
                    code_review=code_review,
                    event_bus=event_bus,
                )
            )
        except ValueError as e:
            print(f"[jarvis] gitlab watcher desativado: {e}", file=sys.stderr)

    return watchers


def start_ui_server(
    config: dict,
    *,
    event_bus: EventBus,
    text_handler,
    audio_handler,
    config_manager,
    mcp_manager,
    capabilities_provider,
    tool_registry_provider,
) -> tuple[threading.Thread, uvicorn.Server, str] | None:
    ui_cfg = config.get("ui") or {}
    if not ui_cfg.get("enabled", True):
        return None

    host = ui_cfg.get("host", "127.0.0.1")
    port = int(ui_cfg.get("port", 8765))
    app = create_app(
        event_bus=event_bus,
        text_handler=text_handler,
        audio_handler=audio_handler,
        config_manager=config_manager,
        mcp_manager=mcp_manager,
        capabilities_provider=capabilities_provider,
        tool_registry_provider=tool_registry_provider,
    )
    server_config = uvicorn.Config(
        app, host=host, port=port, log_level="info", access_log=False
    )
    server = uvicorn.Server(server_config)

    def run() -> None:
        try:
            asyncio.run(server.serve())
        except Exception as e:
            print(f"[ui-server] CRASHED: {e!r}", file=sys.stderr)

    t = threading.Thread(target=run, daemon=True, name="ui-server")
    t.start()

    deadline = time.monotonic() + 8.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)

    url = f"http://{host}:{port}"
    if not server.started:
        print(f"[jarvis] AVISO: UI server não subiu em 8s — UI estará offline.", file=sys.stderr, flush=True)
    else:
        print(f"[jarvis] UI server pronto em {url}", flush=True)
    return t, server, url


def main() -> int:
    load_dotenv(ROOT / ".env")
    config_manager = ConfigManager(CONFIG_PATH)
    config = config_manager.load()
    persona = build_persona(config)
    event_bus = EventBus()

    # MCP manager primeiro pra ter tools prontas quando o agente precisar.
    mcp_manager = MCPManager(MCP_CONFIG_PATH)
    mcp_manager.start()

    print("[jarvis] subindo UI server primeiro...", flush=True)
    pending_text_handler: list = []
    pending_audio_handler: list = []
    capabilities_state: dict = {}
    tool_registry_state: dict[str, Tool] = {}

    def capabilities_provider() -> dict:
        return {"capabilities": dict(capabilities_state)}

    def tool_registry_provider() -> dict[str, Tool]:
        return tool_registry_state

    ui = start_ui_server(
        config,
        event_bus=event_bus,
        text_handler=lambda t: (pending_text_handler[0](t) if pending_text_handler else None),
        audio_handler=lambda b: (pending_audio_handler[0](b) if pending_audio_handler else None),
        config_manager=config_manager,
        mcp_manager=mcp_manager,
        capabilities_provider=capabilities_provider,
        tool_registry_provider=tool_registry_provider,
    )

    try:
        engine = build_engine(config)
    except ValueError as e:
        print(f"[jarvis] Configuração de TTS incompleta: {e}", file=sys.stderr)
        return 1

    narrator = Narrator(
        engine=engine,
        volume=float(config.get("narrator", {}).get("volume", 0.9)),
        event_bus=event_bus,
    )
    state = WatcherState(STATE_PATH)
    stt = build_stt(config)
    workspace = build_workspace(config)
    code_review = build_code_review(config, narrator, persona, workspace)
    spotify = build_spotify(config)
    if spotify is not None:
        SpotifyDucker(spotify, event_bus)
    agent = build_agent(config, persona, mcp_manager)
    watchers = build_watchers(config, narrator, persona, state, stt, code_review, event_bus)

    commander: VoiceCommander | None = None
    hotkey_listener: HotkeyListener | None = None
    wake_listener: WakeWordListener | None = None
    if stt is not None:
        tool_registry_state["persona"] = build_persona_tool(persona)
        if spotify is not None:
            tool_registry_state["spotify"] = build_spotify_tool(spotify)
        gitlab_tool_inst = build_gitlab_tool_inst()
        if gitlab_tool_inst is not None:
            tool_registry_state["gitlab"] = build_gitlab_tool(gitlab_tool_inst)
        commander = VoiceCommander(
            stt=stt, narrator=narrator, persona=persona,
            spotify=spotify, event_bus=event_bus, agent=agent,
            commands=config.get("commands") or [],
            tool_registry=tool_registry_state,
        )
        pending_text_handler.append(commander.handle_text)
        pending_audio_handler.append(commander.handle_audio)
        combo = (config.get("hotkey") or {}).get("push_to_talk", "<ctrl>+<alt>+j")
        hotkey_listener = HotkeyListener(combo=combo, callback=commander.on_hotkey)

        # Wake word — opt-in via config. Sempre usa Vosk (precisa de streaming).
        wake_cfg = (config.get("wake_word") or {})
        if wake_cfg.get("enabled", False):
            vosk_for_wake = stt if hasattr(stt, "model") else build_vosk_stt(config)
            if vosk_for_wake is None or not hasattr(vosk_for_wake, "model"):
                print("[jarvis] wake word desativado: precisa do modelo Vosk.", file=sys.stderr)
            else:
                wake_listener = WakeWordListener(
                    model=vosk_for_wake.model,
                    on_detected=commander.on_wake_word,
                )
                # Pausa enquanto Jarvis fala/ouve pra não auto-detonar.
                def _on_event_pause(event):
                    if event.type in (EventType.SPEAKING_STARTED, EventType.LISTENING_STARTED):
                        wake_listener.pause()
                    elif event.type in (EventType.SPEAKING_ENDED, EventType.LISTENING_ENDED):
                        wake_listener.resume()
                event_bus.subscribe(_on_event_pause)

    boot = persona.boot_phrase()
    print(f"[jarvis] ({engine.name}) {boot}")
    capabilities = []
    if stt: capabilities.append("STT")
    if code_review: capabilities.append("code review" + (" (dry-run)" if code_review.dry_run else ""))
    if spotify: capabilities.append("spotify")
    if agent: capabilities.append("agent (Claude API)")
    if mcp_manager: capabilities.append(f"mcp ({len(mcp_manager.list_status())})")
    if hotkey_listener: capabilities.append(f"push-to-talk ({hotkey_listener.combo})")
    if wake_listener: capabilities.append("wake-word (jarvis)")
    if ui: capabilities.append(f"UI ({ui[2]})")
    if capabilities:
        print(f"[jarvis] capacidades: {', '.join(capabilities)}")

    capabilities_state.update({
        "stt": stt is not None,
        "spotify": spotify is not None,
        "code_review": code_review is not None,
        "agent": agent is not None,
        "wake_word": wake_listener is not None,
        "hotkey": hotkey_listener.combo if hotkey_listener else None,
        "engine": engine.name,
    })

    if ui and (config.get("ui") or {}).get("auto_open", True):
        launch_app_window(ui[2])

    event_bus.publish(EventType.BOOTED, capabilities=capabilities)
    narrator.speak(boot)

    if hotkey_listener is not None:
        hotkey_listener.start()
    if wake_listener is not None:
        wake_listener.start()

    if watchers:
        print(f"[jarvis] Monitorando: {', '.join(w.name for w in watchers)}")
    print("[jarvis] Ctrl+C para encerrar.")

    try:
        while True:
            for w in watchers:
                w.tick()
            time.sleep(LOOP_TICK_SECONDS)
    except KeyboardInterrupt:
        print("\n[jarvis] Encerrando...")
        if hotkey_listener is not None:
            hotkey_listener.stop()
        if wake_listener is not None:
            wake_listener.stop()
        if mcp_manager is not None:
            mcp_manager.stop()
        if ui is not None:
            ui[1].should_exit = True
        narrator.speak(persona.shutdown_phrase())
        return 0


if __name__ == "__main__":
    sys.exit(main())
