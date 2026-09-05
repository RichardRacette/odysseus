# Research timeout checkpoint verification

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
