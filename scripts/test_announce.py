"""Roda o GitLab watcher uma única vez com state fresco.

Por padrão, NÃO chama a fish.audio — apenas imprime o que seria falado.
Use --speak pra ouvir de verdade.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.narrator import DryRunNarrator, Narrator
from core.persona import Persona
from core.state import WatcherState
from core.tts import build_engine
from watchers.gitlab import GitLabWatcher


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--speak",
        action="store_true",
        help="Realmente chama o TTS e toca o áudio (default: dry-run, só imprime).",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    with (ROOT / "config" / "jarvis.yaml").open(encoding="utf-8") as f:
        config = yaml.safe_load(f)

    persona = Persona(
        user_name=config["user"]["name"],
        honorific=config["user"]["honorific"],
    )

    if args.speak:
        narrator: Narrator | DryRunNarrator = Narrator(
            engine=build_engine(config),
            volume=float(config["narrator"]["volume"]),
        )
        print("[test] modo --speak: vai chamar TTS de verdade.")
    else:
        narrator = DryRunNarrator()
        print("[test] dry-run: TTS desativado. Use --speak pra ouvir.")

    # State temporário pra forçar primeira execução (path inexistente).
    tmp = Path(tempfile.gettempdir()) / "jarvis_test_state.json"
    if tmp.exists():
        tmp.unlink()
    state = WatcherState(tmp)

    watcher = GitLabWatcher(
        token=os.environ["GITLAB_TOKEN"],
        base_url=os.environ.get("GITLAB_URL", "https://gitlab.com"),
        narrator=narrator,
        persona=persona,
        state=state,
        poll_interval_seconds=0,
    )

    print("[test] executando um poll com state fresco...")
    watcher.poll()
    print("[test] feito.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
