# Durable Dispatcher — operator guide

This opt-in companion advances explicitly authorized, fixed-output Project Factory missions through the existing queue. It does not require the ZBook, an LLM, Docker, third-party packages, or a running Odysseus web application. Python 3.10+ with the existing SQLite/FTS5 requirements is sufficient.

## What is implemented

- Execution permits bind a mission ID, manifest SHA-256, operator authorization reference, and registered project-root hash. They persist in the existing `queue.sqlite3` and its snapshots.
- A supervised coordinator repeatedly calls the existing Factory progression API. Existing scoped workers claim and complete existing typed jobs over authenticated loopback HTTP.
- Kernel-owned process locks serialize state changes and prevent two continuous coordinators on one state directory. Abrupt process termination releases the lock.
- Queue leases, stale-result fencing, bounded queue retries, immutable Factory definitions, evidence sealing and disarmed restore remain authoritative.
- Small queue-side hooks enforce dispatcher scope at worker claim, heartbeat and result acceptance, before a restarted coordinator can run. Deterministic Factory admission keys identify committed jobs even if a crash prevented step bookkeeping.
- Successful fixed outputs are independently recalculated against a separately trusted manifest. Receipt publication is atomic and exclusive; identical files are adopted after a restart and conflicting files are preserved.
- Only `text.hash` and `json.validate` are admitted in this milestone. Existing model serving, game code, test-runner candidates, shell permissions and live services are unchanged.

Execution authorization is not final product approval. The coordinator never calls `Factory.approve`; final Factory review and human/device acceptance remain separate. Pausing cancels outstanding queued/running claims, including committed-but-unlinked admissions. If interrupted during revocation, queue-side checks already refuse unauthorized results and the next tick finishes cancellation. Re-admitting the same ID does not unpause it; use a deliberately authorized new mission revision.

## First run: disposable interruption proof

Use a reconciled candidate checkout and a NEW private directory outside that checkout. Its parent must already exist. From `integrations/homelab`:

```powershell
py -3 scripts/verify-durable-dispatch.py --output "$env:LOCALAPPDATA\durable-dispatch-proof-001"
```

```bash
python3 scripts/verify-durable-dispatch.py --output /absolute/private/new-proof-001
```

The script uses real child processes, the existing hub HTTP server, and the real constrained worker. A test-only gated client pauses that worker immediately after an authenticated claim; the script kills the worker and coordinator, starts fresh processes, waits for the **normal 60-second queue lease** to expire, and proves both dependent steps finish. Budget about 1–2 minutes. The gate adds no production job kind or API and is not a long-running game/build worker test.

It also resubmits the same mission, rejects an expired worker's late result, reopens the coordinator without duplicating receipts, verifies outputs independently, and snapshots/restores into disarmed state. Focused regressions additionally restore queued work before coordinator startup and interrupt publication before a separate human approval. Every child is stopped at the end, including on failure.

`PROOF.json`, the synthetic `mission.json`, and the generated `.report.md` are suitable for review after checking their contents. The state, snapshot, worker export and `claimed-private.json` contain generated test credentials or a lease token. Keep those private; never commit them or paste them into reports. The `.evidence.json` contains full inputs and outputs and is private by default.

This is one-host managed-process acceptance, not physical home-host, GPU, overnight, Windows boot, or power-loss acceptance.

## Admit an actual bounded mission

First identify the ONE authoritative companion hub and obtain a consistent backup. Reconcile the newer native Factory checkpoint before installing this additive candidate. Never initialize over an existing state folder and never start a second hub using a copied live database.

With the accepted private hub config and an already registered project:

```powershell
py -3 dispatch.py admit --config C:\Private\Lab\config.json --receipts C:\Private\LabReceipts --mission C:\Private\Missions\audit.json --authorization-ref owner:20261030:audit
py -3 dispatch.py run --config C:\Private\Lab\config.json --receipts C:\Private\LabReceipts --once
py -3 dispatch.py status --config C:\Private\Lab\config.json --receipts C:\Private\LabReceipts
```

`admit` records an operator's scoped execution authorization. It neither derives consent from a model nor proves the identity behind the reference. Ordinary Factory missions without a matching permit are not picked up. Changed manifest content or a changed/removed project root blocks progression.

Run the accepted hub and its already scoped workers through their existing private transport. This coordinator does not launch them or infer that another agent has started. A paused or failed mission does not receive new manual retry generations automatically. Reconfiguration of project roots requires stopping and restarting the coordinator as well as following the existing hub restart procedure.

For continuous coordinator operation:

```powershell
py -3 dispatch.py run --config C:\Private\Lab\config.json --receipts C:\Private\LabReceipts --interval 2
```

The command remains in the foreground. It emits JSON when mission state changes, honors Ctrl+C/SIGTERM where supported, and exits nonzero on operational errors. Capture stdout/stderr in private rotating host logs. A successful process launch is not evidence that a worker is available; inspect job progress and receipts.

## Windows operation before ZBook Linux

After the disposable native proof passes, the existing Windows host can run the coordinator. Choose it explicitly; do not move or duplicate an existing hub merely to follow this guide.

For an accepted installation, configure a dedicated Windows Task Scheduler task with the exact installed Python executable, absolute `dispatch.py` path, private config and receipt paths, and the companion folder as “Start in”. Set a deliberate startup trigger, “Do not start a new instance”, an appropriate restart-on-failure policy, and a host power/sleep policy that matches unattended use. Do not use the old Odysseus bootstrap launcher: its historical install/startup side effects are outside this task.

This candidate does not register or modify a task. Verify on the actual host: normal start, process termination/restart, sign-out or startup behavior for the chosen account, private logs, worker pickup, and receipts. Enabling boot operation and changing an existing hub are separate native installation actions after source reconciliation and backup.

## Verify and return a receipt to RichieOS

```powershell
py -3 dispatch.py verify --receipt C:\Private\LabReceipts\MISSION-HASH.receipt.json --evidence C:\Private\LabReceipts\MISSION-HASH.evidence.json --mission C:\Private\Missions\audit.json
```

The manifest must come from a trusted separate source. Verification recalculates deterministic outputs and checks identifiers, source pins, attempt bounds, workers, input/output hashes and receipt/evidence agreement. A hash-consistent forged result still fails. Keep the resulting receipt digest in a separate trusted record. SHA-256 alone does not authenticate a packet, operator or physical worker.

The generated `.report.md` excludes payloads, outputs, credential paths and tokens. It is an outbox artifact; the coordinator does not hold GitHub write credentials. Publish it through the assigned agent's permitted `reports/codex/<assignment-id>.md` route. Fae reconciles that report into canonical project records. Assignment files and a report PR do not themselves launch a worker or update canonical state.

## Storage, failure and rollback

Keep the queue, locks and active database on one local disk. Receipt publication requires atomic same-volume hard links (normal NTFS/APFS/Linux filesystems); do not assume FAT/exFAT or arbitrary network shares support the contract. Workers access the API, not SQLite over a network mount. Existing consistent snapshot/restore operations include the additive permit tables.

Stop or disable the coordinator to end automatic progression. `dispatch.py pause --config ... --receipts ... --mission-id ...` durably revokes one mission's remaining claims; already completed computation is historical evidence. No automatic cleanup deletes old evidence. Missing/conflicting receipts, scope drift or verification failures appear as blocked status; operational filesystem/database failures exit nonzero for supervision. Restore only into a fresh directory, preserve project disarming, and reapprove the intended source through the existing admin workflow.

Rollback: stop the coordinator and retain its state and receipts. Its new tables are additive. The queue, Factory and hub include small dispatcher-scope enforcement hooks; preserve those hooks while pending jobs or permits exist, and deliberately revoke/cancel unfinished missions before reverting queue code. Do not remove a shared live database or roll native work back to the published candidate baseline.
