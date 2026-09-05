# Odysseus Fork Handoff

Verified: 2026-09-05

## Purpose

Odysseus is a self-hosted AI workspace used as a practical local-AI systems laboratory: local
models, authenticated remote access, tools, tasks/calendar, document RAG, and deep research. This
repository is a public fork of odysseus-dev/odysseus. The default branch is dev, the upstream
project's newest-change branch.

This file separates repository-verifiable behavior from one known operator deployment. It contains
no credentials, hostnames, private URLs, household data, or private research contents.

## Run it

The simplest supported path is Docker:

    cp .env.example .env
    docker compose up -d --build
    docker compose logs odysseus

Open the configured local application port after containers are healthy. The first administrator
password appears in the container logs; move it into the operator's password manager and do not
copy it into Git or handoff notes.

For native Windows, macOS, GPU, HTTPS, and reverse-proxy setup, follow website/setup.md. Do not
invent a second setup guide here.

Core dependencies include Python/FastAPI/Uvicorn, SQLite by default, ChromaDB plus local
embeddings for semantic memory and RAG, SearXNG for self-hosted search, and the MCP Python SDK on
its maintained v1 line. Docker Compose is the most reproducible full-stack path.

## Models

Odysseus discovers Ollama and other OpenAI-compatible endpoints. A known local deployment has used
Ollama with qwen3.5:4b and gemma3:4b. Those are operator choices, not repository defaults or
guaranteed quality baselines.

For host Ollama with a containerized application, configure the documented OLLAMA_BASE_URL and
make Ollama reachable only as broadly as required. Do not expose Ollama's raw service port to the
public internet. Model downloads can be large; confirm hardware and storage before changing the
installed set.

## Network and authentication assumptions

Repository-verified security defaults:

- authentication is enabled by default;
- LOCALHOST_BYPASS is false by default and must remain false on shared/network deployments;
- bundled service ports bind to loopback unless deliberately changed;
- non-admin users do not receive shell, Python, file, MCP administration, or other high-trust
  privileges by default;
- reverse proxies should reach a loopback-bound app and preserve the request scheme for secure
  session cookies.

A known Windows deployment uses Tailscale for trusted remote access and an authenticated web
surface. Preserve both layers: Tailscale is transport reachability, not a replacement for
application authentication. Do not publish the raw application, Ollama, ChromaDB, notification,
or calendar service ports.

Exact machine addresses, tailnet names, account identities, certificates, passwords, OAuth
credentials, API tokens, and household records belong only in local secret/configuration storage.

## Tool architecture

- Browser/API routes enforce authentication, ownership, and per-user privileges.
- Agent tools are selected and dispatched through the tool index/execution layers.
- Shell and Python are intentional high-trust capabilities and remain admin-only.
- Built-in MCP servers provide image, memory, RAG, and email tools; the browser MCP is optional
  and cache-gated.
- Configured MCP servers are managed by the MCP manager and can use stdio, SSE, or streamable HTTP.
- MCP output is external/untrusted input on model re-entry and high-impact follow-on actions
  require approval.
- Scheduled tasks and calendar tooling have both UI/API and local command surfaces.
- Deep research uses a separate research handler/runtime, provider and fetch layers, persisted
  local reports, and owner-scoped web routes.

Read specs/shell-mcp.md, specs/research.md, specs/calendar-tasks-notes.md, and tests/README.md before
changing those boundaries.

## Known operator deployment state

The following was previously verified on the operator machine but is not fully reproducible from
Git alone:

- authenticated local web access and remote access through Tailscale worked;
- Ollama qwen3.5:4b and gemma3:4b were available;
- task/calendar tooling was exercised;
- outbound research used an operator privacy gate;
- deep-research benchmark runs 1–3 met their local acceptance gates;
- a runtime/version compatibility problem was resolved by pinning MCP 1.29.1 in that environment;
- Windows Task Scheduler supplied startup continuity.

Treat these as deployment observations, not promises about a fresh clone. The current repository
constraint is mcp below version 2 rather than an exact 1.29.1 lock. Before changing MCP versions,
capture the installed working version and rerun focused compatibility tests.

## Stable versus experimental

Stable/repository-backed:

- authenticated self-hosted web workspace;
- Docker and native setup paths;
- local/OpenAI-compatible model discovery;
- tasks, calendar, documents, RAG, MCP, and deep-research subsystems;
- extensive focused test taxonomy and security/ownership coverage.

Experimental or deployment-specific:

- quality and performance of the two named small local models;
- operator-specific outbound research approval policy;
- remote-access and startup automation details;
- local deep-research benchmark corpus and results;
- default-on document RAG behavior on the preserved feature branch.

## Preserved unfinished branch

fix/default-document-rag-on contains one unique commit that makes document RAG default on for a
fresh profile while preserving an explicit opt-out. It changes static/app.js and adds
tests/test_rag_default_storage_js.py.

On 2026-09-05 its four focused tests passed. It remains deliberately unmerged because default-on
retrieval is a product/privacy behavior decision, not cosmetic cleanup. Review the default,
upgrade the branch against current dev, and run the neighboring document/RAG suite before merging.

## Verification

Focused branch test:

    python -m pytest -q tests/test_rag_default_storage_js.py

The handoff environment ran the test with conftest disabled because it installed only pytest, not
the full application dependency set:

    python -m pytest --noconftest -q tests/test_rag_default_storage_js.py

Result: 4 passed. For a real merge, install the documented environment and run the focused test
through the normal test harness plus relevant RAG/storage/security tests. Do not represent this
focused result as a full-suite pass.

## Important unresolved questions

- Is the operator's outbound-research privacy gate implemented in tracked code, local
  configuration, or an external wrapper? Reconstruct it from the machine before replacing it.
- Where are the sanitized benchmark definitions and aggregate results for deep-research runs 1–3?
- Should the deployed environment stay exactly pinned to MCP 1.29.1, or does the current
  maintained-v1 constraint pass the same compatibility tests?
- Should document RAG default on for every fresh profile, or only for this operator's deployment?
- Which startup steps are application-owned versus Windows Task Scheduler-owned?

## Logical next experiments

1. Produce a secret-free deployment manifest containing OS, start command, container/native mode,
   exact package versions, model names, and service topology.
2. Re-run one frozen deep-research benchmark after confirming the outbound privacy gate and record
   only aggregate results.
3. Review the document-RAG default branch as a policy change; run neighboring tests before merge.
4. Compare the two small local models on one bounded family-safe task set using latency, tool-call
   reliability, and answer quality rather than adding more models.
5. Add a documented backup/restore drill for the local data directory.

Do not expand remote exposure or weaken authentication for convenience.
