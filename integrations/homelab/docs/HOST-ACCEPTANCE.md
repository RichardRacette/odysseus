# Physical lab acceptance record

Copy this template to a private operator folder. Do not commit hostnames, private addresses, credentials, source documents, or private outputs to the public repository. A software test receipt is separate from this host record.

## Inventory

| Field | Recorded value |
| --- | --- |
| Date / operator | |
| Companion commit | |
| Hub / GPU / optional worker roles | |
| OS / Python executable and version for each | |
| State/config paths and owner | |
| Existing Odysseus / OpenClaw versions and startup owners | |
| Private hub endpoint / transport | |
| Approved project roots / reader locations | |
| Local model names and inventory evidence | |
| Backup destination / successful snapshot ID | |

## Acceptance evidence

Mark each item PASS, FAIL, or NOT RUN and retain the actual receipt locally.

| Check | Result | Evidence / issue |
| --- | --- | --- |
| Core tests and synthetic demonstration on hub | NOT RUN | |
| Hub loopback only; private remote endpoint reachable | NOT RUN | |
| Unauthorized client refused; reader cannot access another project | NOT RUN | |
| Hub and GPU workers each complete a targeted synthetic job | NOT RUN | |
| Installed Ollama models verified; cloud-off setting survives restart | NOT RUN | |
| GPU task completes with bounded output | NOT RUN | |
| Reader/MCP retrieval returns current file hash and source lines | NOT RUN | |
| Source edit invalidates old citation; reingestion refreshes it | NOT RUN | |
| GPU offline leaves pending work; reconnect produces one accepted terminal result | NOT RUN | |
| Hub restart preserves pending and completed jobs | NOT RUN | |
| User-login startup launches exactly one intended process | NOT RUN | |
| Reboot-before-login availability, if required | NOT RUN | |
| Sleep/wake and private-network reconnection | NOT RUN | |
| Snapshot verified; restored index and job recovered after root reapproval | NOT RUN | |
| Old credentials refused by recovered hub; fresh exports work | NOT RUN | |
| Original source folders independently backed up/restored | NOT RUN | |
| Off-machine encrypted backup and restored sample file | NOT RUN | |
| Git mirror recovery into a separate working copy | NOT RUN | |
| Availability samples over one ordinary day | NOT RUN | |

## Model usefulness evaluation

Use a small fixed set of representative, non-sensitive inputs. Record the model/digest, prompt version, expected properties, actual output, latency, peak GPU/host memory, and whether the task remains usable while normal Odysseus work is running. Include an unavailable/overloaded-model case. Count structural validity and factual/source correctness separately.

Choose an explicit success threshold before calling the workflow useful. For example, all outputs must be valid JSON when required, all factual claims must be supported by supplied sources, and ordinary interactive use must remain acceptable. A successful HTTP response alone does not satisfy this record.

Retries after lease expiry may repeat computation. Fencing controls which terminal result is accepted; it does not guarantee exactly-once execution. Future tasks with external side effects need their own idempotency contract.

## Promotion decision

- Authoritative hub and backup owner:
- Accepted availability window and recovery-loss target:
- Failed checks requiring action:
- Known limits accepted for this rollout:
- Date the workflow became part of ordinary use:

Keep the original hub stopped when promoting a restored copy. One active authority is required.
