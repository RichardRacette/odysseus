# Durable Dispatcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Steps use checkbox syntax for tracking.

**Goal:** One owner-authorized synthetic Factory mission progresses unattended, recovers after actual process interruption, and produces an independently checked receipt delivered through the permitted RichieOS report route.

**Architecture:** Add an opt-in hub-local coordinator above the existing Factory and QueueStore. Persist explicit execution permits in the same queue database, reuse fixed typed workers and fenced leases, and export immutable private evidence plus sanitized execution receipts. Keep final Factory approval separate from execution authorization.

**Tech Stack:** Python 3.10+, standard library, SQLite, existing authenticated loopback HTTP workers.

**Spec:** Owner message PROJECT DURABLE DISPATCHER in this session, plus existing integrations/homelab/docs/PROJECT-FACTORY.md. Published baseline b2691dc31ed0a3a52ac8d4ebdd6aea79eff61d28; its 53 imported files verified against Git blob IDs. Native newer f3cf355/29ebb2f checkpoints are unavailable here and protected. New additive files must be reconciled with that native source before installation.

## Global Constraints

- Work only in this isolated candidate; leave home services, credentials, data, network settings, existing runtime state, and other worktrees unchanged.
- Reuse Factory and QueueStore; no second scheduler authority, cloud provider, arbitrary shell runner, model download, or third-party dependency.
- Process only explicitly admitted manifest hashes; never scan arbitrary inboxes or authorize from model output.
- Final product/human approval remains pending. Execution authorization is narrower than Factory.approve.
- Retain explicit failed/disarmed states and bounded queue retries; never silently grant retry generations.
- RichieOS canonical files remain Fae-owned. Publish only the permitted reports/codex/20261030-durable-dispatcher.md via a report branch/PR.
- Source publication is an isolated stacked candidate; not integration, installation, or physical host acceptance.

## Review Focus

- Concurrent coordinators, abrupt termination, expired worker claims, and stale results must not corrupt admission or duplicate accepted receipts.
- Restore, changed manifests, removed project registration, and revoked authorization must fail closed across restarts.
- A forged but hash-consistent receipt or wrong trusted manifest must not pass independent semantic verification.
- Export failures and partial receipt writes must recover without overwriting unrelated files or leaking private results into public reports.
- Windows locking and paths must remain portable; actual native acceptance remains separately identified.

### Task 1: Authorized coordinator and semantic receipts

**Files:** Create integrations/homelab/homelab/durable_dispatch.py, homelab/dispatch_receipts.py, tests/test_durable_dispatch.py.

**Interfaces:** Dispatcher(config, receipt_dir); admit(manifest, authorization_ref); pause(mission_id); tick(); run(interval, stop_event, max_seconds=None). verify_receipt(receipt_path, evidence_path, manifest) returns verified execution only after independent fixed-output checks. Existing Factory.submit/advance/inspect/export and QueueStore remain authoritative.

- [x] Write regressions for explicit admission, durable deduplication, restart, revocation, disarmed restore, typed-result verification, hash-consistent receipt forgery, immutable outputs, and single coordinator locking. Run them and observe the missing feature fail.
- [x] Implement the minimal additive coordinator, durable permit store, portable process lock, and crash-safe receipt export. Run focused tests, then the complete companion suite.
- [x] Commit the coherent runtime change and record evidence in the ledger.

### Task 2: Operator CLI and real interruption drill

**Files:** Create integrations/homelab/dispatch.py, scripts/verify-durable-dispatch.py, tests/test_dispatch_cli.py; consume Task 1 interfaces.

- [x] Write failing CLI tests for admission, invalid timing, receipt verification, and process interruption acceptance.
- [x] Implement explicit admit/pause/status/run/verify commands. Implement a new-directory-only drill with real hub HTTP, a gated real worker interrupted after claim, coordinator interruption/restart, normal worker recovery, repeated restart idempotence, independent receipt verification, and disarmed snapshot restore.
- [x] Run the drill as actual child processes and preserve sanitized proof plus private raw evidence separately. Run focused tests and the full companion suite; commit.

### Task 3: Native handoff and durable publication

**Files:** Create integrations/homelab/docs/DURABLE-DISPATCHER.md and docs/DURABLE-DISPATCHER-HANDOFF.md; optional additive public sanitized proof. Create the permitted RichieOS report on an isolated report branch.

- [x] Document exact commands for the disposable proof, explicit admission, supervised native Windows operation without ZBook, rollback, logging, and reconciliation against newer native work.
- [x] Obtain a fresh independent code review. Fix material findings with failing regressions and run final relevant verification.
- [x] Publish only source, safe documentation, and sanitized proof. Preserve immutable source hashes and exact resulting commits. Deliver the verified receipt in the RichieOS report PR without changing canonical state.

## Completion boundary

Pass means verified unattended execution and interruption recovery in this managed Linux environment, with a receipt returned to RichieOS. Home-machine startup, physical Windows/macOS operation, continuous overnight uptime, live queue adoption, and final product approval remain unverified until performed on the actual hosts.

## Delivery checkpoint

Source candidate: https://github.com/RichardRacette/odysseus/pull/11 at 7ec1cc4aaf0dbc086125827406e61d8b8815e1f4 (15 files read back byte-for-byte). RichieOS report: https://github.com/RichardRacette/richieos-control-plane/pull/15 at 9b5267f073a18947bc4ac6fec547e1e97a39d28c (exact report read back; only the permitted report path changed). Full companion verification: 185 tests, 177 passed, 8 skipped; retained final process proof and semantic receipt passed. Runtime implementation is HANDOFF_READY / STOPPED_EDITING. Native deployment remains pending.
