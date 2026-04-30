"""Smoke test do fluxo MR review interativo.

Pega o todo de `review_requested` mais recente do GitLab e força o fluxo
completo (anuncia → pergunta → escuta → parse → executa/cancela), pulando
toda a lógica de "primeira execução" do watcher real.

NÃO chama TTS por padrão (use --speak pra ouvir). Mas SIM grava microfone +
roda STT — esses são locais.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

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
from core.stt import VoskSTT
from core.tts import build_engine
from core.workspace import WorkspaceManager
from tools.code_review import CodeReviewTool, project_configs_from_yaml
from watchers.gitlab import GitLabWatcher


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speak", action="store_true", help="Chama TTS de verdade")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    with (ROOT / "config" / "jarvis.yaml").open(encoding="utf-8") as f:
        config = yaml.safe_load(f)

    persona = Persona(
        user_name=config["user"]["name"], honorific=config["user"]["honorific"],
    )
    if args.speak:
        narrator: Narrator | DryRunNarrator = Narrator(
            engine=build_engine(config),
            volume=float(config["narrator"]["volume"]),
        )
    else:
        narrator = DryRunNarrator()
        print("[test] dry-run TTS — use --speak pra ouvir.")

    model_dir_str = (config.get("stt") or {}).get("model_dir", "")
    model_dir = ROOT / model_dir_str
    if not model_dir.is_dir():
        print(f"Modelo Vosk não encontrado em {model_dir}.", file=sys.stderr)
        return 1
    stt = VoskSTT(model_dir)

    code_review_cfg = (config.get("tools") or {}).get("code_review") or {}
    workspace = None
    if code_review_cfg.get("auto_clone", True) and os.environ.get("GITLAB_TOKEN"):
        workspace = WorkspaceManager(
            root=ROOT / ".jarvis_state" / "repos",
            gitlab_base_url=os.environ.get("GITLAB_URL", "https://gitlab.com"),
            gitlab_token=os.environ["GITLAB_TOKEN"],
        )
    code_review = CodeReviewTool(
        project_dirs=project_configs_from_yaml(code_review_cfg.get("project_dirs") or {}),
        commands=code_review_cfg.get("commands") or {},
        narrator=narrator,
        persona=persona,
        dry_run=True,  # sempre dry-run no teste
        workspace=workspace,
    )

    # State temporário (não toca o real)
    tmp = Path(tempfile.gettempdir()) / "jarvis_test_review_state.json"
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
        stt=stt,
        code_review=code_review,
    )

    todos = watcher._fetch_pending_todos()  # noqa: SLF001 — uso explícito p/ teste
    review_todos = [
        t for t in todos
        if t.get("action_name") == "review_requested"
        and t.get("target_type") == "MergeRequest"
    ]
    if not review_todos:
        print("[test] nenhum review_requested pendente — não há o que testar.")
        return 0

    todo = review_todos[0]
    print(f"[test] usando MR: {(todo.get('target') or {}).get('title', '?')[:80]}")
    print(f"[test] projeto: {(todo.get('project') or {}).get('path_with_namespace', '?')}")

    # Força o caminho interativo do watcher.
    state.initialize_namespace(watcher.name)  # marca como "não primeira exec"
    watcher._handle_todo(todo, interactive=True)  # noqa: SLF001
    print("[test] fluxo concluído.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
