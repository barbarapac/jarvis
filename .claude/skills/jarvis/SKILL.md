---
name: jarvis
description: Inicia o assistente Jarvis local da Bárbara (UI no Edge, watchers do GitLab, push-to-talk com Spotify). Use quando ela quiser ligar o Jarvis, verificar se está rodando, ou abrir só a janela.
argument-hint: opcional — "status" pra checar, "stop" pra encerrar, vazio pra ligar
---

# Jarvis — Controle

Você está orquestrando o assistente pessoal **Jarvis** da Bárbara, que vive em
`C:\Users\barbara.pacheco\Documents\jarvis`. O processo dele é um `py main.py`
de longa duração que sobe servidor UI, watchers e push-to-talk.

## Ações por argumento

### Sem argumento (ou "start", "ligar", "iniciar"): ligar o Jarvis

1. **Verifique se já está rodando**: tente `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8765/` (timeout 2s).
   - Se retornar 200, avise "Jarvis já está rodando" e vá para o passo 4 (abrir UI).
   - Caso contrário, prossiga.

2. **Suba o processo em background**:
   ```bash
   cd /c/Users/barbara.pacheco/Documents/jarvis
   py -u main.py
   ```
   Use `run_in_background: true` na chamada do Bash. Isso retorna um shell ID.

3. **Aguarde o servidor subir**: monitore o output do background até aparecer
   `UI server pronto em http://127.0.0.1:8765` (até ~10s). Use `BashOutput` no
   shell ID. Se aparecer `AVISO: UI server não subiu`, reporte o erro e pare.

4. **Abra a UI no Edge --app**:
   ```bash
   "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --app=http://127.0.0.1:8765 --window-size=460,720 --no-first-run &
   ```

5. **Reporte**: "Jarvis online. UI aberta. {capacidades listadas no boot}."

### "status": verificar se está rodando

1. `curl -s http://127.0.0.1:8765/` — 200 = online, erro = offline.
2. Reporte de forma sucinta.

### "stop", "encerrar", "desligar": parar o processo

1. Liste shells em background ativos com nome contendo "main.py" ou "jarvis".
2. Use `KillShell` no shell ID correspondente.
3. Confirme: "Jarvis encerrado."

### "ui", "abrir": só abrir a janela (assumindo que já está rodando)

1. Verifique status (curl como acima).
2. Se online, abra Edge --app como no passo 4.
3. Se offline, sugira ligar primeiro.

## Notas importantes

- **NUNCA** rode `py main.py` em foreground sem `run_in_background: true` — vai
  travar a sessão (é loop infinito).
- O processo precisa rodar enquanto a Bárbara estiver usando — não encerre
  voluntariamente a menos que ela peça.
- Se o servidor não subir, leia o output do background pra reportar a causa
  (geralmente: token Spotify expirado, fish.audio sem crédito, modelo Vosk
  ausente).
- Se a Bárbara estiver fora do diretório do jarvis, NÃO peça pra ela mudar de
  pasta — apenas use o caminho absoluto no `cd` do passo 2.
