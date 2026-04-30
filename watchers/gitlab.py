"""GitLab watcher. Polla a API de Todos e anuncia eventos novos via narrator.

Endpoint usado: GET /api/v4/todos?state=pending
Cobre: review_requested, mentioned, directly_addressed, assigned, marked, etc.
Docs: https://docs.gitlab.com/ee/api/todos.html

Quando a action é `review_requested` E o tool de code review está disponível
e o projeto está mapeado, o watcher entra num fluxo interativo: anuncia →
pergunta → escuta voz → parse → executa ou cancela.
"""

from __future__ import annotations

import httpx

from core.audio_recorder import record
from core.intent import YesNo, parse_yes_no
from core.narrator import SpeakingNarrator
from core.persona import Persona
from core.state import WatcherState
from core.stt import VoskSTT
from tools.code_review import CodeReviewTool, ReviewRequest
from watchers.base import Watcher

VOICE_RESPONSE_DURATION_SECONDS = 5.0


class GitLabWatcher(Watcher):
    name = "gitlab"

    def __init__(
        self,
        token: str,
        base_url: str,
        narrator: SpeakingNarrator,
        persona: Persona,
        state: WatcherState,
        poll_interval_seconds: int = 30,
        stt: VoskSTT | None = None,
        code_review: CodeReviewTool | None = None,
    ) -> None:
        super().__init__(poll_interval_seconds)
        if not token:
            raise ValueError("GITLAB_TOKEN ausente")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._narrator = narrator
        self._persona = persona
        self._state = state
        self._stt = stt
        self._code_review = code_review
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={"PRIVATE-TOKEN": token},
            timeout=10.0,
        )

    def poll(self) -> None:
        todos = self._fetch_pending_todos()

        first_run = not self._state.is_namespace_initialized(self.name)
        if first_run:
            self._state.initialize_namespace(self.name)
            if todos:
                most_recent = todos[0]
                print(f"[gitlab] Primeira execução: {len(todos)} pendentes — anunciando o mais recente como amostra.")
                self._handle_todo(most_recent, interactive=False)
            for todo in todos:
                self._state.mark_seen(self.name, todo["id"])
            self._state.save()
            return

        new_todos = [t for t in todos if not self._state.has_seen(self.name, t["id"])]
        if not new_todos:
            return

        for todo in new_todos:
            self._handle_todo(todo, interactive=True)
            self._state.mark_seen(self.name, todo["id"])
        self._state.save()

    def _fetch_pending_todos(self) -> list[dict]:
        resp = self._client.get(
            "/api/v4/todos",
            params={"state": "pending", "per_page": 50},
        )
        resp.raise_for_status()
        return resp.json()

    def _handle_todo(self, todo: dict, *, interactive: bool) -> None:
        self._announce(todo)
        action = todo.get("action_name", "")
        target_type = todo.get("target_type", "")

        # Fluxo interativo só pra review_requested em MR (não em primeira execução).
        if not interactive:
            return
        if action != "review_requested" or target_type != "MergeRequest":
            return
        if not (self._stt and self._code_review):
            return

        project = todo.get("project") or {}
        project_full_path = project.get("path_with_namespace") or project.get("name", "")
        if not self._code_review.can_review(project_full_path):
            print(f"[gitlab] projeto {project_full_path!r} sem dir local configurado — pulando prompt de revisão.")
            return

        target = todo.get("target") or {}
        source_branch = target.get("source_branch", "")
        if not source_branch:
            return

        self._prompt_and_review(
            project_full_path=project_full_path,
            source_branch=source_branch,
            mr_url=todo.get("target_url", ""),
            mr_title=target.get("title", ""),
        )

    def _announce(self, todo: dict) -> None:
        action = todo.get("action_name", "")
        author = todo.get("author", {}).get("name", "alguém")
        target = todo.get("target") or {}
        target_type = todo.get("target_type", "")
        title = target.get("title") or target.get("name") or "(sem título)"
        web_url = todo.get("target_url") or target.get("web_url", "")
        project = (todo.get("project") or {}).get("name", "")

        phrase = self._persona.announce_gitlab_event(
            action=action,
            author=author,
            target_type=target_type,
            title=title,
            project=project,
        )
        print(f"[gitlab] {phrase}")
        if web_url:
            print(f"         {web_url}")
        self._narrator.speak(phrase)

    def _prompt_and_review(
        self,
        *,
        project_full_path: str,
        source_branch: str,
        mr_url: str,
        mr_title: str,
    ) -> None:
        assert self._stt is not None
        assert self._code_review is not None

        self._narrator.speak(
            f"Deseja que eu execute a revisão, {self._persona.honorific}?"
        )
        print(f"[gitlab] aguardando resposta de voz ({VOICE_RESPONSE_DURATION_SECONDS:.0f}s)...")
        audio = record(VOICE_RESPONSE_DURATION_SECONDS)
        text = self._stt.transcribe(audio)
        intent = parse_yes_no(text)
        print(f"[gitlab] transcrição: {text!r} → intent: {intent.value}")

        if intent == YesNo.YES:
            self._code_review.run_async(
                ReviewRequest(
                    project_full_path=project_full_path,
                    source_branch=source_branch,
                    mr_url=mr_url,
                    mr_title=mr_title,
                )
            )
        elif intent == YesNo.NO:
            self._narrator.speak(f"Como queira, {self._persona.honorific}.")
        else:
            self._narrator.speak(
                f"Não compreendi sua resposta, {self._persona.honorific}. "
                f"Cancelando o prompt de revisão."
            )
