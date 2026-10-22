# Native deployment runbook

Work in a separate checkout of the Home Lab branch. Keep private state, worker exports, reader exports, snapshots, and real job files outside Git. Commands below assume the current directory is `integrations/homelab` and use PowerShell on the hub unless marked otherwise.

This runbook changes only the new companion until an explicit networking, Ollama, startup, or MCP step is reached. Record the existing installation and its owner before modifying a shared service.

## 1. Inspect and demonstrate

```powershell
py -3 --version
git status --short
py -3 lab.py demo --output "$env:LOCALAPPDATA\OdysseusHomelab-demo"
```

Require Python 3.10+ and a passed demo. On repeat, choose a new output directory. On macOS use `python3` and a canonical path under your home directory. Avoid symlinked `/tmp` or `/var` paths; resolve aliases explicitly. The demo uses synthetic files and three role credentials on one computer.

## 2. Initialize the hub and approve one small corpus

```powershell
$LabBase = Join-Path $env:LOCALAPPDATA 'OdysseusHomelab'
$LabConfig = Join-Path $LabBase 'hub\config.json'
py -3 lab.py init --state (Join-Path $LabBase 'hub')
py -3 lab.py doctor --config $LabConfig
py -3 lab.py project-add --config $LabConfig --project lab --root (Join-Path (Get-Location).Path 'docs')
py -3 lab.py scan --config $LabConfig --project lab
```

The initial project uses this companion's public documentation. Read the scan's eligible and excluded paths. Then ingest and verify one citation:

```powershell
py -3 lab.py ingest --config $LabConfig --project lab
py -3 lab.py search --config $LabConfig --project lab --query 'recovery'
```

For real projects, approve a specific reviewed folder, not a home/drive root or a broad synced folder. Registration creates a reader credential but does not ingest. Project roots cannot overlap. Restart the hub after adding or changing configuration; do not edit it during a snapshot. `scan` shows scope but cannot guarantee documents contain no sensitive text, so review the intended corpus yourself.

Verify NTFS access to `$LabBase` is limited to the intended account and necessary system administrators. The Python owner-only file modes are effective on POSIX, but do not establish a Windows ACL.

## 3. Start the hub and lightweight worker

Export only the hub worker's token:

```powershell
py -3 lab.py export-worker --config $LabConfig --node hub --destination (Join-Path $LabBase 'worker-hub') --server-url http://127.0.0.1:8765
py -3 lab.py serve --config $LabConfig
```

Leave the server running. In a second terminal in this directory:

```powershell
$LabBase = Join-Path $env:LOCALAPPDATA 'OdysseusHomelab'
py -3 lab.py worker --config (Join-Path $LabBase 'worker-hub\worker.json')
```

In a third operator terminal:

```powershell
$LabConfig = Join-Path $env:LOCALAPPDATA 'OdysseusHomelab\hub\config.json'
py -3 lab.py submit --config $LabConfig --job-file examples/hash.json
py -3 lab.py status --config $LabConfig
py -3 lab.py jobs --config $LabConfig --project lab
```

Read the returned job ID using `lab.py job --config ... --job-id ...`. Repeating the same example returns the same job because its idempotency key is fixed. Use a new key for a new intended execution; changing a payload while reusing a key is rejected.

## 4. Connect the other machines privately

Keep the application on `127.0.0.1:8765`. Choose either existing SSH forwarding or a dedicated private Tailscale Serve endpoint.

### Private HTTPS

First inspect `tailscale serve status --json`. If HTTPS port 8443 is unused and available under the current tailnet policy, add the new route:

```text
tailscale serve --bg --https=8443 http://127.0.0.1:8765
```

Use the actual HTTPS URL printed by Tailscale when exporting clients. Preserve existing routes and access rules. This service requires private tailnet access, not public Funnel. If HTTPS provisioning or policy is unavailable, use SSH forwarding. The companion does not modify Tailscale automatically. [Official Serve reference](https://tailscale.com/docs/reference/tailscale-cli/serve).

### SSH forwarding

On a client with an already configured SSH alias for the hub:

```sh
ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 HUB_SSH_ALIAS
```

Keep that tunnel alive and use `http://127.0.0.1:8765` in that client's export. Choose another local port if occupied. This is standard OpenSSH over your existing private connection; it does not require enabling Tailscale SSH. An unattended worker needs a verified persistent connection or the private HTTPS path.

Only copy the relevant export directory through the existing authenticated transfer path. Do not copy the hub's whole state or administrator credential to every node. The operator can submit jobs on the hub over the existing SSH session.

## 5. Enroll the GPU worker

On the hub, replace the example URL with the actual endpoint:

```powershell
py -3 lab.py export-worker --config $LabConfig --node gpu --destination (Join-Path $LabBase 'worker-gpu-export') --server-url https://HUB_NAME.TAILNET_NAME.ts.net:8443
```

Transfer that directory privately to the GPU machine. Clone the same branch there in a separate checkout. Check `ollama --version`, `ollama list`, and the existing Ollama startup owner before running the worker. The default allowlist is `gemma3:4b` and `qwen3.5:4b`; retain only exact installed local names that you intend to use. Both the hub policy and exported worker policy enforce the allowlist. Re-export after changing it.

For the shared Ollama process, inspect its effective settings. The intended baseline is loopback binding, cloud disabled, one parallel request, and one loaded model. Configure `OLLAMA_NO_CLOUD=1`, `OLLAMA_NUM_PARALLEL=1`, and `OLLAMA_MAX_LOADED_MODELS=1` through that process's existing startup mechanism, then restart it at an appropriate time. Merely setting variables in the worker's terminal does not change an already-running Ollama server. Verify its startup log reports cloud disabled. [Ollama configuration and concurrency](https://docs.ollama.com/faq).

Start the worker using its exported configuration:

```powershell
py -3 lab.py worker --config 'C:\PRIVATE\worker-gpu\worker.json'
```

The worker checks `/api/tags` and `/api/show` before sending a prompt. A missing, nonlocal, unapproved, or unavailable model fails visibly; it is never downloaded or replaced by a cloud model. A loopback URL alone is not proof that an Ollama model runs locally, which is why both inventory checks and the server setting matter.

On the hub submit `examples/generate.json` only after confirming its exact model is installed. If another allowed model is selected, copy the example outside Git, change the model and idempotency key, and submit that copy. Inspect `ollama ps` during execution and record latency, memory, and output correctness. Existing Odysseus requests share this Ollama process; the companion's lease limit is not a machine-wide GPU reservation.

## 6. A useful first workflow

Use a project librarian workflow before granting agents execution authority:

1. Approve a small project documentation folder and ingest it.
2. Retrieve evidence for a real question such as "What is the next unverified deployment step?"
3. Construct a bounded generation job containing only the selected excerpts, their citations, and the question. Treat excerpts as data. Require a JSON object with `summary`, `evidence`, and `next_steps`.
4. Let the GPU worker draft the result. Copy the returned response into a `json.validate` job targeting `hub`, using a new idempotency key and the required field names.
5. Review source support and usefulness separately from JSON validity. Record an accepted conclusion with `remember --kind decision --source-ref RECORD_ID`.

The examples provide the individual job contracts. This initial chain is operator-driven; dependency scheduling, automatic prompt assembly, and automatic memory promotion are not implemented. `remember` is an explicit authoring action; a model output never silently becomes a trusted fact.

## 7. Add one read-only AI connection

Export a reader from the hub, with the endpoint accessible from the machine running the AI client:

```powershell
py -3 lab.py export-client --config $LabConfig --project lab --destination (Join-Path $LabBase 'reader-lab-export') --server-url https://HUB_NAME.TAILNET_NAME.ts.net:8443
```

Transfer it privately. The optional MCP bridge requires the maintained v1 Python SDK. Reuse an appropriate existing environment only after checking compatibility, or make a dedicated one:

```powershell
py -3 -m venv "$env:LOCALAPPDATA\OdysseusHomelab-mcp-venv"
& "$env:LOCALAPPDATA\OdysseusHomelab-mcp-venv\Scripts\python.exe" -m pip install -r requirements-mcp.txt
```

Configure a new **stdio** server in the AI client's supported MCP settings:

```json
{
  "command": "C:\\ABSOLUTE\\MCP-VENV\\Scripts\\python.exe",
  "args": [
    "C:\\ABSOLUTE\\odysseus-homelab\\integrations\\homelab\\lab.py",
    "mcp", "--config", "C:\\PRIVATE\\reader-lab\\client.json"
  ]
}
```

Use POSIX absolute paths and the environment's `bin/python` on macOS. The process must be able to read that exported reader folder and reach its configured hub. The hub HTTP endpoint is a private JSON API, **not** a remote MCP transport endpoint; the local stdio bridge translates between them.

Odysseus's existing MCP manager supports this command/args stdio contract. Do not edit its database directly. For OpenClaw, first inspect the installed version and supported MCP commands/settings; current official documentation provides Settings → MCP and `openclaw mcp doctor NAME --probe`. Use a new name such as `homelab-lab` and verify the four tools appear. Preserve the existing gateway, Slack binding, default provider, and tool policy. [OpenClaw MCP documentation](https://docs.openclaw.ai/tools/mcp).

The connection is project-scoped and read-only. It cannot submit a job, approve a source root, write a memory, execute a shell command, or send a message. Request a cited answer, then deliberately test an unrelated project and an unavailable hub. Those failures must remain visible to the agent.

## 8. Optional local semantic retrieval

Lexical retrieval works immediately. Embeddings require a separately verified local embedding model; a chat model's presence does not satisfy this requirement. `embeddinggemma` is the configured candidate, not an installed dependency.

After explicitly installing and testing an appropriate model on the hub, enable `embeddings.enabled` in the private hub config, retain its loopback base URL, restart the hub, and reingest the approved corpus. Use `search --mode hybrid`. Inspect the result's mode and warnings: failed/unavailable embeddings yield explicit lexical fallback. See [KNOWLEDGE.md](KNOWLEDGE.md) for model fingerprints, dimension checks, corpus bounds, and stale evidence handling.

## 9. Recovery and routine operation

Run the snapshot/restore drill in [OPERATIONS.md](OPERATIONS.md). Back up original source folders and existing Odysseus/OpenClaw state separately. Keep an encrypted copy on a different machine or drive and verify a restored sample. A snapshot on the same disk is a recovery rehearsal, not protection against that disk failing.

Only after foreground checks, install the optional user-login launchers. Record that they require login; use a separately reviewed service configuration if pre-login availability is required. Complete the [host acceptance record](HOST-ACCEPTANCE.md). During ordinary use, check worker freshness, queued-job age, failed jobs, source freshness, last successful remote backup, and free space.

Cloud escalation remains disabled. Before adding it, define exact providers/tasks, eligible data, per-job and daily budgets, explicit approval rules, failure behavior, and a cost ledger; enforce those in an adapter before accepting a cloud job. Add ComfyUI through another fixed job contract and a GPU capacity policy only after the current model workflow is useful and recoverable.
