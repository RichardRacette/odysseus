# Home Lab foundation design

Date: 2026-10-22. Status: implementation candidate; deployment must be verified on each host.

## Goal

Coordinate an intermittently available three-computer lab, preserve project evidence, and make recovery testable without replacing existing Odysseus or OpenClaw installations. The companion is opt-in, runs with Python 3.10+ and SQLite, and requires no new hardware, Docker, paid API, or model download for its synthetic demonstration.

## Existing foundation

Odysseus already provides personal memory, Chroma-backed document retrieval, scheduled local tasks, model discovery, and MCP. Those capabilities and their data remain authoritative. This companion adds a distributed durable queue and a separate project-evidence index with exact file provenance. It does not change existing memory defaults, the unmerged document-RAG branch, authentication, provider selection, or background tasks.

## Components

1. **Hub:** one local SQLite queue and one project knowledge database, loopback HTTP service on 8765, separate administrator/reader/node credentials, private HTTPS or SSH transport between machines. One authority: no shared database file and no automatic leader failover.
2. **Workers:** pull work using node-specific credentials. Tasks are fixed implementations (`text.hash`, `json.validate`, `ollama.generate`). No shell strings, arbitrary subprocesses, automatic downloads or cloud execution. Worker jobs have expiring fenced leases, bounded attempts, heartbeats, and one concurrent task per node by default.
3. **Knowledge:** approved project roots only; dry-run before ingest; UTF-8 text, Markdown, JSON and source text; file SHA256 and exact line spans; project-specific lexical search plus optional local Ollama embeddings; explicit source/note/inference/decision kinds. Existing Odysseus memory and RAG remain separate. Scanned PDFs and images require a later extraction adapter and are not silently treated as indexed.
4. **Operations:** self-diagnostic commands, synthetic end-to-end demonstration, consistent SQLite snapshots, hash manifests, restore to a fresh directory, optional read-only Git mirror, native Windows/macOS start recipes, and a documented reboot/restore drill.
5. **Interfaces:** CLI owns mutations and approved-root configuration. An optional maintained-v1 MCP adapter provides read-only status, job results and project search to Odysseus/OpenClaw. It requires a fixed project and cannot ingest directories or execute commands.

## Routing and privacy

The configured `hub` handles lightweight validation, `gpu` handles allowed local Ollama generation, and `mac` is an optional lightweight worker. Nodes are roles rather than assumed network addresses. A disconnected eligible worker leaves its jobs pending; no silent cloud fallback occurs. Cloud escalation is an explicit future boundary, not an implemented billing feature. Local-only model requests use an exact allowlist and a loopback Ollama endpoint; setup verifies Ollama cloud mode and model inventory separately.

Only administrator submission can create jobs. Worker tokens are bound to their configured node and cannot view other jobs, knowledge, configuration, or administrative endpoints. Reader tools access only the configured project; this is a trusted personal lab, not a multi-user hosted service. Secrets live outside Git, startup does not print them, requests have size limits, and service errors do not echo credentials or private file contents.

## Acceptance

- Synthetic queue jobs travel through real HTTP and actual worker code and survive hub restart.
- Competing claims cannot allocate one job twice; stale leases cannot commit results.
- Retries stop at the configured limit; idempotency keys reject different payloads.
- Worker identity, capabilities, exact model allowlists, local-only and capacity rules are enforced in code.
- Knowledge never crosses project roots; symlinks, hidden paths, credentials and excessive files are excluded; citations include content hash and source lines.
- Updates and removals update retrieval, and missing embedding services yield an explicit lexical fallback.
- Snapshot restoration passes SQLite integrity checks and preserves known jobs and retrieval.
- Core tests run with no model downloads and no third-party Python dependencies.
- Physical Windows/macOS startup, real-model quality, Tailscale reachability and reboot behavior remain unverified until receipts are collected on those hosts.
