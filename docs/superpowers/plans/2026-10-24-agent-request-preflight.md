# Offline Assistant Request Preflight Implementation Plan

**Goal:** Convert a bounded assistant request into an existing synthetic Factory mission without executing it.

**Architecture:** A pure validator/compiler and a small operator CLI. Existing Factory remains the only execution authority. No new queue or remote listener.

**Tech stack:** Python 3.10+ standard library and existing homelab Factory validation.

**Spec:** ../specs/2026-10-24-agent-request-preflight-design.md

## Constraints and review focus

Reject requester-controlled authority and execution parameters. Preserve full fixture/template pins. Distinguish attribution, preparation, execution, integrity and human acceptance. Preserve existing modules and branch ownership. Tests must cover duplicate keys, bool-as-int, mutated templates, symlink output and conflicting requests under one ID.

## Task 1 — Pure request compilation

- [x] Write failing cases in integrations/homelab/tests/test_agent_requests.py for valid deterministic preparation and strict malformed-input rejection.
- [x] Implement the loader/compiler in integrations/homelab/homelab/agent_requests.py using the spec contract.
- [x] Verify fixed template and fixture bytes, preserve original fixture revision, bind request digest into the returned mission, and run validate_mission.
- [x] Verify identical admission reuses existing Factory state and changed requests conflict instead of creating a second execution identity.

## Task 2 — Operator preparation CLI

- [x] Add integrations/homelab/agent_request.py with --request and --output, exclusive output creation and bounded generic failure messages.
- [x] Test new-file success, existing-file refusal, symlink refusal, malformed data, and no queue/config requirements.
- [x] Add the synthetic request example and AGENT-REQUESTS.md with commands, trust boundaries and integration limits.

## Task 3 — Verification and handoff

- [x] Run focused tests and the full integrations/homelab unittest suite; report all skips and failures.
- [x] Independently review the additive diff for claims of authority, side effects, source drift, and inconsistent source revisions.
- [x] Preserve exact-base patch/commit and evidence. Leave existing Factory implementation and live services unchanged.

Execution is authorized by the user's request to proceed with the preceding coordination plan. The implementation owner works in a separate branch; native integration remains a later reconciliation step.

Verification: final companion run discovered 178 tests, 170 passed and 8 skipped. Focused preflight suite: 18 passed. Windows newline conversion was exercised by a real temporary Git checkout with core.autocrlf=true on Linux; physical Windows execution is not claimed. Independent review found no blocking adapter issue. Ordinary-file I/O boundary documented.
