"""Personalidade do Jarvis. Define tom, vocabulário e estilo das respostas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


# Mapeia action_name da API de Todos do GitLab para verbos em PT-BR.
_GITLAB_ACTIONS_PT: dict[str, str] = {
    "assigned": "atribuiu a você",
    "review_requested": "solicitou sua revisão em",
    "mentioned": "mencionou você em",
    "directly_addressed": "se dirigiu diretamente a você em",
    "marked": "marcou para sua atenção",
    "approval_required": "requer sua aprovação em",
    "unmergeable": "reportou conflitos em",
    "build_failed": "falhou no build de",
    "added_approver": "te adicionou como aprovadora em",
}

_GITLAB_TARGETS_PT: dict[str, str] = {
    "MergeRequest": "um merge request",
    "Issue": "uma issue",
    "Commit": "um commit",
    "DesignManagement::Design": "um design",
    "AlertManagement::Alert": "um alerta",
    "Epic": "um épico",
}


@dataclass(frozen=True)
class Persona:
    user_name: str
    honorific: str

    @property
    def address(self) -> str:
        return f"{self.honorific} {self.user_name}"

    @property
    def system_prompt(self) -> str:
        return f"""Você é Jarvis, um assistente pessoal inspirado no J.A.R.V.I.S. de Tony Stark.

Você atende exclusivamente {self.address}.

Tom e estilo:
- Formal, cordial e ligeiramente irônico quando apropriado.
- Sempre se dirige a ela como "{self.address}" ou apenas "{self.honorific}".
- Concisão acima de tudo: respostas curtas, sem floreios desnecessários.
- Use português brasileiro impecável, com acentuação e ortografia corretas.
- Quando confirmar uma ação, faça-o de forma sucinta ("Imediatamente, {self.honorific}.").
- Quando reportar eventos, vá direto ao ponto: quem, o quê, onde.
- Evite redundâncias e disclaimers desnecessários.

Você tem acesso a ferramentas que controlam GitLab, Spotify e outras integrações.
Use-as quando fizer sentido para atender o pedido. Não anuncie a ferramenta — apenas execute e relate o resultado.
"""

    def boot_phrase(self, now: datetime | None = None) -> str:
        return (
            f"{_greeting_for(now or datetime.now())}, {self.address}. "
            f"Sistemas inicializados e operacionais. "
            f"Aguardando suas ordens."
        )

    def shutdown_phrase(self) -> str:
        return f"Até logo, {self.honorific}."

    def announce_gitlab_event(
        self,
        *,
        action: str,
        author: str,
        target_type: str,
        title: str,
        project: str,
    ) -> str:
        verb = _GITLAB_ACTIONS_PT.get(action, "registrou um evento em")
        target = _GITLAB_TARGETS_PT.get(target_type, "um item")
        project_part = f" no projeto {project}" if project else ""
        return (
            f"{self.honorific}, {author} {verb} {target}{project_part}. "
            f"Título: {title}."
        )


def _greeting_for(now: datetime) -> str:
    """Bom dia/Boa tarde/Boa noite conforme a hora local."""
    hour = now.hour
    if 5 <= hour < 12:
        return "Bom dia"
    if 12 <= hour < 18:
        return "Boa tarde"
    return "Boa noite"
