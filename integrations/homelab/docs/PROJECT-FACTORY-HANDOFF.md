# Project Factory v0.1 — durable engineering handoff

Date: 2026-10-23 (America/Detroit). Status: **code committed, review and runtime acceptance pending**.

## Source of truth

- Repository: https://github.com/RichardRacette/odysseus
- Draft factory PR: https://github.com/RichardRacette/odysseus/pull/9
- Feature branch: `codex/project-factory-v01`
- Feature base: `codex/homelab-portability`, draft PR #5.
- Baseline parent commit inspected: `5581cdd624b9df49efcd4683e60c29d2da80830f`.
- Foundation PR #4 targets `dev`. Neither it nor PR #5 is integrated on `dev`.
- Draft PR #8 concerns **non-production** Pillow 13 candidate testing; PR #7's
  opt-in metadata-only builder is already merged into the PR #5 branch.
- Do not fast-forward/merge/deploy anything until the relevant exact-head
  CI results, security gate, code review and separate host acceptance pass.

## Implementation inventory

| Artifact | Purpose |
| --- | --- |
| `homelab/factory.py` | Immutable missions, DAG planning, queue admission, crash-gap repair, bounded retry and evidence/approval journal |
| `factory.py` | Operator CLI and real HTTP + current worker demo |
| `examples/project-factory-release-audit.json` | Two-step pinned `validate.json` mission, with its source commit SHA and SHA-256 |
| `tests/test_factory.py` | 20 new unit/integration/fault and security cases |
| `scripts/verify-project-factory.sh` | One-command Linux/macOS acceptance |
| `scripts/verify-project-factory.ps1` | One-command Windows acceptance |
| `docs/PROJECT-FACTORY.md` | Architecture, operation, permission boundaries, recovery and host checklist |
| `README.md`, `docs/RUNBOOK.md` | Discovery and operator navigation |
| `.github/workflows/homelab.yml` | Existing unittest matrix also compiles the new entrypoint, runs the ephemeral end-to-end CLI demonstration and verifies its evidence packet on each platform |

Everything is in `integrations/homelab/` except the workflow. No dependency
or production application requirements changed. No new worker permission, shell
command runner, server listener, model download or cloud integration was added.

## Acceptance matrix at handoff

| Gate | Status | Evidence / next action |
| --- | --- | --- |
| Live repository and PR inspection | **Verified** | #4/#5/#7/#8 inspected via authenticated GitHub connector on Oct 23 |
| Feature source committed and draft PR created | **Verified** | GitHub draft PR #9, 11 changed paths, source history |
| Source consistency and guard presence | **Verified (static)** | 11/11 source-level checks including pinned fixture text, no obvious general shell executor, safeguards and unchanged dependency workflow |
| Python unit/integration/fault suite | **Unverified** | 20 tests authored; no new-head hosted CI or local Python execution observed in this session |
| End-to-end CLI demo | **Unverified** | Runnable test and existing-HTTP/worker demo code published; needs host run |
| Cross-platform hosted companion matrix on feature head | **Unverified** | No Actions workflow run available on the new factory commit when checked |
| Existing parent companion & app CI | **Historically verified** | Parent `5581cdd…` passed the hosted matrix and application CI on Oct 22, not the factory branch |
| Dependency review required gate | **Failing historically / current result not reverified** | Last inspected run 39870163475 failed: Dependency Graph disabled and 35 Pillow 12.3.0 findings |
| Dependency candidate production approval | **Blocked** | #8 is isolated testing; no default dependency fix approved |
| Three real machines: RTX / Latitude / Mac | **Unverified** | No fresh physical host receipt; previous Oct 22 recovery results remain historical |
| Encrypted backup target restoration & recipient checksum | **Unverified** | Existing backup must not be altered or assumed recipient-verified |
| Production deployment and protected merges | **Not performed** | Requires explicit owner approval and passing gates |

## Smallest operator action: one local acceptance run

**Do not overwrite existing working state or use `lab.py init` on production.**
Fetch the PR #9 branch into a fresh, disposable checkout/worktree. From
`integrations/homelab` on the Mac or another development machine, execute:

```sh
bash scripts/verify-project-factory.sh "$HOME/odysseus-factory-acceptance-001"
```

On Windows PowerShell:

```powershell
.\scripts\verify-project-factory.ps1 -Destination "$env:LOCALAPPDATA\OdysseusFactory-acceptance-001"
```

Use a **new**, otherwise nonexistent destination and review output carefully.
The script executes the full existing Home Lab unittest suite, compiles
entrypoints, executes the two-step example via real local HTTP/workers, closes
and reopens persisted state, exports and verifies hashed evidence, records a
*demo-only* approval and verifies disarmed SQLite restore. The generated
receipt has `physical_nodes_verified=false`, and is not a claim of a
three-node acceptance run. Keep it out of source control and protect private
receipts/test credentials.

If the run fails, retain terminal output and the new private directory; do
not merge, deploy or run against the working home-lab state. Fix only the
specific reproducible failure with new regression evidence.

## Follow-on owner actions

1. In GitHub repository settings, verify/enable Dependency Graph:
   https://github.com/RichardRacette/odysseus/settings/security_analysis .
   This requires repository-administration rights; read-only connector calls
   cannot establish or change this setting.
2. Resolve production FastEmbed/Pillow constraints via an actually tested,
   supported dependency path; do **not** confuse the opt-in backport experiment
   with release approval. Re-run strict advisory checks and required review.
3. At a safe maintenance window, run the physical checklist in
   `docs/PROJECT-FACTORY.md`; preserve old backups, verify an encrypted backup
   recipient and compare exact receipts with the accepted commit.
4. Only after all gates pass, request explicit review and an authorized
   integration/merge decision. A factory review action is not permission
   to merge or deploy.

## Important design boundaries for the next session

- The queue is the **single execution authority**. No parallel scheduler or
  worker RPC semantics were invented; the factory adds metadata to the
  existing `queue.sqlite3` that is covered by its snapshot system.
- `advance` is an **explicit** idempotent reconciliation call. There is no
  continuously running factory DAG scheduler in v0.1.
- The queue enforces capability/model allowlists and lease fencing. GPU
  routing additionally requires an explicit administrator-owned `gpu`
  resource label; CPU thread count above one is rejected. Availability
  reflects recent authenticated worker contact, not hardware attestation.
- Only `text.hash` and `json.validate` are independently checked against
  declared inputs in this v0.1; generated language is `reported_only`
  and is prohibited as an automatic dependency.
- No arbitrary subprocess, shell, production modification or third-party
  package install action is available. Human approval stores an audited,
  content-hash-referenced decision but grants no further capabilities.
- Storing a SHA-256 does **not** sign or authenticate the packet. Store a
  trusted copy of its digest separately to detect tampering.
- Restored config disarms project roots. Factory rejects new dispatch,
  retry and approval until the project is reapproved using the existing
  admin registration path.

## Next multiplier milestone

Build a **sandboxed, allowlisted test-runner capability**, scoped to an
immutable repository checkout + pinned interpreter + specific test target,
with bounded resources, no automatic network/cloud access, and machine-
readable test artifacts. Then add a durable, optional hub-side reconciler
to move verified DAG steps forward after worker completion without requiring
a manual `advance` after each dependency. Preserve all security gates,
stop conditions and human approvals. Do **not** introduce arbitrary command
execution under the guise of a test runner.

## Minimum next-session prompt

"Resume Odysseus Project Factory v0.1 from draft PR #9, not from memory.
Inspect latest commit and check the recorded acceptance matrix in
`integrations/homelab/docs/PROJECT-FACTORY-HANDOFF.md`. Run the full
Home Lab test suite and the disposable factory demo on the exact branch
head using existing infrastructure. Fix real failures, record exact receipts,
keep dependency review and physical-host acceptance separate, and do not
merge/deploy or relax security gates without explicit approval."
