"""Parser de intenção local — sem chamada a LLM, regex/keywords PT-BR."""

from __future__ import annotations

from enum import Enum


class YesNo(Enum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


# Negativas primeiro: "claro que não" deve dar NO, não YES.
_NO_KEYWORDS = (
    "não",
    "nao",
    "negativo",
    "negativa",
    "nem",
    "depois",
    "agora não",
    "agora nao",
    "para",
    "pare",
    "cancela",
    "cancelar",
    "ignora",
    "ignorar",
    "esquece",
)

_YES_KEYWORDS = (
    "sim",
    "claro",
    "vai",
    "vai lá",
    "manda",
    "manda ver",
    "manda bala",
    "executa",
    "executar",
    "pode",
    "pode sim",
    "afirmativo",
    "afirmativa",
    "ok",
    "okay",
    "tá",
    "ta",
    "isso",
    "positivo",
    "bora",
    "demorou",
)


def _has_word(text: str, word: str) -> bool:
    """Match com fronteiras simples (espaço/início/fim)."""
    padded = f" {text} "
    return f" {word} " in padded


def parse_yes_no(text: str) -> YesNo:
    """Classifica a transcrição em YES/NO/UNCLEAR.

    Regra: NO tem prioridade sobre YES — "claro que não" → NO.
    """
    if not text:
        return YesNo.UNCLEAR

    normalized = text.lower().strip()

    for kw in _NO_KEYWORDS:
        if _has_word(normalized, kw):
            return YesNo.NO

    for kw in _YES_KEYWORDS:
        if _has_word(normalized, kw):
            return YesNo.YES

    return YesNo.UNCLEAR
