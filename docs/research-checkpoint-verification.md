# Research timeout checkpoint verification

## Round 2 application recovery (2026-09-05)

The sections below this update retain the historical Round 1 measurements.
Round 2 preserves that commit and its original nine tests, then closes the
application recovery gaps in a dependent review unit.

- Each completed extraction is checkpointed before sibling work finishes.
- Production search/fetch run through disposable workers. Cancellation and
  deadlines kill and reap them before recovery publishes anything.
- Research uses a monotonic budget, checks cancellation between phases and
  after final generation, and reports measured elapsed time on timeout.
- Save preparation uses `core.atomic_io.atomic_write_json` in a disposable
  worker, targeting a unique sibling candidate. Normal saves have at most the
  remaining outer budget, capped at ten seconds. Deadline/dependency recovery
  has an explicit **maximum ten-second checkpoint grace**, with no model or
  source work. Only the current, uncancelled task with an unchanged owner may
  atomically replace the report after preparation. A failed preparation leaves
  the prior report intact. The final filesystem rename is the linearization
  point; this is not a guarantee against a hung kernel/filesystem.
- Partial state survives consumption/reload on status, result-peek, report,
  research-library and CLI surfaces. Legacy consumed complete results retain
  their status-404 behavior; result-peek and the report remain available.
- Existing chat continuation loads owner-scoped saved evidence, deduplicates
  source URLs, skips completed source fetches and receives a fresh finite
  budget (1800 seconds if the normal outer setting is unlimited). Planning
  queries use the explicit new question without prior private report text;
  synthesis treats prior/fetched text as untrusted evidence.

Verification: **271 unique focused tests pass**, including the unchanged nine,
new failure variations, canonical subprocess search/fetch through a loopback
provider, blocked-fsync cancellation/deadline, owner rename during preparation,
ownership/routes, research CLI, visual reports, atomic I/O and prompt security.
This is not the full application suite. Run the core regressions with:

```sh
python -B -m pytest -q tests/test_research_checkpoints.py tests/test_research_recovery.py -p no:cacheprovider
```

The session's broader test manifest and isolated staging scripts are retained
in the operator handoff. All diagnostics are captured by the runner, which
emits allowlisted totals and hashes. The original nine-test file and fixture
are byte-for-byte unchanged.

Actual application observation: `LOCAL_MODEL_WITH_FIXTURE_SOURCES`. Fresh
synthetic SQLite/data storage, normal authentication and canonical chat/report
routes were used; unrelated startup schedulers were disabled in staging.
Only loopback staging and the existing local model endpoint were reachable.
No cloud fallback, real web source, download or live installation data was used.
The existing `gemma3:4b` (Q4_K_M, digest
`a2af6cc3eb7fa8be8504abaf9b04e88f17a119ec3f04a3addf55f92841195f5a`)
ran at temperature 0, at most 1024 output tokens per call, one extraction at a
time, two research rounds, and a 60-second budget.

The deliberately stalled first run saved an explicit partial report at 60.0
seconds with one cited synthetic source (six model calls, four searches, one
fetch). After process restart, the report and partial badge were visible and
status returned 200. Explicit continuation preserved that source and lineage,
made six model calls and six fixture searches with **zero duplicate fetches**,
and completed in 23.7 seconds of research / 29.75 seconds of handler elapsed
time. The result remained available after consumption. The generated text ends
abruptly at the demonstration's token limit and includes generic `[research]`
markers alongside the preserved source link: this verifies recovery and
continuation, not comprehensive report quality. Screenshots and exact synthetic
inputs are retained in the operator handoff.

The authorized source/startup inspection still did not establish the operator's
outbound privacy gate. Search-provider dispatch and SSRF protections are not
proof of that gate. **External research remains blocked.** No public live
example, installed-data restore or deployment is claimed.

Review the checkpoint prerequisite first, then this dependent recovery change.
No schema migration is required. Rollback is a normal reviewed revert of the
dependent change followed by the prerequisite if needed; preserve saved reports.
Operator: open a partial report in Deep Research, then explicitly request
continuation in its existing chat with a finite budget once the applicable
outbound privacy gate has been verified.

## Historical Round 1 record

Verified offline on 2026-09-05 against fork `dev` at
`c2535d21c4b97c11fe29e74cdb163354a92da35c`.

When the hard deadline interrupted a later round, the handler lost completed
research: `evolving_report` was only published after the round loop. A deadline
during first synthesis also discarded extracted findings; a continuation could
lose supplied evidence while planning. The frozen tests reproduced all three.

The runtime now publishes continuation state before planning and checkpoints
each completed synthesis. On timeout, the existing handler saves the checkpoint
or uses the existing compiled-findings fallback. Saved Markdown explicitly says
the result is partial; raw report, statistics, sources and owner remain available
through normal persistence. A timeout without evidence remains an error.

## Installed baseline, sanitized

| Item | Observation |
| --- | --- |
| OS | Windows, reported NT version 10.0.22000.0 |
| Installation | Native checkout A; process metadata references checkout A |
| Checkout A | `fix/default-document-rag-on`, `2da2ecdb342fafba96989dadd6dab347b46a51d1` |
| Dirty state | No tracked or staged differences; untracked enumeration was incomplete because the existing pytest cache denied access |
| Test checkout B | Dedicated clone of `RichardRacette/odysseus`, clean `dev` before this patch |
| Python | 3.13.15 from installation A's virtual environment, used read-only with bytecode writes disabled |
| Packages | MCP 1.29.1; FastAPI 0.141.1; Uvicorn 0.52.4; SQLAlchemy 2.0.52; httpx 0.28.1; Pydantic 2.13.5; pytest 9.1.1; pytest-asyncio 1.4.0 |
| Vector packages | Both chromadb and chromadb-client 1.5.9 metadata exist; runtime vector mode was not probed or changed |
| Model manifests | `qwen3.5:4b` and `gemma3:4b` exist; no model was loaded or downloaded for this work |
| Process topology | Twelve Python process command lines reference A, including two Uvicorn and eight built-in MCP matches. Counts may include launcher/child pairs; they do not establish independent instance counts |
| Startup | Two matching Task Scheduler records are running and reference the installation/launcher; task names and arguments omitted |
| Model service | An Ollama process was observed; its app endpoint relationship was not probed |
| Unknowns | Exact in-memory app revision, search service topology, active model, vector-store mode and consistent live backup boundary |

Read-only process/startup metadata required elevated sandbox access. No startup,
Tailscale, auth, service, endpoint, dependency or installed checkout was changed.

The handoff reports an operator outbound-research privacy gate, but the targeted
tracked/local-source inspection did not locate its definition or sanitized runs
1–3. Its tracked/local/external boundary remains **unknown**. The existing gate
was not replaced. No real research or model inference was performed.

## Frozen evidence

Fixture: `tests/fixtures/research_checkpoint_cases.json`, SHA-256
`3df2235b32fc9af08fa9ad69100882c45b6818c379673411ddacd74f34734dc2`.
The fixture and test code were frozen before editing production code. The same
Python, dependencies, fake model, settings and inputs ran before and after.

| Outcome | Before | After |
| --- | --- | --- |
| Regression tests | 6 passed, 3 failed | 9 passed |
| Later-round timeout | Error; no saved report/source | Explicit partial report; one saved source |
| First-synthesis timeout | Evidence lost | Compiled findings saved with source |
| Continuation-planning timeout | Prior evidence lost | Prior report and source saved |
| Later-timeout calls | 1 search, 1 fetch, 2 fake model calls | Same |
| Later-timeout model input | 2,058 characters | Same |
| Later-timeout wall time, one sample | 0.2164 s | 0.2157 s |

All other fixture call counts and input character counts were unchanged. Tokens
were unavailable; character counts are not token counts. Single-run timings are
noisy harness observations, not an inference-speed improvement. The demonstrated
improvement is deterministic evidence retention, without additional model work.

Normal cited, conflicting, insufficient-evidence, malicious-source and private
sentinel cases exercise actual orchestration with fake model/search/fetch
dependencies. They preserve citation URLs, keep fetched content in the existing
untrusted user-role wrapper, and prevent the synthetic sentinel or injected
collection URL from reaching fake search/fetch requests. Socket connections are
blocked. This does not establish real-model reasoning quality or validate the
unlocated operator privacy gate.

## Reproduce

From a disposable checkout with the documented Python environment installed:

```powershell
$env:DATABASE_URL = 'sqlite:///:memory:'
$env:ODYSSEUS_DATA_DIR = "$PWD/data"
$env:PYTHONUTF8 = '1'
python -B -m pytest -q tests/test_research_checkpoints.py -p no:cacheprovider
```

The tests inject dependencies and create reports only under pytest temporary
directories. The nine-test demo shows the actual handler saving partial evidence
on its 0.2-second fixture deadline. No app startup or paid endpoint is involved.

The session's broader runner additionally used a sanitized child environment,
an offline socket/DNS guard and existing dependencies read-only. Windows asyncio
requires its stdlib loopback socketpair; only that exact wakeup-pipe call site was
allowed by the guard. The normal repository conftest ran throughout.

Validation covered 406 unique tests: all `tests/*research*.py`, research CLI
tests, visual-report tests, backup CLI security, prompt security/injection,
external-context tool gates and outbound URL type validation. The broad run
passed 404 with two Windows default-encoding failures; those exact two tests
then passed with `PYTHONUTF8=1`. This is a combined focused result, not a full
application suite or a single 406-pass invocation. Existing deprecation warnings
remain. Changed Python files compiled, and `git diff --check` passed.

Earlier runner-only failures were corrected without changing repository tests:
the DNS blocker now raises the standard unavailable-DNS exception; disposable
data uses checkout B's normal data location for existing relative-path tests.

## Backup and live gates

The documented `scripts/odysseus-backup` implementation passed a synthetic drill:
snapshot an open SQLite WAL database, verify the archive, restore into a new
disposable root, check SQLite integrity and rows, compare synthetic key and
vector-placeholder bytes, and confirm the source still reads correctly. The
archive and restored files remain local under ignored `data/` in checkout B.

This is not an installed-data or Chroma consistency validation. A live drill
remains gated on identifying the active data/vector topology and a consistent
method that does not interrupt service. No installation/data change is proposed
or applied in this session.

## Rollback and next action

This is an isolated code patch, not a deployment. Discarding the proposed patch
leaves the installation intact. If adopted later, revert the patch commit using
the normal review process; it has no schema or data migration.

Next action: identify the operator privacy-gate definition and sanitized frozen
benchmark location, then plan local acceptance through that existing boundary.
Do not run real outbound research until the gate is understood and verified.
# Round 3 streaming boundary follow-up

A real child emitting 4 MiB plus one byte and then blocking reproduced the
post-`communicate()` size-check gap: the previous implementation waited for its
deadline instead of rejecting overflow. The helper now enforces the retained
stdout limit incrementally, writes stdin concurrently, kills overflowing or
cancelled children, drains bounded chunks and reaps them even after repeated
cancellation. Worker import diagnostics go to the null device instead of an
unbounded StringIO. This bounds retained stdout in the parent; it is not a total
process-memory limit on source providers, parsing or JSON serialization.

The focused recovery set passes 39 cases, including the unchanged frozen nine,
owner/save/cancellation coverage, full-duplex exact-limit output, overflow before
EOF and repeated cancellation of a real child. The separate historical sets are
406 (Round 1) and 271 (Round 2): 229 shared identities, 42 added in Round 2 and
177 not selected in Round 2. These counts must not be substituted or summed.

The existing research model abstraction still returns text without completion
metadata. Its report-quality/truncation limitation remains unresolved. The
independent artifact worker pilot uses native provider metadata and refuses
truncated/unknown completion; it does not silently change existing research
callers. External research remains blocked pending verification of the running
operator's outbound privacy gate.
