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
from core.stt import VoskSTT
from core.tts import build_engine
from tools.code_review import CodeReviewTool, project_configs_from_yaml
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


def build_stt(config: dict) -> VoskSTT | None:
    stt_cfg = config.get("stt", {}) or {}
    model_dir_str = stt_cfg.get("model_dir")
    if not model_dir_str:
        return None
    model_dir = ROOT / model_dir_str if not Path(model_dir_str).is_absolute() else Path(model_dir_str)
    if not model_dir.is_dir():
        print(f"[jarvis] modelo Vosk ausente em {model_dir} — STT desativado.", file=sys.stderr)
        print(f"[jarvis] rode `py scripts/setup_stt.py` pra baixá-lo.", file=sys.stderr)
        return None
    return VoskSTT(model_dir)


def build_code_review(config: dict, narrator: Narrator, persona: Persona) -> CodeReviewTool | None:
    cfg = (config.get("tools") or {}).get("code_review") or {}
    if not cfg.get("enabled"):
        return None
    return CodeReviewTool(
        project_dirs=project_configs_from_yaml(cfg.get("project_dirs") or {}),
        commands=cfg.get("commands") or {},
        narrator=narrator,
        persona=persona,
        dry_run=bool(cfg.get("dry_run", True)),
    )


def build_watchers(
    config: dict,
    narrator: Narrator,
    persona: Persona,
    state: WatcherState,
    stt: VoskSTT | None,
    code_review: CodeReviewTool | None,
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
    stt = build_stt(config)
    code_review = build_code_review(config, narrator, persona)
    watchers = build_watchers(config, narrator, persona, state, stt, code_review)

    boot = persona.boot_phrase()
    print(f"[jarvis] ({engine.name}) {boot}")
    capabilities = []
    if stt:
        capabilities.append("STT")
    if code_review:
        capabilities.append("code review" + (" (dry-run)" if code_review.dry_run else ""))
    if capabilities:
        print(f"[jarvis] capacidades: {', '.join(capabilities)}")
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
