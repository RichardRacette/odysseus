# Execute the Home Lab rollout

This is an implementation and deployment handoff for a local coding agent with access to the actual home-lab machines. The code is in `integrations/homelab`. Start by executing the checks below; the package is already implemented, not a request to brainstorm a replacement.

## Repository and boundaries

- Work from the `codex/homelab-foundation` branch of this repository in an isolated checkout. Inspect the branch and working tree before making changes. Keep any existing uncommitted work intact.
- Read [README.md](../README.md), [RUNBOOK.md](RUNBOOK.md), [OPERATIONS.md](OPERATIONS.md), and [VERIFICATION.md](VERIFICATION.md).
- Use the user's existing machine inventory and SSH aliases after verifying identity. Roles are `hub`, `gpu`, and optional `mac`; do not infer a hostname from a role.
- Preserve the existing Odysseus/OpenClaw installations, app authentication, personal memory, Chroma/RAG data, Slack bindings, model selection, and credentials. Add the companion beside them. Do not reset a repository or merge another feature branch.
- Keep private data and deployment receipts outside this public checkout. Source ingestion requires a specifically approved folder. Start with this companion's public docs.
- Do not send messages, expose a public service, incur cloud charges, download models, register a public-repository self-hosted CI runner, or rewrite network policy as an incidental setup step.

## Execute in order

1. Inventory Python/OS, existing processes and startup owners, free disk, private connectivity, local model names, and current source-root choices. Record actual observations, not remembered state.
2. Run the core suite and synthetic demo on the proposed hub. Resolve failures before deploying. Keep its `DEMO-RESULT.json` locally.
3. Initialize new private hub state and approve the public documentation corpus. Scan, ingest, search, and verify a returned hash/line span against the file.
4. Start the loopback hub and exported lightweight worker in the foreground. Submit the fixed hash example and read its result through authenticated HTTP.
5. Use the existing private HTTPS or SSH path. Add only a new dedicated Serve route when the exact port and access scope are appropriate; preserve existing routes. Verify credentials never traverse unencrypted non-loopback HTTP.
6. Export only the GPU worker credential, transfer privately, and run that worker on the verified GPU machine. Inspect the actual Ollama startup settings and local inventory. Configure shared-process concurrency/cloud-off only through the established startup owner, preserving unrelated settings. Verify after restart.
7. Execute one small real model job and one separate shape validation job. Record model, prompt, correctness, latency, and memory. Do not equate parseable JSON with a correct answer.
8. Export one project reader and add the read-only stdio MCP bridge through the client's supported configuration. Verify tool discovery and one cited answer. Do not bypass Odysseus's scoped APIs by modifying its private database or files.
9. Rehearse snapshot verification and restore into a new directory. Reapprove roots, export fresh credentials, and recover a known job and citation. Keep one authoritative hub. Independently back up the source corpus.
10. Add the optional user-login launchers after foreground checks. Verify the actual login/reboot/sleep behavior required by the user, plus an unavailable worker and backup destination. Record remaining failures explicitly.

Use [HOST-ACCEPTANCE.md](HOST-ACCEPTANCE.md) as the private receipt template. Continue through reversible steps already authorized by the user's Home Lab request. If a step truly requires a missing credential, unavailable machine, or consequential choice, finish the independent work first and identify that exact blocker; do not ask what the package is for.

## Completion criteria

The user can submit a bounded job, see which machine handled it, retrieve a project answer with current source citations, survive a coordinator restart, and restore verified state with fresh credentials. Report these outcomes separately from outstanding optional work. Do not claim unattended reliability, real-model performance, encrypted off-machine backups, or physical three-node success without the corresponding host receipt.

If a host-specific code fix is necessary, add a regression for the observed defect, change only the companion, rerun the relevant checks, and preserve the fix on its development branch. Do not merge or declare production rollout complete merely because the synthetic test passes.
