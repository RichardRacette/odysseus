# Offline assistant request preparation

This additive preparation tool maps a small untrusted request to the existing
Project Factory's fixed, synthetic `text.hash` and `json.validate` fixture mission.
It does **not** connect assistants, submit work, execute commands, approve work,
add a listener, or install a service. The existing Factory queue remains the sole
execution authority. The existing read-only MCP bridge remains read-only.

## Prepare one request

From `integrations/homelab`, using the existing Python interpreter:

```sh
python agent_request.py --request examples/agent-request.json --output /existing/private/directory/prepared-001.json
```

Replace the output with a new filename in an existing private directory outside
the checkout. On Windows use the corresponding absolute Windows path and the
existing Python command. The tool does not create missing parent directories and
refuses existing files or symlinks. No third-party dependency is required.
Use an ordinary local request file in an operator-controlled directory. The
4096-byte input cap is not an I/O timeout for FIFOs, devices or network filesystems;
this CLI is not a general upload service and does not accept those input sources.

The request has exactly four fields:

| Field | Accepted value |
| --- | --- |
| `schema_version` | Integer `1`, not boolean or float |
| `request_id` | 1–48 ASCII letters/digits/underscores/hyphens; first character alphanumeric |
| `requested_by` | `fae`, `duet`, `grok`, or `operator` |
| `capability` | `fixture.audit.v1` |

Input JSON is limited to 4096 bytes. Duplicate keys, malformed UTF-8/JSON,
nonfinite numbers, unknown fields and unsupported values are rejected. Source
URLs, paths, commands, environment values, credentials and approval fields are
not accepted. The requester label is **unverified attribution**, including when
its value is `operator`; it is not a principal or permission.

The tool verifies the exact shipped mission template and fixture against
source-pinned SHA-256 values before preparation. A changed template or fixture
requires a reviewed code change; requests cannot change those pins. The original
fixture revision remains `5581cdd624b9df49efcd4683e60c29d2da80830f`. The development
baseline for this adapter is Factory commit
`b2691dc31ed0a3a52ac8d4ebdd6aea79eff61d28`. These are different identities: the first
names the fixture provenance, the second the inspected Factory implementation.
Neither proves what is deployed on a physical machine.

The examples directory includes narrowly scoped Git attributes forcing LF for
the two pinned fixtures and the sample request. This preserves their exact bytes
on Windows checkouts with `core.autocrlf=true`; preparation does not normalize
bytes before hashing. Use a fresh checkout of this change for acceptance. If
bringing it into an existing checkout, preserve any local edits before explicitly
refreshing these fixture files from the index. A regression test creates a real
temporary Git repository, enables `core.autocrlf=true`, checks out the indexed
files, and verifies their byte hashes; it does not merely inspect attributes text.

## What is produced

The deterministic envelope contains the validated request, request SHA-256,
prepared Factory mission and its canonical SHA-256. It always reports
`prepared_not_submitted`, `requested_by_verified=false`, and
`execution_authorized=false`. These flags are descriptive, not a security token.

The Factory mission ID is `agent-` plus the request ID. The canonical request hash
is included in the mission input references. After an operator separately admits
a mission through the existing Factory, an identical request reuses the existing
mission; the same request ID with a changed requester produces a conflicting
manifest and is rejected by Factory. Preparation itself has no durable request
registry and makes no claim of exactly-once distributed execution.

## Operator handoff

1. Inspect the request and regenerate it using the trusted checkout. An incoming
   prepared envelope is not trusted just because it contains hashes.
2. Review the resulting mission and request digest. Human approval must use a
   channel unavailable to the requesting agent; a caller-supplied label or an
   agent-authored GitHub comment does not establish human identity.
3. Only in a separately authorized disposable acceptance environment, pass the
   envelope's `mission` object to the existing Factory operator path. The envelope
   itself is **not** a Factory mission and cannot be supplied directly to
   `factory.py submit --mission`.
4. Use existing inspect/export/verify-evidence operations and retain the original
   request beside its evidence. Execution success and human review acceptance
   remain separate states. Do not automatically approve a successful fixture.

This change grants no authority for a live hub, repository merge, deployment,
external message, cloud spend, test runner or GPU rendering. It deliberately
does not add a generic shell capability. A later repository test runner requires
reviewed code and real execution isolation: test commands execute repository
code, and a separate folder or a hash alone is not an OS sandbox or proof of trust.

## Verification

```sh
python -m unittest discover -s tests -p test_agent_requests.py -v
python -m unittest discover -s tests -v
python -m compileall -q homelab lab.py factory.py agent_request.py
```

The integration test uses the actual existing Factory, SQLite queue and fixed
typed worker executor with synthetic state. It demonstrates preparation,
duplicate reuse, conflicting-ID rejection, two verified jobs, and evidence
integrity. It does not demonstrate a cloud connector, HTTP transport, a Windows
run, an always-running service, or a physical three-machine handoff. Use the
separate existing Factory demo for genuine loopback HTTP acceptance.
