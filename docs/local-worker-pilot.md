# Artifact-only local worker pilot

The standalone command inspects one bounded synthetic packet using one installed
Ollama model. It does not start Odysseus, initialize its database, discover hosts,
run tools, execute generated code, download models or fall back to a cloud model.
This experiment is independent of the research recovery PR stack.

Python 3.11+ and the existing `httpx` dependency are required. Supply the separately
distributed hcf/1 contract directory; its task and result schemas are checked
against exact SHA-256 digests before use. The launch kit and operator packets are
not published with this change. The validator implements only the keywords in
those pinned schemas and rejects unsupported schemas without resolving references.

Reuse `OLLAMA_BASE_URL` (or legacy `OLLAMA_URL`) and an explicitly configured
absolute `ODYSSEUS_DATA_DIR`. The latter owns one `artifact-worker` queue for this
operator/node. Configure it once outside the source checkout, and use it for every
invocation. No per-job state directory or endpoint override is accepted. Changing
the operator configuration, using a different OS account, or erasing journals can
bypass local exclusion; this CLI is not a hostile-user machine sandbox. Reserve
the machine's inference slot while running the pilot and avoid other model jobs.

```sh
python3 -B scripts/odysseus-local-worker --task TASK.json --contract-dir CONTRACT_DIR --node mac --model gemma3:4b
```

Use `--node pc` on Windows/Linux; `mac` requires Darwin. Choose a model already
installed on that machine. This pilot admits observed Gemma 3 and Qwen 3.5 families
with completion capability, downloaded size and a full local digest. The task
cannot choose a model, endpoint, executable or tools. A local URL alone is not
locality proof. Remote descriptors/cloud tags are refused, proxies and redirects
are disabled, and no credential is read. Running-server egress configuration is
still unverified, so all non-synthetic classifications are explicitly BLOCKED.
Setting `OLLAMA_NO_CLOUD` in this client would not establish server configuration.

Every task is bounded before and during work: 64 KiB input, four sources / 8 KiB
combined source content, six claims, one generation request, context at most 4096,
output at most 768, and 120 seconds including metadata probes. A conservative
UTF-8-byte prompt bound reserves output and template space for the admitted model
families. The `/api/show` metadata response has its own 256 KiB cap because it
includes license text; generated HTTP bodies are capped at 32 KiB and wire results
at 16 KiB. Schema-constrained decoding helps shape output; all results are still
validated independently and require review against source text.

Provider `done`, `done_reason`, input/output counts and elapsed time are retained.
`length` becomes TRUNCATED; absent/unknown completion metadata stays UNKNOWN;
known token overruns, invented references, tool calls and malformed output are
rejected. COMPLETED means protocol-complete, not semantically approved. Wrapper
identity, model digest, timing and status are never taken from model-generated
JSON. Synthetic candidate output is retained in the local attempt journal as
untrusted review material, not diagnostics or instructions.

An exclusive, fsynced reservation precedes inference. Identical task bytes/node
reuse the hash-bound prior result without another call. A result becomes visible
only after its runtime journal is durable; cache reads verify reservation/attempt
and result-hash binding. One configured queue excludes concurrent jobs. Timeout,
cancellation, transport overflow and interrupted persistence retain conservative
ambiguity across restarts. Closing the HTTP request does not prove server-side
generation stopped. No automatic retry or stale-lock clearing command exists.
Do not rotate the data root to retry. Preserve journals and establish generation
cessation before a separately reviewed new job. Local exclusion is not an
exactly-once guarantee across operators or filesystems; fsync/rename does not
promise recovery from arbitrary storage failure.

Run offline checks with the supplied unchanged contract:

```sh
HCF_CONTRACT_DIR=CONTRACT_DIR python3 -B -m unittest discover -s tests/local_worker
```

The shared contract examples are a separate 20-test set, not hardware or model
evidence. Worker tests exercise actual loopback HTTP and the production CLI,
schema/size/identity failures, cloud-descriptor and redirect refusal, timeout,
cancellation, concurrency, restart deduplication, journal failures, diagnostic
redaction, old-Python refusal and provider token/completion metadata.

Observed Windows PC pilot: four total calls, no retries of a task ID. The original
two tasks reached provider stop but failed output validation. Two separately
identified confirmation tasks reused the sources/claims/rubric and unchanged
token budgets after adding schema-constrained decoding; both were complete.
Source review still found an injection-text paraphrase error, despite correct
claim verdicts. The local handoff retains every attempt and its correction.
Mac execution, two-node speedup, energy and frontier-credit savings are unmeasured.

Adoption: experimental synthetic artifact review only. The research application's
legacy string-only model calls still do not preserve finish metadata; this change
does not certify that application's generated reports as complete.

Rollback: stop invoking this command. Preserve its data directory and journals.
No service or startup configuration is installed.

Primary API references: [chat response and format](https://docs.ollama.com/api/chat)
and [server locality, cloud configuration and concurrency](https://docs.ollama.com/faq).
