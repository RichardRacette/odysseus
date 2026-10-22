# Portability correction and rollout continuation

Date: 2026-10-22. This correction is based on companion commit
`021a8b11c0d66f206289de68dc811f6928b89e72`, on a separate
`codex/homelab-portability` branch. It does not change a deployed host.

## Why this correction exists

[The original Home Lab CI run](https://github.com/RichardRacette/odysseus/actions/runs/39814869411)
passed Ubuntu and the Python 3.10/MCP job but failed Windows and macOS.
The initial Linux verification receipt was accurate for its environment; it
did not establish cross-platform acceptance.

| Failure | Correction | Regression evidence |
| --- | --- | --- |
| Windows snapshot creation failed; the completed SQLite backup was reopened read-only before flushing | Reopen with `r+b`, without truncation, before `fsync` | Real WAL backup, verification and restore under a writable-flush requirement; injected I/O failure still leaves no published or partial snapshot |
| Corrupt knowledge database retained an open connection | Close the connection on every initialization exception and re-raise | Real failed initialization leaves a closed SQLite connection and preserves the corrupt input |
| Windows fixtures produced CRLF bytes while asserting an LF hash; test connections prevented temporary-file deletion | Write explicit UTF-8 bytes and explicitly close test connections | Existing exact-source hash assertions remain unchanged; transaction-writing tests still commit |
| Descriptor and path timestamps could disagree for a stable Windows source | Compare device, inode, size and mtime across descriptor/path observations; compare ctime only across descriptor observations | Stable differing-ctime source is indexed and cited; descriptor mutation and path identity mismatch are rejected |
| A mutation during the last path check could escape the earlier descriptor comparison | Recheck the open descriptor after path verification | Same-size, unchanged-mtime mutation during the path check is rejected |
| A local hub inherited an unnecessary reverse-DNS dependency during bind | Use the literal loopback identity after the underlying TCP bind | A real hub starts and serves `/health` with reverse DNS unavailable |

The last defect is consistent with the original macOS log: the demo reached its
20-second subprocess limit, and the first in-process synthetic HTTP server later
took approximately 35 seconds to start while subsequent starts were fast. The
original run has no stack dump proving that particular timeout's location. The
new regression proves the unwanted DNS dependency directly, and native CI must
confirm the corrected demo. The 20-second demo-test limit is unchanged.

The original snapshot exception hid the lower-level OS errno. The writable-handle
requirement is established by the platform API and the regression, with native
Windows CI providing the integration check.

## Platform references

- [Microsoft FlushFileBuffers](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers)
  requires write access to the file handle.
- [Python's SQLite connection context manager](https://docs.python.org/3/library/sqlite3.html#how-to-use-the-connection-context-manager)
  manages transactions but does not close the connection.
- [CPython issue 157671](https://github.com/python/cpython/issues/157671)
  documents differing Windows `fstat` and path `stat` ctime semantics. The
  correction preserves same-API version checks rather than dropping ctime
  protection entirely.
- [CPython HTTPServer implementation](https://github.com/python/cpython/blob/v3.12.10/Lib/http/server.py)
  performs a reverse lookup in its default bind method. This fixed local service
  does not need a fully qualified hostname.

## Incorporate into an already running rollout

Use the existing local coding-agent session. Complete its current atomic
operation before integrating this correction. Do not launch a second writer in
the same checkout or replace local work with a reset.

1. Inspect the current branch, working tree, running services and local fixes.
   Preserve uncommitted work and private deployment state.
2. Fetch `codex/homelab-portability` from the verified origin and inspect its diff
   against the foundation commit above. Confirm the published PR's exact head
   and current checks before selecting a commit.
3. Incorporate the correction on the rollout's own development branch. If local
   fixes overlap, reconcile behavior and tests deliberately; do not blindly
   replace files or change an active service's source underneath it.
4. Run the complete companion suite on the actual Mac and Windows hosts. The
   optional MCP SDK is needed only for the protocol test; report a skip honestly
   if it is absent. No model download is required for these tests.
5. Run the synthetic demo into a new private directory, then resume
   [CODEX-HANDOFF.md](CODEX-HANDOFF.md) at the first incomplete host gate.
6. Verify real node identity/transport, one installed-model job, cited retrieval,
   restart behavior and restore. Record the deployed source revision in the
   private [HOST-ACCEPTANCE.md](HOST-ACCEPTANCE.md) receipt.

From `integrations/homelab`, the verification commands are:

```sh
python -m unittest discover -s tests -v
python -m compileall -q homelab lab.py
```

Use the appropriate existing Python executable on each host. Run the documented
`demo --output` command with a new private path. Its output contains synthetic
credentials and must not be committed.

## Acceptance boundary

Read the correction PR's actual platform checks for current CI results. A
passing hosted Windows/macOS runner is not a receipt for the user's machines.
No schema migration, token rotation, model selection, public exposure or paid
service is required by this patch. Existing runtime policy and data format are
preserved. Physical startup, model quality, private connectivity and off-machine
recovery remain separately observed acceptance outcomes.
