# Home Lab implementation plan

> Agentic workers: use scoped implementation tasks and independently review integrated behavior.

**Goal:** Deliver an opt-in, tested lab companion and concrete native rollout.

**Architecture:** A durable Python/SQLite hub dispatches fixed jobs to authenticated pull workers. A separate project-evidence store provides attributable retrieval. CLI, private HTTP and a read-only MCP bridge share the same policy boundaries.

**Tech stack:** Python 3.10+ standard library, SQLite FTS5, optional existing MCP v1 SDK, optional Ollama HTTP APIs.

**Spec:** [DESIGN.md](DESIGN.md).

## Global constraints

- Keep existing Odysseus source, local configuration, memory, RAG and installed models unchanged.
- No new paid services, public listener, messages, cloud execution or automatic model downloads.
- Use generic role names and synthetic data in Git; no private node addresses or household files.
- The runtime database belongs on local storage; replicas are immutable snapshots.

## Review focus

- Lease expiration while work is still running: stale completion must fail.
- Reused idempotency key with different content: reject rather than return unrelated work.
- A source becomes unreadable or disappears: never retain an apparently current source silently.
- A malicious path, redirect or local model name: cannot broaden file/network execution authority.
- A partially written backup or stopped coordinator: visible failure and a safe restart path.

## Tasks

- [x] Queue/worker: test persistence, competing claims, capacities, leases, retries, input limits and policy first; implement fixed local tasks.
- [x] Knowledge: test project isolation, secret/symlink exclusions, updates/deletions, provenance and embedding failure first; implement indexed evidence.
- [x] Hub and CLI: test role authentication, payload limits and real HTTP round trips first; implement configuration, status, explicit submission and read-only MCP.
- [x] Operations: test snapshot integrity/restore and safe Git paths first; implement diagnostics and native start scripts.
- [x] Integration: run the core and optional-MCP suite and synthetic demonstration; independently review failures and permission boundaries. See VERIFICATION.md.
- [x] Handoff: write operator runbook and distinguish local tests from physical deployment checks.
- [ ] Repository delivery: preserve sanitized code on an isolated Git branch and open its draft PR; actual repository state is the receipt.
- [ ] Physical rollout: complete the private HOST-ACCEPTANCE.md record on the actual lab machines.
