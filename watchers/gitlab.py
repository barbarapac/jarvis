"""GitLab watcher. Polla a API de Todos e anuncia eventos novos via narrator.

Endpoint usado: GET /api/v4/todos?state=pending
Cobre: review_requested, mentioned, directly_addressed, assigned, marked, etc.
Docs: https://docs.gitlab.com/ee/api/todos.html

Quando a action é `review_requested` E o tool de code review está disponível
e pode revisar o projeto, o watcher entra num fluxo interativo: anúncio +
pergunta na MESMA chamada TTS → escuta voz com VAD → parse → executa ou
cancela.
"""

from __future__ import annotations

import httpx

from core.audio_recorder import record_with_vad
from core.event_bus import EventBus, EventType
from core.intent import YesNo, parse_yes_no
from core.narrator import SpeakingNarrator
from core.persona import Persona
from core.state import WatcherState
from core.stt import VoskSTT
from tools.code_review import CodeReviewTool, ReviewRequest
from watchers.base import Watcher

VOICE_RESPONSE_MAX_DURATION = 6.0


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
        event_bus: EventBus | None = None,
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
        self._event_bus = event_bus
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
        action = todo.get("action_name", "")
        target_type = todo.get("target_type", "")

        is_review_prompt = (
            interactive
            and action == "review_requested"
            and target_type == "MergeRequest"
            and self._stt is not None
            and self._code_review is not None
            and self._code_review.can_review(self._project_full_path(todo))
        )

        if is_review_prompt:
            self._announce_with_review_prompt(todo)
            self._listen_and_dispatch(todo)
        else:
            self._announce(todo)

    def _announce_with_review_prompt(self, todo: dict) -> None:
        """Anuncia + pergunta numa única chamada TTS (economiza ~2s vs 2 chamadas)."""
        base_phrase = self._build_announcement(todo)
        prompt = f"Deseja que eu execute a revisão, {self._persona.honorific}?"
        combined = f"{base_phrase} {prompt}"
        print(f"[gitlab] {combined}")
        web_url = todo.get("target_url", "")
        if web_url:
            print(f"         {web_url}")
        self._publish_announce(todo, combined, prompt=True)
        self._narrator.speak(combined)

    def _announce(self, todo: dict) -> None:
        phrase = self._build_announcement(todo)
        print(f"[gitlab] {phrase}")
        web_url = todo.get("target_url", "")
        if web_url:
            print(f"         {web_url}")
        self._publish_announce(todo, phrase, prompt=False)
        self._narrator.speak(phrase)

    def _publish_announce(self, todo: dict, text: str, *, prompt: bool) -> None:
        if not self._event_bus:
            return
        self._event_bus.publish(
            EventType.GITLAB_ANNOUNCE,
            text=text,
            prompt=prompt,
            url=todo.get("target_url", ""),
            action=todo.get("action_name", ""),
            project=(todo.get("project") or {}).get("path_with_namespace", ""),
        )

    def _build_announcement(self, todo: dict) -> str:
        action = todo.get("action_name", "")
        author = todo.get("author", {}).get("name", "alguém")
        target = todo.get("target") or {}
        target_type = todo.get("target_type", "")
        title = target.get("title") or target.get("name") or "(sem título)"
        project = (todo.get("project") or {}).get("name", "")
        return self._persona.announce_gitlab_event(
            action=action,
            author=author,
            target_type=target_type,
            title=title,
            project=project,
        )

    def _listen_and_dispatch(self, todo: dict) -> None:
        assert self._stt is not None
        assert self._code_review is not None

        print(f"[gitlab] aguardando resposta de voz (até {VOICE_RESPONSE_MAX_DURATION:.0f}s, com VAD)...")
        audio = record_with_vad(max_duration_seconds=VOICE_RESPONSE_MAX_DURATION)
        text = self._stt.transcribe(audio)
        intent = parse_yes_no(text)
        print(f"[gitlab] transcrição: {text!r} → intent: {intent.value}")

        if intent == YesNo.YES:
            target = todo.get("target") or {}
            self._code_review.run_async(
                ReviewRequest(
                    project_full_path=self._project_full_path(todo),
                    source_branch=target.get("source_branch", ""),
                    mr_url=todo.get("target_url", ""),
                    mr_title=target.get("title", ""),
                )
            )
        elif intent == YesNo.NO:
            self._narrator.speak(f"Como queira, {self._persona.honorific}.")
        else:
            self._narrator.speak(
                f"Não compreendi sua resposta, {self._persona.honorific}."
            )

    @staticmethod
    def _project_full_path(todo: dict) -> str:
        project = todo.get("project") or {}
        return project.get("path_with_namespace") or project.get("name", "")
