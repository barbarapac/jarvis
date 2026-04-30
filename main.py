"""Entry point do Jarvis. Boot + event loop com watchers habilitados."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Força UTF-8 no stdout/stderr no Windows pra evitar mojibake nos prints.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

import yaml
from dotenv import load_dotenv

from core.narrator import Narrator
from core.persona import Persona
from core.state import WatcherState
from core.tts import build_engine
from watchers.base import Watcher
from watchers.gitlab import GitLabWatcher

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config" / "jarvis.yaml"
STATE_PATH = ROOT / ".jarvis_state" / "watchers.json"

# Cadência do event loop. Watchers individuais respeitam seu próprio poll_interval.
LOOP_TICK_SECONDS = 1.0


def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_persona(config: dict) -> Persona:
    user = config.get("user", {})
    return Persona(
        user_name=user.get("name", "Usuário"),
        honorific=user.get("honorific", "Senhor(a)"),
    )


def build_watchers(
    config: dict,
    narrator: Narrator,
    persona: Persona,
    state: WatcherState,
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
                )
            )
        except ValueError as e:
            print(f"[jarvis] gitlab watcher desativado: {e}", file=sys.stderr)

    return watchers


def main() -> int:
    load_dotenv(ROOT / ".env")
    config = load_config()
    persona = build_persona(config)

    try:
        engine = build_engine(config)
    except ValueError as e:
        print(f"[jarvis] Configuração de TTS incompleta: {e}", file=sys.stderr)
        return 1

    narrator = Narrator(
        engine=engine,
        volume=float(config.get("narrator", {}).get("volume", 0.9)),
    )
    state = WatcherState(STATE_PATH)
    watchers = build_watchers(config, narrator, persona, state)

    boot = persona.boot_phrase()
    print(f"[jarvis] ({engine.name}) {boot}")
    narrator.speak(boot)

    if not watchers:
        print("[jarvis] Nenhum watcher habilitado. Encerrando.")
        return 0

    print(f"[jarvis] Monitorando: {', '.join(w.name for w in watchers)}")
    print("[jarvis] Ctrl+C para encerrar.")

    try:
        while True:
            for w in watchers:
                w.tick()
            time.sleep(LOOP_TICK_SECONDS)
    except KeyboardInterrupt:
        print("\n[jarvis] Encerrando...")
        narrator.speak(persona.shutdown_phrase())
        return 0


if __name__ == "__main__":
    sys.exit(main())
