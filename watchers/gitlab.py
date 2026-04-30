"""GitLab watcher. Polla a API de Todos e anuncia eventos novos via narrator.

Endpoint usado: GET /api/v4/todos?state=pending
Cobre: review_requested, mentioned, directly_addressed, assigned, marked, etc.
Docs: https://docs.gitlab.com/ee/api/todos.html
"""

from __future__ import annotations

import httpx

from core.narrator import SpeakingNarrator
from core.persona import Persona
from core.state import WatcherState
from watchers.base import Watcher


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
    ) -> None:
        super().__init__(poll_interval_seconds)
        if not token:
            raise ValueError("GITLAB_TOKEN ausente")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._narrator = narrator
        self._persona = persona
        self._state = state
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
            # Anuncia o mais recente como amostra (valida o pipeline) e
            # silencia o restante do backlog.
            if todos:
                most_recent = todos[0]
                print(f"[gitlab] Primeira execução: {len(todos)} pendentes — anunciando o mais recente como amostra.")
                self._announce(most_recent)
            for todo in todos:
                self._state.mark_seen(self.name, todo["id"])
            self._state.save()
            return

        new_todos = [t for t in todos if not self._state.has_seen(self.name, t["id"])]
        if not new_todos:
            return

        for todo in new_todos:
            self._announce(todo)
            self._state.mark_seen(self.name, todo["id"])
        self._state.save()

    def _fetch_pending_todos(self) -> list[dict]:
        resp = self._client.get(
            "/api/v4/todos",
            params={"state": "pending", "per_page": 50},
        )
        resp.raise_for_status()
        return resp.json()

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
