# ADR 0004: Migration to Vertical Slice Architecture, Pragmatic Monorepo, and LiteLLM Gateway

Status: accepted — 2026-09-23

## Context

Over successive development cycles, IntraLink accumulated significant technical debt and architectural sprawl:
1. **Layered Service Accumulation:** The `core-api/app/services` directory expanded to 40+ flat files containing mixed concerns (redundant decision engines, experimental guards, evaluators, duplicate command versions `commands` vs `commands_v2`).
2. **Horizontal Dispersal:** Modifying a single business capability (e.g., ticket triage or RAG search) required editing files across `routers/`, `services/`, `models/`, and `schemas/`, causing cognitive fatigue and high regression risk.
3. **Repository Zoo:** The root directory accumulated uncoordinated utilities, prototypes, and standalone packages (`desktop-companion`, `helpdesk-cli`, `intralink-mcp`, `tools`, `backups`), each maintaining separate dependencies.
4. **Bespoke LLM Plumbing:** AI calls, retries, fallbacks, and token tracking were scattered across custom service wrappers (`ai_suggestions.py`, `ai_synthesis.py`, `response_guard.py`), tightly coupling business logic with specific model providers.
5. **Single Client Reality:** Currently, the system targets a single primary user interface: the Web application (`web`). Maintaining multi-interface scaffolding (CLI, MCP, bots) prematurely creates unnecessary indirection.

## Decision

The project transitions to a **Pragmatic Full-Stack Modular Monorepo** powered by **Vertical Slice Architecture (VSA)** and a centralized **LiteLLM Gateway**:

1. **Repository Layout:**
   - `web/`: Dedicated frontend Single Page Application (Vue/React + Vite + Tailwind), mirrored by feature domain.
   - `api/`: Modern FastAPI service organized strictly into autonomous **Vertical Feature Slices** (`api/src/features/*`).
   - `worker/`: Background automation runner for long-running and Windows-specific execution (WinRM, SMB, printer orchestration, batch synchronization).
   - `core/`: Pure, web-agnostic domain core (IntraService API client, RAG pgvector queries, diagnostic probes, database sessions).
   - `deploy/`: Unified infrastructure orchestration via Docker Compose.

2. **Vertical Slice Architecture (Feature-First):**
   Within `api/src/features/`, each business feature is fully self-contained in its own directory:
   - `features/tickets/`: HTTP routes, Pydantic schemas, and IntraService ticketing orchestration.
   - `features/triage/`: Automated queue triage, prompt assembly, and batch analysis.
   - `features/knowledge_base/`: Semantic search, FastEmbed vectorization, and pgvector cosine distance queries.
   - `features/diagnostics/`: Workstation reachability, SMB:445, WinRM:5985, and Active Directory lookups.
   - `features/reports/`: Support load calculations and report exports.

   *Cross-feature direct imports are prohibited.* Shared utilities and low-level adapters reside exclusively in `core/`.

3. **LiteLLM Proxy as Infrastructure Gateway:**
   - All AI/LLM operations are routed through a dedicated `litellm` Docker container (`ghcr.io/berriai/litellm`) exposed on port 4000.
   - Model aliases, local Ollama endpoints, cloud fallbacks (DeepSeek, OpenAI), Redis response caching, and rate limits are managed declaratively in `deploy/litellm_config.yaml`.
   - The Python backend interacts with LiteLLM purely via the standard `AsyncOpenAI` SDK pointing to `http://litellm:4000/v1`, eliminating custom LLM boilerplate.

4. **Zero-Downtime Pruning (Pre-Production Clean Slate):**
   - The active tree is stripped of legacy debris (`desktop-companion`, `helpdesk-cli`, `tools`, `backups`, dead command iterations).
   - Historical implementations remain accessible via Git history without cluttering the active codebase.

## Consequences

- **High Cohesion & Zero-Fear Refactoring:** Adding, modifying, or deleting a feature affects only its specific slice directory without cascading side effects.
- **Provider Agility:** Switching LLM models or enabling semantic caching requires zero Python code edits.
- **Cognitive Clarity:** Total top-level directories reduced to 5 obvious blocks (`web`, `api`, `worker`, `core`, `deploy`).
- **Seamless Future Extensibility:** When secondary interfaces (MCP server or CLI) are prioritized, they import pure domain functions directly from `core/` or communicate via `api/` without architectural disruption.
