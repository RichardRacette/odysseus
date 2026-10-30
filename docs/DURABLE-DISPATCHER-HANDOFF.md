# Durable Dispatcher — native owner and Fae handoff

Assignment: `20261030-durable-dispatcher`. Owner authorization: PROJECT DURABLE DISPATCHER in the October 30 conversation. Scope: get as close as possible to one unattended, interruption-tolerant mission with a verified receipt returned to RichieOS, without waiting for ZBook Linux.

## Source and ownership

The published Factory baseline is `b2691dc31ed0a3a52ac8d4ebdd6aea79eff61d28`, draft PR #9 in `RichardRacette/odysseus`. A scoped source snapshot of 53 companion/instruction files was fetched through the GitHub connector and verified against the exact original Git blob IDs. The local snapshot commit is not represented as the original upstream commit.

RichieOS records identify native checkpoints `29ebb2f4cae12cc183e608ed83b590e1f7fd593b` and `f3cf35590d6516236c18bb9aa926c062d1dd98f1`, plus separately preserved app recovery work. The newer Factory source was not available through remote Git. Its saved dependency-floor packet was inspected and confirms preservation requirements; it does not contain the full newer source. No native checkout was modified or reset.

Incoming native owner: the existing Codex Factory owner, after current branch/HEAD/status/worktree/lock/ownership inspection. This candidate primarily adds new files and includes small queue/Factory/hub enforcement hooks required by independent review. Compare those exact changes against the newer native implementation, then integrate deliberately in an isolated branch. Never replace the native checkout with the older published tree. Do not start the root Odysseus web application to exercise this dependency-free companion.

Fae remains the canonical project/assignment writer. This lane writes only its permitted control-plane report; the Odysseus local agent remains on probation. No new local-agent Git credentials or network authority are granted.

## Implemented surface

- `integrations/homelab/homelab/durable_dispatch.py`: scoped durable execution permits, local coordinator locks, progression, revocation, immutable receipt publication.
- `integrations/homelab/homelab/dispatch_receipts.py`: independent fixed-output checks and sanitized reports.
- `integrations/homelab/homelab/dispatch_policy.py` plus narrow queue/Factory/hub hooks: enforce scope before claim/acceptance, including disarmed restore and committed-but-unlinked jobs.
- `integrations/homelab/dispatch.py`: explicit operator CLI.
- `integrations/homelab/scripts/verify-durable-dispatch.py`: disposable real-process interruption proof.
- Focused regression tests and `docs/DURABLE-DISPATCHER.md` operator instructions.

The existing worker, root application, requirements and workflows are preserved. Existing Factory and hub constructors pass project registration to the queue; queue hooks preserve original behavior for installations without dispatcher permits and for explicit manual Factory missions. The coordinator initially admits hashing/JSON validation only. Newer native test-runner capabilities and PS5/ComfyUI adapters remain separate future work.

## Next native slice

1. Inspect and preserve the current native Factory checkpoint, existing hub identity, state paths and active owners.
2. Compare/apply this additive candidate on the newer checkpoint. Run the complete companion suite, then the disposable proof in a new private directory; retain native receipts.
3. Verify the actual Windows process behavior and existing private worker transport. Do not initialize over live state, copy a changing live database, or run duplicate hubs.
4. After source compatibility, backup and native proof are accepted, choose the host and install deliberate supervision. Use the existing Windows machine if suitable; ZBook Linux is not a prerequisite.
5. Verify startup, interruption recovery and an overnight run on the actual host. Fae records only the evidence returned.

## Completion boundary

Managed Linux process tests and proof are distinct from physical Windows/macOS acceptance, home-service installation, a 24-hour run, model inference, public release, and final product approval. A generated report is an outbox until delivered through the permitted report branch. The canonical records must not claim home deployment from a cloud receipt.

## Rollback and protected work

Stop the new coordinator and preserve receipts/state. No existing service configuration has to be rolled back by this source-only candidate. If it is later installed, revoke/cancel unfinished dispatcher work before reverting queue enforcement; stopping only the coordinator does not retract work already admitted. Preserve all local branches, app recovery candidates, existing jobs/backups, household DNS, Tailscale, Open WebUI, ComfyUI, PS5LM and family data. Do not merge the older Factory stack, change dependencies, or bypass its independent release gates merely to install the dispatcher.
