"""GitLab Tool — ações invocáveis por comando de voz.

Usa a API GitLab (header PRIVATE-TOKEN). Espelha a leitura de Todos que
o `GitLabWatcher` faz, mas exposta em forma de ações on-demand:

- `list_pending_reviews` — frase com os MRs aguardando review.
- `count_pending` — frase com a contagem total e por categoria.

Os textos são prontos pra serem falados via TTS pelo VoiceCommander.
"""

from __future__ import annotations

import httpx


_ACTION_PT: dict[str, str] = {
    "review_requested": "review solicitado",
    "assigned": "atribuído",
    "mentioned": "menção",
    "directly_addressed": "menção direta",
    "marked": "marcado",
    "approval_required": "aprovação requerida",
    "build_failed": "build falhado",
    "unmergeable": "conflito",
    "added_approver": "adicionada como aprovadora",
}


class GitLabError(RuntimeError):
    pass


class GitLabTool:
    name = "gitlab"

    def __init__(self, token: str, base_url: str) -> None:
        if not token:
            raise ValueError("GITLAB_TOKEN ausente")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"PRIVATE-TOKEN": token},
            timeout=10.0,
        )

    # ---------- API pública ----------

    def list_pending_reviews(self, max_items: int = 5) -> str:
        """Frase com os MRs aguardando minha revisão. Limita a `max_items`."""
        todos = self._fetch_pending_todos()
        reviews = [
            t for t in todos
            if t.get("action_name") == "review_requested"
            and t.get("target_type") == "MergeRequest"
        ]
        if not reviews:
            return "Sem merge requests aguardando sua revisão."
        n = len(reviews)
        head = reviews[:max_items]
        items = "; ".join(_format_mr(t) for t in head)
        suffix = f", e mais {n - max_items}" if n > max_items else ""
        plural = "s" if n > 1 else ""
        return f"{n} merge request{plural} aguardando revisão: {items}{suffix}."

    def count_pending(self) -> str:
        """Frase com a contagem total e quebra por categoria."""
        todos = self._fetch_pending_todos()
        if not todos:
            return "Nenhuma pendência no GitLab."
        n = len(todos)
        by_action: dict[str, int] = {}
        for t in todos:
            key = t.get("action_name") or "outro"
            by_action[key] = by_action.get(key, 0) + 1
        # Ordena por contagem decrescente.
        parts = ", ".join(
            f"{count} {_pluralize(_ACTION_PT.get(action, action), count)}"
            for action, count in sorted(by_action.items(), key=lambda kv: -kv[1])
        )
        plural = "s" if n > 1 else ""
        return f"{n} pendência{plural} no GitLab: {parts}."

    # ---------- Internos ----------

    def _fetch_pending_todos(self) -> list[dict]:
        try:
            resp = self._client.get(
                "/api/v4/todos",
                params={"state": "pending", "per_page": 100},
            )
            resp.raise_for_status()
            return resp.json() or []
        except httpx.HTTPError as e:
            raise GitLabError(f"falha consultando GitLab: {e}") from e


def _format_mr(todo: dict) -> str:
    target = todo.get("target") or {}
    title = (target.get("title") or "sem título").strip()
    project = (todo.get("project") or {}).get("name", "")
    return f"{project} - {title}" if project else title


def _pluralize(noun: str, count: int) -> str:
    if count <= 1:
        return noun
    if noun.endswith(("s", "x", "z")):
        return noun
    if noun.endswith("ão"):
        return noun[:-2] + "ões"
    if noun.endswith("r"):
        return noun + "es"
    return noun + "s"
