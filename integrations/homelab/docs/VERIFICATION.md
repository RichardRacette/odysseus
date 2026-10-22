# Verification receipt

Date: **2026-10-22**. Base repository commit: `c2535d21c4b97c11fe29e74cdb163354a92da35c` (`dev`). This receipt describes the isolated companion implementation, not a deployed physical lab.

## Portability follow-up

The original [CI run 39814869411](https://github.com/RichardRacette/odysseus/actions/runs/39814869411)
passed Ubuntu and Python 3.10/MCP but failed Windows and macOS. The separate
`codex/homelab-portability` correction fixes snapshot flushing, SQLite cleanup,
cross-platform source metadata checks and loopback startup's reverse-DNS
dependency. [PORTABILITY.md](PORTABILITY.md) records causes, regression evidence,
native-CI limits and safe adoption by an already running local rollout.

After these changes, the root-owned combined run in the same Linux/Python
3.12.14/MCP 1.30.0 environment completed:

```text
python -m unittest discover -s tests -v
Ran 130 tests in 16.259s
OK
```

**130 passed; zero skips.** The eight added cases include stable Windows-style
metadata, mutation rejection, constructor cleanup, snapshot recovery and flush
failure, and a real HTTP request without reverse DNS. The targeted defect
regressions failed before their respective production fixes. Independent review
found no blocking findings. Python compilation, shell syntax and `git diff
--check` also passed. The demo's original 20-second subprocess limit remains.

The earlier implementation receipt is preserved below. Consult the correction
PR's actual checks for native Windows/macOS results; this local run alone is
not a cross-platform or physical-host acceptance claim.

## Executed here

Environment: Linux, Python **3.12.14**, optional MCP SDK **1.30.0** in an isolated verification environment. Core code uses the standard library. Integration tests used real loopback sockets; no external model/API or household corpus was used.

```text
python -m unittest discover -s tests -v
Ran 122 tests in 16.252s
OK
```

**122 passed; no skips in this run.** The SDK test performed an actual stdio initialize/list-tools/call-tool exchange against a real authenticated HTTP hub and source index.

| Area | Evidence exercised |
| --- | --- |
| Queue and routing | Independent-store competing claims; persistence; capability/model/capacity enforcement; bounded retries; idempotency; expiration/fencing; clock read after database-lock acquisition |
| Worker | Real synthetic Ollama HTTP fixtures; heartbeats during inference; late-result refusal; local inventory and `/api/show`; independent allowlist; proxy/redirect refusal; timeout recovery |
| Knowledge | Exact source hash/text/line provenance; duplicate source occurrences; project isolation; hidden/credential/symlink exclusions; updates/deletions; stale-source withholding; authored memory and evidence references |
| Embeddings | Native request shape and retrieval prefixes; vectors/fingerprints; numeric/dimension validation; local-only inventory; no source submission to an unverified model; visible fallback |
| Access and configuration | Scoped worker/reader tokens; cross-project refusal; no active lease token in public results; private transport validation; project validation before credential writes; mismatched MCP project/token refusal |
| Recovery | Consistent WAL snapshot; manifest/file tampering; restore into fresh state; preserved attempts/results; invalidated leases; cleared roots; rotated/rebound credentials; pull-only local Git mirror |
| Operator interfaces | CLI flow; real MCP protocol; launcher argument preservation; full synthetic demonstration |

Also passed: Python compilation and POSIX shell syntax check. The PowerShell launcher was reviewed here; this environment has no PowerShell executable.

## Standalone demonstration

Ran `lab.py demo` separately at `2026-10-22T16:26:12Z` with a new output folder. Receipt:

```json
{
  "status": "passed",
  "checks": {
    "http_worker_round_trip": true,
    "three_distinct_worker_credentials": true,
    "idempotent_submission": true,
    "source_citation": true,
    "pending_job_survives_restart": true,
    "restore_integrity": true
  },
  "completed_jobs": 3,
  "pending_jobs": 1,
  "physical_nodes_verified": false,
  "model_inference_verified": false
}
```

Three credentials represent three roles on one test host. The demo verified actual HTTP/worker behavior and restored retrieval against the synthetic source; it does not establish three-machine transport or model performance.

## Independent review

Queue/worker, knowledge/embeddings, and operations were implemented in separate scopes and reviewed across their integration boundaries. Review findings resulted in regressions for delayed-lock lease expiry, overlapping project registration before writes, MCP project/token mismatch, and local model verification. The final run above includes those corrections.

## CI and unverified boundaries

The added workflow defines Linux/Windows/macOS core jobs and an Ubuntu Python 3.10 job with the optional MCP dependency. Consult the actual pull request checks for execution status; the presence of a workflow is not a passing CI receipt.

The existing full Odysseus application suite was attempted in the isolated environment but could not collect because its SQLAlchemy dependency/import setup was unavailable. The existing application was not launched here. The companion changes are confined to `integrations/homelab` and its new workflow, but that isolation does not substitute for the repository's full checks or a deployment smoke test.

Still unverified: physical Windows/macOS installation and startup, private Tailscale/SSH connectivity, GPU placement and memory, real model output quality, existing-app coexistence, pre-login availability, and encrypted off-machine backup. Record those results in a private copy of [HOST-ACCEPTANCE.md](HOST-ACCEPTANCE.md). Never label them passed based on this receipt.
