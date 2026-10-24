# Offline assistant request preflight

## Intent and scope

Prepare a narrow bridge from external assistant handoffs to the existing Project Factory. The operator requested preparation of multi-assistant coordination and local-device readiness. This slice compiles untrusted requests into a single fixed, existing synthetic mission. It does not connect vendors, dispatch work, add a scheduler, or grant approval.

## Approach

Three routes were considered: direct cloud-to-shell access, a new general broker, or an offline compiler feeding the existing Factory. The compiler is the implementation choice because the Factory already owns durable admission, idempotency, leases and evidence, while newer local work needs reconciliation before runtime integration. The user authorized proceeding with the shared-record/local-worker plan; this is its bounded preparatory slice.

## Contract

Accept only schema_version=1, request_id (ASCII identifier, at most 48 characters), requested_by (fae, duet, grok, operator), and capability=fixture.audit.v1. Attribution is unverified and confers no authority. Reject extra keys, duplicate JSON keys, nonfinite numbers, invalid types, malformed JSON and input over 4096 bytes.

The capability maps to the existing project-factory-release-audit.json template. Pin its full SHA-256 in source and verify the validate.json input bytes. Preserve its original fixture revision; do not confuse that revision with the tested Factory implementation revision. No requester-controlled commands, environment, paths, source URLs, node targets or approval fields.

Output a deterministic preparation envelope and validated Factory mission. Mission identity is agent-<request_id>; the canonical request digest is bound into its input references. Existing Factory submission then reuses identical requests and rejects changed content under the same identity. The compiler itself owns no persistent job state.

## Boundaries

- No subprocess execution, network, third-party dependency, daemon, database writes, or changes to existing runtime modules.
- CLI reads one explicit request file and creates one new output file, refusing overwrite and symlink output.
- Prepared is not submitted, authorized, executed, or accepted. Hashes establish consistency, not authorship.
- Source-template pins are a drift check; a same-user process able to rewrite compiler and pins remains outside this boundary.
- A private folder is not an operating-system sandbox. A future repository test runner must isolate execution of reviewed code separately.
- Existing read-only MCP remains read-only. There is no new consumer-agent API claim.

## Acceptance

Strict validation, deterministic compilation, pin-drift rejection, no-overwrite behavior, existing Factory duplicate/conflict semantics, full companion regression suite, and a disposable generated-mission execution where available. Physical hosts, remote connectors, Windows/Mac parity, live deployment and repository security gates remain separately pending.
