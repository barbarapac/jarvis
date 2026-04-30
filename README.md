# Jarvis

Assistente pessoal local inspirado no J.A.R.V.I.S. do Iron Man, com personalidade
em português e voz clonada. Monitora eventos (GitLab, futuramente Spotify, Jira, etc.),
anuncia em voz alta, e — nas próximas fases — aceita comandos de voz pra disparar
skills do Claude Code.

## Stack

- Python 3.14+ rodando localmente
- TTS plugável: **fish.audio** (voz clonada, pago) ou **Edge TTS** (neural Microsoft, grátis)
- Reprodução de áudio via `playsound3`
- Watchers como módulos plugáveis (atualmente: GitLab `/api/v4/todos`)
- Estado persistido em `.jarvis_state/watchers.json` (eventos já anunciados)

## Estrutura

```
core/
  persona.py     # Personalidade do Jarvis (PT-BR, "Senhora Bárbara")
  narrator.py    # Reprodução de áudio
  tts.py         # Engines plugáveis (fish_audio, edge_tts)
  state.py       # Persistência de IDs já vistos
watchers/
  base.py        # Interface Watcher
  gitlab.py      # Polla todos do GitLab e anuncia
tools/           # Capacidades plugáveis (Spotify, code review, MCPs — em breve)
scripts/
  check_gitlab.py     # Smoke test do token + lista todos pendentes
  test_announce.py    # Roda 1 poll com state fresco pra validar voz
config/
  jarvis.yaml    # User, narrator engine, watchers habilitados, tools
main.py          # Entry point: boot + event loop
```

## Setup

```bash
# 1) Instalar deps
py -m pip install -r requirements.txt

# 2) Configurar credenciais
cp .env.example .env
# Editar .env e preencher:
#   FISH_AUDIO_API_KEY, FISH_AUDIO_VOICE_ID  (se engine = fish_audio)
#   GITLAB_TOKEN  (PAT com scopes read_api, read_user)
#   SPOTIFY_*  (próxima fase)

# 3) Rodar
py main.py
```

## Trocar voz

Em `config/jarvis.yaml`:

- `engine: "fish_audio"` — voz clonada (requer crédito em fish.audio)
- `engine: "edge_tts"` — neural Microsoft grátis (PT-BR `AntonioNeural` por padrão)

## Roadmap

- [x] Fase 1 — Esqueleto + persona + TTS plugável
- [x] Fase 2 — GitLab watcher (anúncios falados)
- [ ] Fase 3 — Push-to-talk + STT + comandos de voz
- [ ] Fase 4 — Tools: Spotify, code review (skill do Claude Code)
- [ ] Fase 5 — MCP registry (plugar MCPs como tools)
