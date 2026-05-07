"""Agente conversacional do Jarvis — fallback quando dispatch local não casa.

Provider-agnóstico: aceita qualquer LLMClient (Ollama local, Anthropic na
nuvem, etc.). Roda loop tool_use até stop_reason == end_turn ou bater
MAX_TOOL_TURNS. A resposta final é narrada pelo Narrator.

Memória:
- **Vault** (long-term): conteúdo curado de perfil/projetos injetado no
  system prompt a cada turno.
- **Sessão** (short-term): últimos N turnos em RAM, anexados antes da
  mensagem nova pra dar continuidade conversacional.
- **Persistência**: após cada turno, append em `interacoes/YYYY-MM-DD.md`
  via Vault.record_interaction.

Tool calling com modelos pequenos (Llama 3.2 3B, etc.) é instável — o
modelo pode devolver tool_call mal-formado ou ignorar o schema. O loop
trata isso como "fim de turno" pra não travar.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any, Callable

from core.event_bus import EventBus, EventType
from core.llm_client import LLMClient, LLMError, ToolCall
from core.vault import Vault

MAX_TOKENS = 600
MAX_TOOL_TURNS = 8
DEFAULT_TEMPERATURE = 0.2
SESSION_TURNS_KEPT = 6  # últimos N turnos (user+assistant pairs) mantidos em RAM
PREVIEW_MAX_CHARS = 240  # truncamento de previews em eventos pra UI

# Modos de pensamento — prefixo injetado como bloco extra no system prompt
# do turno. None = comportamento padrão da persona.
THINKING_MODES: dict[str, str] = {
    "critico": (
        "Modo Crítico: aponte problemas, riscos, edge cases e premissas frágeis. "
        "Seja construtiva mas direta. Termine com a maior dúvida que ficou."
    ),
    "sintetizador": (
        "Modo Sintetizador: comprima ao essencial. Resposta em no máximo 3 frases. "
        "Sem floreio."
    ),
    "advogado": (
        "Modo Advogado do Diabo: defenda o ponto MENOS óbvio. Mesmo se for contrário "
        "ao que parece certo. O objetivo é stress-test do raciocínio, não conformidade."
    ),
}


class JarvisAgent:
    def __init__(
        self,
        system_prompt: str,
        llm_client: LLMClient,
        tool_list_provider: Callable[[], list[dict[str, Any]]],
        tool_caller: Callable[[str, dict[str, Any]], str],
        vault: Vault | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        event_bus: EventBus | None = None,
        skills_provider: Callable[[], str] | None = None,
    ) -> None:
        self._base_system_prompt = system_prompt
        self._llm = llm_client
        self._tool_list_provider = tool_list_provider
        self._tool_caller = tool_caller
        self._vault = vault
        self._temperature = temperature
        self._event_bus = event_bus
        # Provider em vez de string fixa: mudanças na curadoria refletem
        # imediatamente sem precisar reinstanciar o agente.
        self._skills_provider = skills_provider
        # Sessão: deque de turnos completos (cada turno = lista de mensagens).
        self._session: deque[list[dict[str, Any]]] = deque(maxlen=SESSION_TURNS_KEPT)

    def respond(self, user_text: str, mode: str | None = None) -> str:
        """Roda o turno. Retorna o texto final pra falar pra usuária.

        `mode` opcional: 'critico' | 'sintetizador' | 'advogado' | None.
        """
        self._emit(EventType.AGENT_TURN_STARTED, user_text=user_text)

        mode_key = (mode or "").strip().lower() or None
        if mode_key and mode_key not in THINKING_MODES:
            mode_key = None

        # Mensagens deste turno: histórico da sessão + novo input.
        messages: list[dict[str, Any]] = []
        for turn in self._session:
            messages.extend(turn)
        messages.append({"role": "user", "content": user_text})

        new_turn_messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_text}
        ]
        tools_used: list[str] = []

        for iteration in range(MAX_TOOL_TURNS):
            label = "pensando..." if iteration == 0 else "consultando ferramentas..."
            self._emit(EventType.AGENT_THINKING, label=label)

            anthropic_tools = self._tool_list_provider()
            openai_tools = [_anthropic_to_openai_tool(t) for t in anthropic_tools] if anthropic_tools else None
            system = self._build_system(mode_key)

            try:
                response = self._llm.complete(
                    system=system,
                    messages=messages,
                    tools=openai_tools,
                    max_tokens=MAX_TOKENS,
                    temperature=self._temperature,
                )
            except LLMError as e:
                # Falha de IO — encerra o turno graciosamente.
                err_msg = f"Senhora, o motor de raciocínio falhou: {e}"
                self._emit(
                    EventType.AGENT_TURN_ENDED,
                    assistant_text=err_msg,
                    tools_used=list(tools_used),
                )
                return err_msg

            # Acumula a resposta do assistente.
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.text or "",
            }
            if response.tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": tc.id, "name": tc.name, "input": tc.input}
                    for tc in response.tool_calls
                ]
            messages.append(assistant_msg)
            new_turn_messages.append(assistant_msg)

            if response.stop_reason != "tool_use" or not response.tool_calls:
                # Final — colhe o texto, registra no vault e na sessão.
                final_text = response.text or ""
                self._commit_turn(new_turn_messages, user_text, final_text, tools_used)
                self._emit(
                    EventType.AGENT_TURN_ENDED,
                    assistant_text=final_text,
                    tools_used=list(tools_used),
                )
                return final_text

            # Executa todas as tools pedidas neste turno.
            for tc in response.tool_calls:
                tools_used.append(tc.name)
                self._emit(
                    EventType.AGENT_TOOL_CALL,
                    tool=tc.name,
                    args_preview=_preview(tc.input),
                    call_id=tc.id,
                )
                error = False
                try:
                    result_text = self._tool_caller(tc.name, tc.input or {})
                except Exception as e:
                    result_text = f"[tool exception] {e!r}"
                    error = True
                self._emit(
                    EventType.AGENT_TOOL_RESULT,
                    tool=tc.name,
                    result_preview=_preview(result_text),
                    call_id=tc.id,
                    error=error,
                )
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                }
                messages.append(tool_msg)
                new_turn_messages.append(tool_msg)

        # Estourou MAX_TOOL_TURNS.
        fallback = "Senhora, não consegui concluir a tarefa nesta tentativa."
        self._emit(
            EventType.AGENT_TURN_ENDED,
            assistant_text=fallback,
            tools_used=list(tools_used),
        )
        return fallback

    # ---------- Internals ----------

    def _build_system(self, mode_key: str | None = None) -> str:
        """System prompt = persona + (opcional) contexto do vault + (opcional) modo."""
        parts: list[str] = [self._base_system_prompt]
        if self._vault is not None:
            ctx = self._vault.build_context()
            if ctx:
                parts.append(
                    "Contexto persistente sobre a Senhora e seus projetos "
                    "(do vault local). Use como referência ao responder.\n\n"
                    + ctx
                )
        if self._skills_provider is not None:
            try:
                summary = (self._skills_provider() or "").strip()
            except Exception as e:
                print(f"[agent] skills_provider falhou: {e!r}")
                summary = ""
            if summary:
                # Só awareness — você não executa essas skills, só sabe que a
                # Senhora as tem disponíveis no terminal Claude Code.
                parts.append(
                    "Skills e slash commands que a Senhora tem instalados no Claude Code "
                    "(você NÃO os executa — apenas pode mencioná-los pelo nome quando "
                    "ela perguntar o que tem disponível): "
                    + summary
                )
        if mode_key and mode_key in THINKING_MODES:
            parts.append(THINKING_MODES[mode_key])
        return "\n\n".join(parts)

    def _commit_turn(
        self,
        new_turn_messages: list[dict[str, Any]],
        user_text: str,
        assistant_text: str,
        tools_used: list[str],
    ) -> None:
        """Registra turno concluído na sessão e no vault."""
        self._session.append(new_turn_messages)
        if self._vault is not None and assistant_text:
            try:
                self._vault.record_interaction(
                    user_text=user_text,
                    assistant_text=assistant_text,
                    tools_used=tools_used or None,
                )
            except Exception as e:
                print(f"[agent] falha ao gravar no vault: {e!r}")

    def _emit(self, event_type: EventType, **data: Any) -> None:
        if self._event_bus is None:
            return
        try:
            self._event_bus.publish(event_type, **data)
        except Exception as e:
            print(f"[agent] falha emitindo {event_type}: {e!r}")


def _anthropic_to_openai_tool(t: dict[str, Any]) -> dict[str, Any]:
    """MCP devolve no formato Anthropic ({name, description, input_schema});
    o LLMClient espera OpenAI ({type: function, function: {...}})."""
    return {
        "type": "function",
        "function": {
            "name": t.get("name") or "",
            "description": t.get("description") or "",
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
        },
    }


def _preview(obj: Any) -> str:
    """String curta segura pra mandar pra UI."""
    if isinstance(obj, str):
        s = obj
    else:
        try:
            s = json.dumps(obj, ensure_ascii=False, default=str)
        except Exception:
            s = repr(obj)
    if len(s) > PREVIEW_MAX_CHARS:
        return s[:PREVIEW_MAX_CHARS] + "…"
    return s
