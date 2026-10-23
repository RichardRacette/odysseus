# Project Factory v0.1 — operator runbook and threat model

**Status:** implementation candidate, stacked on the Home Lab portability branch. The
factory does not merge PRs, deploy services, execute commands, contact cloud APIs,
install packages, or replace any existing mission/job/knowledge data.

## What changed

The executable is [../factory.py](../factory.py); the implementation is
[../homelab/factory.py](../homelab/factory.py). Both reuse the existing
QueueStore and worker implementations. All factory mission tables are created
inside the authoritative `queue.sqlite3` database. Consequently, the existing
SQLite snapshot/verify/restore machinery includes factory state automatically.

The operator controls a sequence of explicit state transitions:

```
registered project → validated mission → durable definition → dependency-ready
steps → queued typed job → authenticated worker lease → completed job →
independent fixed-output verification → hash-sealed review packet →
explicit human approval
```

The factory is a **hub-local controller**. There is no second job server, no
ambient remote access, no general-purpose script runner and no cross-device
SQLite mount. Connect to the hub console by your existing approved private
access method. Workers continue to communicate only with the current
loopback-bound, authenticated Home Lab hub over the existing transport.

## Fresh operator setup

Python 3.10+ with SQLite, FTS5 and the existing companion are sufficient. Do
not install new packages or touch your working services to try the factory.

From `integrations/homelab`:

```sh
python factory.py --help
python -m unittest discover -s tests -v
python -m compileall -q homelab lab.py factory.py
```

Run a self-contained integration drill in a new private directory (choose a
new path; **do not place it in Git**):

```sh
python factory.py demo --output "$HOME/odysseus-factory-demo-001"
```

PowerShell:

```powershell
py -3 factory.py demo --output "$env:LOCALAPPDATA\OdysseusFactory-demo-001"
```

This demo verifies a **real existing repository example**
`examples/validate.json` using the current fixed worker implementations via
real loopback HTTP with separately scoped worker credentials. The first step
hashes the pinned example; the dependent second step checks its JSON fields.
It reopens the persistent mission controller, seals and independently
re-hashes the evidence, records a **demo-only approval**, takes a snapshot,
restores into a fresh directory and verifies the approval survived. It must
stop if the checked-out input differs from the pinned copy. It does **not**
use the historical Mission 001 offline 72-job kit (not in this repository).
It also does not prove multiple physical hosts, model inference, correct
semantic conclusions or the GitHub security gate.

The output contains test credentials, source contents and execution receipts.
Restrict access and protect it with an encrypted backup before retaining it.

## Enroll an actual existing hub, without replacing its state

Use your **current** hub configuration; do not run `lab.py init` on your
working state directory. If your intended project is not already registered,
register its **specific existing root** from the hub and restart the hub:

```sh
python lab.py project-add --config /private/homelab/config.json \
  --project lab --root /absolute/path/to/approved/project
```

To submit the included example, first verify that `lab` is registered, then
from the authoritative hub:

```sh
python factory.py submit --config /private/homelab/config.json \
  --mission examples/project-factory-release-audit.json
python factory.py advance --config /private/homelab/config.json \
  --mission-id factory-release-audit-v01
python factory.py inspect --config /private/homelab/config.json \
  --mission-id factory-release-audit-v01
```

Start or maintain your **existing** authenticated workers normally (see
[RUNBOOK.md](RUNBOOK.md)); no factory-specific worker installation is needed.
Call `advance` again after any dependency finishes; it is safe to call
repeatedly. In v0.1, **progression of dependent steps requires an operator
advance or a separately authorized scheduler**; it does not silently spawn a
polling daemon.

When `inspect` reports `review_required`:

```sh
python factory.py seal --config /private/homelab/config.json \
  --mission-id factory-release-audit-v01
python factory.py export --config /private/homelab/config.json \
  --mission-id factory-release-audit-v01 \
  --destination /private/evidence/factory-release-audit-v01.json
python factory.py verify-evidence \
  --file /private/evidence/factory-release-audit-v01.json
python factory.py approve --config /private/homelab/config.json \
  --mission-id factory-release-audit-v01 --reviewer local-operator \
  --evidence-sha256 PASTE_EXACT_REVIEW_PACKET_SHA256
```

Approval writes an explicit audit event and seals a **new final packet**;
it grants **no** merge, shell, deployment, credential or spending authority.
Models may be queued only if the specific model exists in the hub's configured
exact allowlist; their generated words stay `reported_only` until human review.
For those missions, approval additionally requires `--accept-reported`, and
a reviewed output is still not an independently factual verification.

For a failed step, inspect the sanitized failure and retry **deliberately**:

```sh
python factory.py retry --config /private/homelab/config.json \
  --mission-id factory-release-audit-v01 --step-id integrity
```

The previous job and packet remain in the database. A new bounded generation
is submitted with a different idempotency key. At most three **manual**
retries per step are allowed; normal queue retries retain their own existing
bounded attempts and exponential backoff.

## Mission definition contract

See [../examples/project-factory-release-audit.json](../examples/project-factory-release-audit.json).
Each mission is immutable under its unique `mission_id`; a different body
with the same ID is rejected. A new task revision requires a new ID.

Required fields: `schema_version=1`, `mission_id`, registered `project`,
`objective`, pinned GitHub HTTPS `repository.url` and 40-hex `revision`,
bounded input references with SHA-256, `expected_outputs`,
`acceptance_criteria`, restrictions, mandatory human approval and a bounded
acyclic list of steps with IDs, dependencies and fixed typed queue payloads.
The schema does not accept unknown keys, general tools, links with tokens,
or arbitrary execution commands.

Only `text.hash`, `json.validate` and `ollama.generate` are supported.
Hash steps **must** carry the exact expected source digest; the factory checks
both the declared digest against its own independently computed bytes and the
returned bytes/hash. JSON steps independently check parseable object keys and
the worker's shape verdict, **not semantic accuracy**. Model output is
`reported_only` and cannot auto-unblock a downstream step.

Default steps use the **existing unpinned pull-based router**, which admits
only configured node capabilities and exact model allowlists and adapts to
workers appearing and disappearing. A pinned `target_node` is supported.
An explicitly requested GPU requires the hub owner's `resources.gpu=true`
label on a capable configured node; the factory pins the most recently
healthy such node so that the existing queue never dispatches a labelled GPU
job to an unlabelled CPU node. This label is a **configuration assertion**,
not hardware attestation. A GPU-targeted job will wait if its selected worker
is offline; automatic GPU rehoming is **not** implemented. CPU thread count
other than one is deliberately rejected until the queue can enforce it.
Worker health means recent hub claim/heartbeat only; it is not a measured
success-rate or physical-resource probe.

## Durable guarantees and limitations

- **Crash during dispatch:** mission row is committed first; deterministic
  project-scoped queue idempotency keys allow `advance` to repair the gap
  between queue admission and the mission-to-job reference.
- **Interrupted worker:** queue lease expiry requeues up to the existing
  maximum. Expired/stolen/cancelled leases cannot commit stale results.
  Computation *can* repeat; workers execute only bounded, no-side-effect
  types. Never claim exactly-once computation.
- **Retries and failures:** worker retries are bounded; terminal error or
  verification mismatch blocks downstream execution until an explicit retry.
  Completed or approved missions are immutable except by a new mission ID.
- **Evidence:** durable, append-only revision/stage packets live inside
  `queue.sqlite3`; a public command exports a *new* private file and refuses
  overwrite. It includes source identifiers, pinned manifest SHA, worker
  attribution, job IDs, attempt counts, timestamps, result/error and their
  digests. The worker software version is **not attested**; the operator
  Python version is recorded. Input source hashes are declarations until
  independently compared with trusted source bytes.
- **Integrity vs authenticity:** `verify-evidence` checks canonical JSON
  integrity. Without a separately trusted hash/signature it **does not**
  prove authorship, authoritative source contents, physical worker identity
  or absence of tampering by a machine administrator.
- **Backup:** existing `lab.py snapshot`, `verify-snapshot`, and disarmed
  `restore` cover the factory because no new data store is introduced.
  Snapshots contain private results and credentials and are unencrypted:
  protect them with the existing encrypted-backup system. Do not delete or
  modify existing backups, jobs, notes or credentials as part of acceptance.
- **Concurrency:** SQLite serializes missions and job admission. Manual
  retries preserve previous dispatch IDs. Queue/result size and job
  admission limits remain inherited from the companion.
- **Trust boundary:** source text and model output are untrusted; never
  execute instructions embedded in either. Factory is hub-admin-only and
  supplies no new HTTP endpoint or worker permission. Review/approval does
  not authorize production changes, credential disclosure, cloud spending
  or protected merges.

## Physical three-node acceptance checklist

Perform against a **disposable test mission**, not family/personal input, and
record UTC timestamps, source SHA and private receipts.

1. Confirm the exact checkout and expected GitHub checks; inspect source
   and back up the live hub state before any installation.
2. Start the authoritative hub normally. From the Mac controller, use an
   approved private SSH session to run `factory.py inspect` on the hub;
   never share the SQLite database over the network.
3. Stop the Latitude worker. Queue a lightweight unpinned step; verify
   healthy RTX or Mac worker claims it. With *all* eligible workers offline,
   verify the mission remains queued, not lost.
4. Interrupt a worker while executing a *disposable*, local-only step;
   confirm lease expiry, bounded retry and stale-result rejection using
   the existing queue recovery tests. Never kill production processes as a
   substitute for a safe drill.
5. Restart the **operator process** and then the hub at a maintenance
   window. Confirm the mission/job IDs survive and the next `advance`
   is idempotent.
6. Finish the dependent step with a second verified eligible node; export
   and verify the evidence and approve it with the exact reviewed hash.
7. Perform a fresh encrypted snapshot and restore to a disarmed destination;
   confirm both completed and queued factory missions are present and
   old snapshots are untouched. Perform the separately required recipient
   verification for the encrypted backup.
8. Verify Windows logout/login, reboot, sleep/wake and normal-day uptime as
   **new** host acceptance evidence; prior receipts are historical only.

Record **pass / fail / not run**, the host-specific software versions and
evidence locations. A green CI job is not a physical acceptance result.

## Security and CI release gate

PR #5 is stacked on #4, targets the portability branch and historically had
a required dependency-review failure at run 39870163475. GitHub Dependency
Graph was disabled and strict pip-audit flagged Pillow 12.3.0. An opt-in
reproducible FastEmbed/Pillow 13 builder was merged through #7, but it does
**not** change default dependency security. Draft PR #8 separately exercises
the *candidate* dependency combination; neither a candidate green run nor
these factory tests clears the production security gate.

Repository owner action: open
https://github.com/RichardRacette/odysseus/settings/security_analysis
and enable/verify Dependency Graph if allowed. Re-run **the exact security
workflow** after settings are corrected, and resolve default dependency
advisories through a reviewed, passing and supported dependency change.
Do not skip required checks, suppress advisory findings, force merge or
adopt unreviewed derived wheels.

## Next multiplier

A deliberately scoped **test-runner adapter** should accept only an immutable
Git checkout, an operator-approved interpreter, a strict test-target allowlist,
a time/resource limit and isolated workdir; its output should become a hash-
verified factory artifact. Design and review its sandbox/security boundary
before introducing execution. Then add a restartable hub-side reconciler that
uses the same existing mission journal, without expanding worker privileges.
