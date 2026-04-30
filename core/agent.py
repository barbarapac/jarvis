"""Agente Claude com tool-calling — fallback quando dispatch local não casa.

Usa o SDK `anthropic` síncrono. Recebe a lista agregada de tools dos
MCPs e roda o loop tool_use até `stop_reason == end_turn`. A resposta
final é narrada pelo Narrator.

Prompt caching habilitado no system prompt e na lista de tools (são
estáveis entre turnos da mesma sessão).
"""

from __future__ import annotations

import os
from typing import Any, Callable

DEFAULT_MODEL = "claude-opus-4-7"
MAX_TOKENS = 1024
MAX_TOOL_TURNS = 8


class JarvisAgent:
    def __init__(
        self,
        system_prompt: str,
        tool_list_provider: Callable[[], list[dict[str, Any]]],
        tool_caller: Callable[[str, dict[str, Any]], str],
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
    ) -> None:
        self._system_prompt = system_prompt
        self._tool_list_provider = tool_list_provider
        self._tool_caller = tool_caller
        self._model = model
        # Late import pra não exigir o pacote quando o agente não estiver em uso.
        from anthropic import Anthropic

        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY ausente — agente Claude desativado.")
        self._client = Anthropic(api_key=key)

    def respond(self, user_text: str) -> str:
        """Roda o turno. Retorna o texto final pra falar pra usuária."""
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_text}
        ]

        for _ in range(MAX_TOOL_TURNS):
            tools = self._tool_list_provider()
            kwargs: dict[str, Any] = {
                "model": self._model,
                "max_tokens": MAX_TOKENS,
                "system": [
                    {
                        "type": "text",
                        "text": self._system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                "messages": messages,
            }
            if tools:
                # Cache_control no último item da lista de tools cobre toda ela.
                tools_with_cache = [dict(t) for t in tools]
                tools_with_cache[-1]["cache_control"] = {"type": "ephemeral"}
                kwargs["tools"] = tools_with_cache

            response = self._client.messages.create(**kwargs)

            # Acumula a resposta do assistente nas mensagens.
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                # Final — colhe o texto.
                return _extract_text(response.content) or ""

            # Executa todas as tools pedidas neste turno.
            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) == "tool_use":
                    try:
                        result_text = self._tool_caller(block.name, block.input or {})
                    except Exception as e:
                        result_text = f"[tool exception] {e!r}"
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})

        return "Desculpe, não consegui concluir a tarefa nesta tentativa."


def _extract_text(blocks: list[Any]) -> str:
    parts = []
    for b in blocks:
        if getattr(b, "type", None) == "text":
            parts.append(b.text)
    return "\n".join(parts).strip()
