"""Briefing matinal — Jarvis fala primeiro.

Quando a Senhora liga o Jarvis no início do dia (ou após um intervalo
configurável), ele monta um sumário curto e fala antes de qualquer comando:
saudação por hora, pendências do GitLab, contexto recente do vault, e
sugestão da primeira ação.

Não roda em loop nem como cron — é disparado uma vez no boot (em background)
e pode ser disparado manualmente via UI.

State persistido em `.jarvis_state/briefing.json` (`{last_run_iso}`) pra
evitar briefing duplicado dentro do mesmo intervalo.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.event_bus import EventBus, EventType
from core.llm_client import LLMClient, LLMError
from core.narrator import Narrator
from core.persona import Persona
from core.vault import Vault

MAX_TOKENS = 400
TEMPERATURE = 0.4   # um pouco mais solto que o agente — o briefing tolera variação
RECENT_DAYS_FOR_BRIEFING = 3
DEFAULT_MIN_INTERVAL_HOURS = 6


_SYSTEM_PROMPT_TEMPLATE = """Você é o Jarvis, falando primeiro com {address} no \
início da sessão. Está fazendo um BRIEFING MATINAL.

REGRAS:
- Resposta CURTA: 2 a 4 frases, no máximo. Sem floreios.
- Saúde com "Bom dia", "Boa tarde" ou "Boa noite" conforme a hora.
- Mencione no máximo o que é MAIS relevante. Pendências críticas primeiro.
- Termine sugerindo UMA primeira ação concreta (opcional, só se houver sinal claro).
- Tom cordial e direto. Português brasileiro impecável.
- Trate sempre como "{address}" ou "{honorific}".
- NUNCA cite tecnologias internas: Claude, OpenAI, GPT, API, modelo, LLM são proibidos.
- Fale sempre em 1ª pessoa, como o Jarvis."""


class BriefingError(RuntimeError):
    pass


class BriefingService:
    def __init__(
        self,
        *,
        vault: Vault | None,
        persona: Persona,
        narrator: Narrator,
        event_bus: EventBus,
        llm_client: LLMClient,
        gitlab_tool: Any | None = None,
        state_path: Path,
        min_interval_hours: float = DEFAULT_MIN_INTERVAL_HOURS,
    ) -> None:
        self._vault = vault
        self._persona = persona
        self._narrator = narrator
        self._event_bus = event_bus
        self._gitlab = gitlab_tool
        self._state_path = state_path
        self._llm = llm_client
        self._min_interval = timedelta(hours=min_interval_hours)

    # ---------- API pública ----------

    def should_run(self, now: datetime | None = None) -> bool:
        """True se passou tempo suficiente desde o último briefing."""
        now = now or datetime.now()
        last = self._read_last_run()
        if last is None:
            return True
        return (now - last) >= self._min_interval

    def run(self, now: datetime | None = None) -> str:
        """Gera o briefing, fala, atualiza state, emite evento. Retorna o texto."""
        now = now or datetime.now()
        gitlab_text = self._collect_gitlab()
        recent_summary = self._collect_recent_interactions(now=now)

        text = self._generate(now=now, gitlab_text=gitlab_text, recent_summary=recent_summary)
        text = (text or "").strip()
        if not text:
            text = self._fallback_text(now=now, gitlab_text=gitlab_text)

        self._narrator.speak(text)
        self._emit_initiative(text)
        self._write_last_run(now)
        return text

    def run_if_due(self, now: datetime | None = None) -> str | None:
        """Atalho: roda se devido, senão None."""
        if not self.should_run(now=now):
            return None
        return self.run(now=now)

    # ---------- Coleta de dados ----------

    def _collect_gitlab(self) -> str:
        if self._gitlab is None:
            return "(GitLab indisponível)"
        try:
            return self._gitlab.count_pending()
        except Exception as e:
            return f"(falha consultando GitLab: {e})"

    def _collect_recent_interactions(self, *, now: datetime) -> str:
        """Resumo bruto dos últimos N dias — dado pro Claude resumir."""
        if self._vault is None:
            return "(vault indisponível)"
        turns = self._vault.read_recent_interactions(days=RECENT_DAYS_FOR_BRIEFING, now=now)
        if not turns:
            return "(sem interações recentes)"

        # Compacta: data hora + user_text truncado + tools usadas.
        lines: list[str] = []
        for turn in turns[-20:]:  # últimos 20 turnos no máximo
            date = turn.get("date", "?")
            time = turn.get("time", "?")
            user = (turn.get("user_text") or "").replace("\n", " ").strip()[:120]
            tools = turn.get("tools") or []
            tool_part = f" [{', '.join(tools)}]" if tools else ""
            lines.append(f"[{date} {time}] {user}{tool_part}")
        return "\n".join(lines)

    # ---------- Geração ----------

    def _generate(self, *, now: datetime, gitlab_text: str, recent_summary: str) -> str:
        system = _SYSTEM_PROMPT_TEMPLATE.format(
            address=self._persona.address,
            honorific=self._persona.honorific,
        )
        user_msg = (
            f"Hora atual: {now.strftime('%H:%M')} ({now.strftime('%A, %d de %B')})\n\n"
            f"Estado das pendências do GitLab:\n{gitlab_text}\n\n"
            f"Resumo bruto das interações dos últimos {RECENT_DAYS_FOR_BRIEFING} dias:\n"
            f"{recent_summary}\n\n"
            f"Faça o briefing matinal."
        )

        try:
            response = self._llm.complete(
                system=system,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
            )
        except LLMError as e:
            raise BriefingError(f"falha gerando briefing: {e!r}") from e

        return response.text

    def _fallback_text(self, *, now: datetime, gitlab_text: str) -> str:
        """Caso a chamada ao agente falhe ou venha vazia, gera algo determinístico."""
        greet = _greeting_for(now)
        return f"{greet}, {self._persona.address}. {gitlab_text}"

    # ---------- State ----------

    def _read_last_run(self) -> datetime | None:
        if not self._state_path.is_file():
            return None
        try:
            with self._state_path.open(encoding="utf-8") as f:
                data = json.load(f)
            iso = data.get("last_run_iso")
            return datetime.fromisoformat(iso) if iso else None
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def _write_last_run(self, now: datetime) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump({"last_run_iso": now.isoformat()}, f)
            tmp.replace(self._state_path)
        except OSError as e:
            print(f"[briefing] falha persistindo state: {e!r}")

    # ---------- Eventos ----------

    def _emit_initiative(self, text: str) -> None:
        try:
            self._event_bus.publish(
                EventType.JARVIS_INITIATIVE,
                label="Briefing matinal",
                text=text,
            )
        except Exception as e:
            print(f"[briefing] falha emitindo evento: {e!r}")


# ---------- Helpers ----------


def _greeting_for(now: datetime) -> str:
    hour = now.hour
    if 5 <= hour < 12:
        return "Bom dia"
    if 12 <= hour < 18:
        return "Boa tarde"
    return "Boa noite"
