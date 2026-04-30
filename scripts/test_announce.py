"""Roda o GitLab watcher uma única vez com state fresco.

Usa um arquivo de state temporário para forçar o caminho de "primeira execução"
e ouvir o Jarvis anunciando um todo real. Não toca no state real.
"""

from __future__ import annotations

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

from core.narrator import Narrator
from core.persona import Persona
from core.state import WatcherState
from core.tts import build_engine
from watchers.gitlab import GitLabWatcher

load_dotenv(ROOT / ".env")

with (ROOT / "config" / "jarvis.yaml").open(encoding="utf-8") as f:
    config = yaml.safe_load(f)

persona = Persona(
    user_name=config["user"]["name"],
    honorific=config["user"]["honorific"],
)
narrator = Narrator(
    engine=build_engine(config),
    volume=float(config["narrator"]["volume"]),
)

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
