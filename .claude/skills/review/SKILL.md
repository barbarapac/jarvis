---
name: hp:review
description: Robust code review for Compass features using 4 specialized agents (test quality, conformance, correctness, design). Classifies findings into 3 tiers, applies approved fixes with verification loop, and generates review artifact.
argument-hint: Jira key or scope (e.g., "COMPASS-456", "features/mp/", "branch:feat/COMPASS-456")
metadata:
  model: opus
---

## Use this skill when

- User wants a code review after development
- User says "review", "revisar", "code review", "hp:review"
- User wants to check code quality before pushing
- User wants to review a specific set of files or a branch diff

## Do not use this skill when

- Full feature development end-to-end (use `/hp:dev`)
- Only exploring code (use `hp-explorer` agent directly)
- Bug fix from QA findings (QA fixes inline now)
- Spec has not been created yet (use `/hp:spec` first)

---

# HP Code Review

You are orchestrating a comprehensive, multi-perspective code review for the **the current project** platform. Follow a systematic 5-phase approach using 4 specialized agents.

This skill is responsible for **finding and fixing quality issues in code produced by `/hp:dev`**. It does NOT push, create MRs, or post to Jira — those are responsibilities of downstream workflows.

## 🪄 Hogwarts Console — Aula de Defesa Contra as Artes das Trevas

Esta skill opera na **Aula de DCAT** — 4 bruxos examinam o codigo de todas as perspectivas.

**Ao iniciar a skill**, exiba:
```
⚔️ Aula de Defesa Contra as Artes das Trevas — 4 bruxos prontos para examinar o codigo!
```

**Ao invocar cada agente**, exiba a mensagem tematica ANTES do lancamento:

| Agente | Invocacao |
|--------|-----------|
| `hp-test-reviewer` | `👁️ Mad-Eye Moody ergue a varinha... "HOMENUM REVELIO!" — VIGILANCIA CONSTANTE!` |
| `hp-reviewer` | `🧠 Snape ergue a varinha... "LEGILIMENS!" — Penetrando as convencoes...` |
| `hp-bug-hunter` | `🗡️ Neville ergue a varinha... "DIFFINDO!" — Cacando bugs que sobrevivem em producao...` |
| `hp-code-quality` | `💎 Bill Weasley ergue a varinha... "FINITE INCANTATEM!" — Quebrando maldicoes de design...` |

**Ao receber resultado do agente**, exiba:
```
✅ {Personagem} guarda a varinha. {N} findings encontrados.
```

**Lancamento (4 agentes em paralelo)**:
```
⚡ Revisao Completa — 4 bruxos convocados!

  🪄 Moody: "HOMENUM REVELIO!" — Examinando qualidade dos testes...
  🪄 Snape: "LEGILIMENS!" — Convencoes e regras do projeto...
  🪄 Neville: "DIFFINDO!" — Bugs, seguranca e edge cases...
  🪄 Bill Weasley: "FINITE INCANTATEM!" — Qualidade de codigo e design...
```

**Ao concluir a skill**:
```
⚔️ Aula de DCAT encerrada. O codigo foi examinado por 4 bruxos!
```

---

## Core Principles

- **4 perspectives, no blind spots**: Every file is analyzed from 4 different angles covering test quality, conformance, correctness, and design quality.
- **Rules are law**: Every finding must cite evidence — a rule from `the loaded project rules`, a .NET best practice, or a design principle.
- **Confidence-based filtering**: Only report issues with confidence >= 75. Classify into 3 tiers.
- **No false positives**: Better to miss a minor issue than report a false alarm.
- **Autonomous fixes**: Agents fix findings without asking for confirmation. Every fix is verified by build+test.
- **Local only**: This skill does NOT push, create MRs, or post to Jira. Output is local commits + artifacts.

---

## Finding Tiers

All findings from all agents are classified into 3 tiers:

### CORRIGIR (confidence >= 90)
Mechanical, unambiguous fixes. No judgment needed — the rule is clear and the fix is obvious.

Examples:
- `DateTime.Now` → `IClock.Now`
- `Guid.NewGuid()` → `GuidGenerator.Create()`
- Missing `ConfigureByConvention()` in entity config
- Unused import
- `AutoMapper` → `Mapperly`
- Empty catch block
- Missing `sealed` on entity class

### AVALIAR (confidence 75-89)
Findings that require human judgment. The agent is fairly confident but the fix has trade-offs.

Examples:
- "Metodo com 25 linhas, sugiro extrair" — talvez faca sentido manter junto
- "Possivel N+1 query" — talvez o volume nao justifique otimizar
- "Handler pode estar violando SRP" — talvez seja pragmatico manter
- "Mock nao reflete dependencia real" — talvez seja aceitavel para o cenario
- "Logica duplicada entre handlers" — talvez a duplicacao seja intencional

### INFORMAR (observacoes)
Observations that don't require immediate action. Documented for awareness.

Examples:
- "Aggregate crescendo, considerar extrair sub-aggregate no futuro"
- "Pattern X poderia simplificar, mas nao e urgente"
- "Cobertura de testes para edge case Y seria desejavel"

---

## Phase 0: Scope Detection

**Goal**: Determine what code to review and collect the file list.

**Input**: $ARGUMENTS

**Actions**:

1. **Record start timestamp**:
   ```bash
   review_start_ts=$(date -u +%Y-%m-%dT%H:%M:%S%z)
   ```

2. **Detect Jira key** from `$ARGUMENTS` (pattern `[A-Z]+-\d+`). If found, store for artifact naming.

3. **Detect review scope** from `$ARGUMENTS`:

   - **Explicit files/directory**: e.g., `features/mp/` → glob for all `.cs` files
   - **Branch diff**: e.g., `branch:feat/COMPASS-456` → diff against default branch
   - **No argument**: default to uncommitted + committed changes on current branch vs default branch

4. **Collect file list**:

   For files/directory:
   ```bash
   find {path} -name "*.cs" -not -path "*/bin/*" -not -path "*/obj/*"
   ```

   For branch diff:
   ```bash
   default_branch=$(git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@')
   git diff ${default_branch}...HEAD --name-only --diff-filter=ACMR -- '*.cs'
   ```

   For uncommitted changes:
   ```bash
   git diff --name-only --diff-filter=ACMR -- '*.cs'
   git diff --cached --name-only --diff-filter=ACMR -- '*.cs'
   ```

5. **Read dev summary** — if `.hp-dev-summary.md` exists, read it to understand what was built and which files to focus on.

6. **Extract Design Decisions** — if `.hp-dev-summary.md` contains a `## Design Decisions` section, extract ALL design decisions into a list. These are explicit deviations from project conventions that were approved in the architecture blueprint. This list is used in Phase 1 (agent prompts) and Phase 2 (cross-reference).

7. **Detect module** from file paths (e.g., `features/mp/` → module `mp`)

7. **Classify files** into categories:
   - Domain: entities, value objects, managers, repository interfaces
   - Application: handlers, commands, queries, validators, DTOs, mappers
   - Infrastructure: EF configs, repository implementations, DbContext
   - HttpApi: controllers, consumers
   - Tests: test classes, fixtures, fakers, mocks

8. **If no files found** → STOP:
   ```
   Nenhum arquivo encontrado para revisao no escopo informado.
   ```

9. **Present scope** to user:
   ```
   ## Escopo da Revisao
   - {N} arquivos para revisar
   - Modulo: {module}
   - Camadas: {Domain, Application, Infrastructure, HttpApi, Tests}
   ```

10. Create todo list with all 5 phases (0–4)

---

## Phase 1: Multi-Agent Analysis (4 Agents in Parallel)

**Goal**: Analyze the code from 4 different perspectives. Each agent has a unique, non-overlapping focus.

> **MANDATORY**: Todos os 4 agentes DEVEM ser lancados. Cada um cobre um aspecto diferente e insubstituivel da qualidade.

Launch all 4 agents in parallel:

**Agent 1 — Test Quality** (`hp-test-reviewer`):
> "Revise a qualidade de todos os testes unitarios neste escopo.
>
> **Arquivos de implementacao**: [{list of implementation files with paths}]
> **Arquivos de teste**: [{list of test files with paths}]
>
> **Design Decisions (do blueprint aprovado)**: [{lista de design decisions extraidas do dev-summary, ou "Nenhuma" se vazio}]
> Findings que contradigam uma Design Decision explicita devem ser rebaixados para INFORMAR com nota: "conflita com design decision #{N}: {decisao}".
>
> Avalie: Os testes realmente validam comportamento? As assertions sao significativas ou superficiais? Edge cases estao cobertos? Esses testes pegariam bugs reais? Os mocks sao realistas? Ha cenarios faltando? Reporte apenas findings com confianca >= 75."

**Skip condition**: No test files in scope → skip this agent.

**Agent 2 — Conformance** (`hp-reviewer`):
> "Revise todos os arquivos para conformidade com as convencoes do projeto. Arquivos: [{list}].
>
> **Design Decisions (do blueprint aprovado)**: [{lista de design decisions extraidas do dev-summary, ou "Nenhuma" se vazio}]
> Findings que contradigam uma Design Decision explicita devem ser rebaixados para INFORMAR com nota: "conflita com design decision #{N}: {decisao}".
>
> Verifique: coding-style, mediator-cqrs, entity-mapping, logging, i18n, bibliotecas proibidas, fluxo do Result pattern, consistencia de paginacao, convencoes de entidades, repository pattern, Unit of Work, convencoes de autorizacao, multi-tenancy, convencoes de cache, convencoes temporais. Foco exclusivo em CONVENCOES — bugs e corretude sao responsabilidade de outro agente. Reporte apenas issues com confianca >= 75."

**Skip condition**: Only test files in scope → skip this agent.

**Agent 3 — Correctness and Safety** (`hp-bug-hunter`):
> "Cace bugs, falhas de seguranca e edge cases em todos os arquivos deste escopo. Arquivos: [{list}].
>
> **Design Decisions (do blueprint aprovado)**: [{lista de design decisions extraidas do dev-summary, ou "Nenhuma" se vazio}]
> Findings que contradigam uma Design Decision explicita devem ser rebaixados para INFORMAR com nota: "conflita com design decision #{N}: {decisao}".
>
> Cace: bugs logicos, null handling incorreto, race conditions, error handling faltando ou incorreto, edge cases que podem quebrar em producao, padroes async/await perigosos, performance traps (N+1, cartesian explosion, queries sem limite), falhas de seguranca (injection, data exposure, authorization bypass), resource leaks, problemas de integridade de dados. Foco exclusivo em CORRETUDE — convencoes sao responsabilidade de outro agente. Reporte apenas issues com confianca >= 75."

**Skip condition**: Only test files in scope → skip this agent.

**Agent 4 — Code Quality** (`hp-code-quality`):
> "Revise todos os arquivos para qualidade de codigo e design. Arquivos: [{list}].
>
> **Design Decisions (do blueprint aprovado)**: [{lista de design decisions extraidas do dev-summary, ou "Nenhuma" se vazio}]
> Findings que contradigam uma Design Decision explicita devem ser rebaixados para INFORMAR com nota: "conflita com design decision #{N}: {decisao}".
>
> Verifique: complexidade cognitiva acidental (nesting >2, metodos >20 linhas, nomes pouco claros, fluxo de controle obscuro), codigo morto (imports nao utilizados, metodos privados nao utilizados, parametros nao utilizados, codigo comentado, placeholders TODO), duplicacao, principios SOLID (SRP, OCP, LSP, ISP, DIP), violacoes DRY, code smells (Feature Envy, Data Clumps, Primitive Obsession, God Method/Class), violacoes KISS/YAGNI, qualidade do domain design (modelo anemico, metodos de dominio faltando). Reporte findings com sugestoes antes/depois e confianca >= 75."

**Skip condition**: Only test files in scope → skip this agent.

### After all agents complete:

Collect all findings from all 4 agents. Update context.

---

## Phase 2: Consolidate & Classify

**Goal**: Deduplicate findings, classify into 3 tiers, and generate the review findings artifact.

**Actions**:

1. **Deduplicate**: Same issue reported by different agents → keep highest confidence version.

2. **Classify into tiers**:
   - **CORRIGIR** (confidence >= 90): mechanical, unambiguous
   - **AVALIAR** (confidence 75-89): requires judgment
   - **INFORMAR** (observations, architectural notes)

3. **Cross-reference Design Decisions**: Para cada finding classificado como CORRIGIR ou AVALIAR, verificar se contradiz alguma Design Decision do dev-summary. Se sim:
   - Rebaixar para INFORMAR
   - Adicionar nota: "Conflita com Design Decision #{N}: {decisao} — {justificativa do blueprint}"
   - Este step é o filtro final do orquestrador — mesmo que um agente não tenha respeitado a instrução de rebaixamento, o orquestrador corrige aqui.

4. **Generate review summary artifact** — write `.hp-review-summary.md` in the repository root:

   ```markdown
   # Review Summary: {JIRA-KEY or module}

   ## Metadata
   - timestamp: {timestamp}
   - module: {module}
   - escopo: {N} arquivos
   - agentes: 4/4 executados
   - veredito: PENDENTE
   - start: {review_start_ts}

   ## Findings por Tier

   ### CORRIGIR ({N} itens — confidence >= 90)

   | # | Categoria | Arquivo | Linha | Descricao | Agente | Status | Correcao Aplicada |
   |---|-----------|---------|-------|-----------|--------|--------|-------------------|
   | 1 | Rules | path.cs | L42 | DateTime.Now → IClock.Now | reviewer | PENDENTE | — |

   ### AVALIAR ({N} itens — confidence 75-89)

   | # | Categoria | Arquivo | Linha | Descricao | Sugestao | Agente | Status | Justificativa |
   |---|-----------|---------|-------|-----------|----------|--------|--------|---------------|
   | 1 | Design | handler.cs | L30 | Metodo com 25 linhas | Extrair metodo | refactorer | PENDENTE | — |

   ### INFORMAR ({N} itens)

   | # | Categoria | Arquivo | Observacao | Agente |
   |---|-----------|---------|------------|--------|
   | 1 | Design | aggregate.cs | Considerar extrair sub-aggregate | code-quality |

   ## Findings por Categoria

   | Categoria | Total | Criticos | Importantes |
   |-----------|-------|----------|-------------|
   | Rules | {N} | {N} | {N} |
   | Bugs | {N} | {N} | {N} |
   | .NET | {N} | {N} | {N} |
   | ABP | {N} | {N} | {N} |
   | Design | {N} | {N} | {N} |
   | Testes | {N} | {N} | {N} |
   | Complexidade | {N} | {N} | {N} |
   | Dead Code | {N} | {N} | {N} |

   ## Resumo Numerico

   | Metrica | Valor |
   |---------|-------|
   | Total findings | {N} |
   | CORRIGIR | {N} |
   | AVALIAR | {N} |
   | INFORMAR | {N} |
   ```

   > **IMPORTANT**: This artifact is consumed by `/hp:publish` for the consolidated Jira report. The "Findings por Categoria" table and the "veredito" field are required for the report.

4. **Present findings to user** organized by tier:

   ```
   ## Resultado da Analise

   **Escopo**: {N} arquivos | Modulo: {module}

   ### CORRIGIR ({N} itens — automaticos)
   | # | Categoria | Arquivo:Linha | Descricao |
   ...

   ### AVALIAR ({N} itens — requerem decisao)
   | # | Categoria | Arquivo:Linha | Descricao | Sugestao |
   ...

   ### INFORMAR ({N} itens — observacoes)
   | # | Observacao |
   ...

   Artefato completo: `.hp-review-summary.md`
   ```

5. Update context.

---

## Phase 3: Fix Loop

**Goal**: Apply all fixes autonomously with verification. Iterate until clean.

> **REGRA**: Os agentes tem autonomia total para corrigir findings. Nenhum fix e considerado completo sem build+test verde.

### Step 3.1: CORRIGIR Round

1. **Apply all CORRIGIR fixes automatically** (no confirmation needed):
   - Apply each fix
   - Run build + test:
     ```bash
     dotnet build features/{module}/ && dotnet test features/{module}/test/
     ```
   - **If green** → update finding status to `CORRIGIDO` in artifact, record what was changed in "Correcao Aplicada" column
   - **If red** → identify which fix broke, revert it, try an alternative approach. If still fails → **RECLASSIFICAR para AVALIAR** com justificativa "Correção automática falhou após N tentativas" e processar no Step 3.2 (AVALIAR round). **PROIBIDO marcar CORRIGIR como ACEITO.** Se não pode ser corrigido automaticamente, deve ser reclassificado para AVALIAR para decisão explícita
   - **Commit** via the commit convention from loaded rules

### Step 3.2: AVALIAR Round

1. **For each AVALIAR finding**, the agent autonomously decides: **corrigir** or **aceitar**.

   **Decision criteria — CORRIGIR if**:
   - The fix is straightforward and low-risk
   - The suggestion improves readability, correctness, or maintainability
   - The change is localized (doesn't cascade across multiple files)

   **Decision criteria — ACEITAR if**:
   - The fix would introduce more complexity than it removes
   - The current code is intentional (e.g., duplication for isolation between handlers)
   - The performance impact is negligible for the expected data volume
   - The suggestion conflicts with an existing architectural decision

2. For each finding decided as **corrigir**:
   - Apply the fix
   - Run build + test
   - **If green** → mark `CORRIGIDO`, record what was changed in "Correcao Aplicada" column
   - **If red** → revert, mark `ACEITO` with justification "Correcao causou regressao — mantido como esta"

3. For each finding decided as **aceitar**:
   - Mark `ACEITO` and record a **justificativa tecnica** explaining WHY in the artifact
   - Examples: "Metodo tem 25 linhas mas cada linha e uma etapa logica distinta — extrair fragmentaria a leitura", "Duplicacao intencional para isolamento entre handlers"

4. **Commit all fixes** via the commit convention from loaded rules

### Step 3.3: Re-verification (if fixes were applied)

After applying any fixes, run a lightweight re-check on the changed files only:

1. Collect list of files modified by fixes
2. Launch 2 agents in parallel on ONLY the modified files:

   **Agent A — Rule Conformance** (`hp-reviewer`):
   > "Verifique estes arquivos corrigidos para conformidade com as convencoes do projeto. Arquivos: [{modified files only}]. Reporte apenas issues NOVAS com confianca >= 90."

   **Agent B — Correctness** (`hp-bug-hunter`):
   > "Verifique estes arquivos corrigidos para bugs ou regressoes. Arquivos: [{modified files only}]. Reporte apenas issues NOVAS com confianca >= 90."

3. **If new issues found** → present to user, apply if approved, re-test
4. **If clean** → proceed

### Step 3.4: Final Gate

```bash
dotnet build features/{module}/ && dotnet test features/{module}/test/
```

**Gate**: ALL tests MUST be green. Non-negotiable.

---

## Phase 4: Report & Metrics

**Goal**: Update artifacts with final status and record metrics. No push, no MR, no Jira.

**Actions**:

1. **Update `.hp-review-summary.md`** with final status:
   - Update every finding's Status column (CORRIGIDO / ACEITO / PENDENTE / FALHOU)
   - **Validate CORRIGIR integrity**: any CORRIGIR finding with status != CORRIGIDO is an error. CORRIGIR items can only be CORRIGIDO (fixed) or reclassified to AVALIAR (if fix failed). They can NEVER be ACEITO or PENDENTE.
   - Update `veredito` in Metadata:
     - `APROVADO`: 0 CORRIGIR with status != CORRIGIDO AND 0 AVALIAR with status == PENDENTE
     - `ALTERACOES_PENDENTES`: any CORRIGIR with status != CORRIGIDO, OR any AVALIAR with status == PENDENTE
   - Append final results section:
     ```markdown
     ## Resultado Final

     | Metrica | Valor |
     |---------|-------|
     | Veredito | {APROVADO ou ALTERACOES_PENDENTES} |
     | CORRIGIR | {N} corrigidos / {N} total |
     | AVALIAR | {N} corrigidos, {N} aceitos, {N} pendentes / {N} total |
     | INFORMAR | {N} documentados |
     | Build | verde |
     | Testes unitarios | {N} passando |
     ```

2. **Record end timestamp** and update `.hp-review-summary.md` Metadata section:
   ```bash
   review_end_ts=$(date -u +%Y-%m-%dT%H:%M:%S%z)
   ```
   Append to Metadata:
   ```markdown
   - end: {review_end_ts}
   - duration_min: {calculated minutes}
   - outcome: {concluido}
   - findings_total: {N}
   - findings_corrigir: {N} ({N} corrigidos)
   - findings_avaliar: {N} ({N} corrigidos, {N} aceitos)
   - findings_informar: {N}
   ```

3. **Present summary to user**:

   ```
   ## Revisao Concluida

   **Modulo**: {module}
   **Branch**: `{branch_name}`

   ### Findings
   - CORRIGIR: {N}/{N} aplicados
   - AVALIAR: {N} corrigidos, {N} aceitos pelo agente
   - INFORMAR: {N} documentados

   ### Verificacao
   - Build: verde
   - Testes: {N} passando
   - Re-check pos-fix: limpo

   ### Artefatos
   - `.hp-review-summary.md` — findings com status final (inclui timestamps e duração)

   ### Proximo passo
   - QA automatizado: `/hp:qa {JIRA_KEY}`
   ```

4. **Artifacts gerados** (NOT committed, consumed by downstream skills):
   - `.hp-review-summary.md` — findings com status final (inclui timestamps e duração)

---

## Phase Flow Summary

```
Phase 0: Scope Detection        → Detecta arquivos, modulo, le dev-summary, registra inicio
Phase 1: 4-Agent Analysis       → test quality, conformance, correctness, code quality (all in parallel)
Phase 2: Consolidate & Classify → Deduplica, classifica 3 tiers, gera artifact de findings
Phase 3: Fix Loop               → CORRIGIR → AVALIAR → build+test → re-check → commit
Phase 4: Report & Metrics       → Atualiza artifact, registra metricas, resumo ao usuario
```

**Gate rules**:
- Phase 0: files found → proceed
- Phase 1: ALL 4 agents launched (skip individual agents only if no relevant files)
- Phase 2: findings artifact generated
- Phase 3: build + ALL tests green after fixes
- Phase 4: artifacts updated, metrics recorded

---

## Error Handling

| Error | Action |
|-------|--------|
| No files in scope | STOP — inform user |
| Agent returns no findings | Report "nenhum issue encontrado" for that category |
| Build fails after fix | Revert the specific fix, mark FALHOU, continue with next |
| Tests fail after fix | Revert the specific fix, mark FALHOU, continue with next |
| All fixes reverted | Report to user, all remain PENDENTE |
| Dev summary not found | Proceed without it — detect scope from git diff |
| Metrics file not found | Create new one with review section only |
