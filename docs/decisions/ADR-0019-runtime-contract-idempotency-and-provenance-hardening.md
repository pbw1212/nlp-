# ADR-0019 — Runtime Contract, Idempotency and Provenance Hardening

Status: Implemented in Phase 4C

## Context

The frozen Task 1–3 algorithms and acceptance baselines already meet their
declared metrics. The integration audit nevertheless found defects at module
boundaries: the prompt adapter read a field that does not exist on
`DistilledEvidence`, automatic promotion used the explicit-memory API in a
mode that only buffered input, `NOOP` replay lost the selected memory identity,
conversation message de-duplication performed its read and write in separate
transactions, and several public configuration/context inputs admitted values
outside their mathematical or storage contracts.

These are contract and transaction defects, not evidence that the retrieval,
applicability or task-state algorithms should be retuned. Changing validated
ranking thresholds without a new benchmark would add regression risk without
an observed benefit.

## Decision

1. **Preserve the frozen semantic baselines.** Task 2 scoring, applicability,
   conflict and bounded-evidence policies remain unchanged.
2. **Make DTO boundaries executable contracts.** Prompt composition consumes
   `DistilledEvidence.text`, escapes untrusted context, places warnings before
   the current request, and rejects blank turn input. Memory algorithm
   parameters are validated at configuration construction.
3. **Make promotion provenance explicit.** The default-off automatic promotion
   gate writes accepted candidates through the canonical lifecycle pipeline
   with `INFERRED` authority and stable source/session/message identifiers. It
   never impersonates an explicit user instruction.
4. **Make replay identity complete.** A `NOOP` ingestion event stores the
   matched `memory_id`; replay returns the same domain result without calling
   the provider again. Legacy `NOOP` rows without that metadata remain
   readable through a compatibility sentinel.
5. **Make conversation de-duplication atomic.** Identity lookup, conflict
   detection and insert execute under one `BEGIN IMMEDIATE` transaction.
   Identical retries return the existing row; the same identity with different
   content fails explicitly. SQLite documents that `BEGIN IMMEDIATE` starts a
   write transaction immediately and prevents another connection from jumping
   between the read and write ([SQLite transaction semantics](https://www.sqlite.org/lang_transaction.html),
   [SQLite isolation](https://sqlite.org/isolation.html)).
6. **Retry only failures that can plausibly change without modifying the
   request.** Transport failures plus HTTP 408, 429, 500, 502, 503 and 504 retain
   bounded retry; other 4xx responses fail once. This follows the HTTP class
   distinction between client and server errors and avoids repeating invalid or
   unauthorized requests ([RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html)).
7. **Enforce bounded decay.** Negative elapsed wall time is treated as zero and
   computed strength is clamped to `[0, s0]`, preserving the storage invariant
   and monotone non-increase even after clock rollback or imported future
   timestamps.

## Consequences

The change repairs integration paths without altering validated retrieval or
task-state semantics. New regression tests cover DTO compatibility, prompt
trust boundaries, inferred provenance, replay without provider access,
concurrent message identity, strict context budgets, configuration domains,
clock rollback and chained confirmation state.

Automatic promotion remains **experimental and disabled by default**. This ADR
only makes the explicitly enabled path correct and traceable; it does not claim
that the rule-based gate is a production-quality cognitive model. Task 4,
long-session semantic compaction and external dynamic-fact tools remain
deferred until each has its own dataset, failure policy and acceptance gate.
