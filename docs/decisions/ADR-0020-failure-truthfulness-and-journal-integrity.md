# ADR-0020 — Failure Truthfulness and Journal Integrity

Status: Implemented in Phase 4D

## Context

Phase 4C repaired the main integration contracts, but a repository-wide failure
audit found several boundary paths that could still make an unsuccessful or
corrupt operation look harmless: explicit memory apply returned `mem_unknown`
when no durable identity was produced; automatic promotion converted every
exception into an empty list; a damaged tool receipt replaced invalid JSON with
an invented empty object; a pending action could create a parent run whose root
turn was `unknown_turn`; and broad exception handlers sometimes exposed raw
exception text or misclassified storage failures as missing resources.

These behaviors violate the stronger product requirement: an unknown or failed
outcome must remain unknown or failed. It must never be represented as success,
absence of work, or synthesized data.

## Decision

1. **Fail closed at durable boundaries.** Empty lifecycle results, missing
   parent rows, invalid receipt JSON, unknown update targets and identity
   collisions raise or return a typed integrity failure. No placeholder
   identity or empty-argument substitution is permitted.
2. **Use complete replay identity.** Task events compare task, source kind,
   content, structured payload, source reference, turn and tool-call identity.
   Memory ingestion additionally compares session and message identity. Legacy
   Task 3 tool rows are deterministically backfilled from their already stored
   canonical content before strict replay comparison.
3. **Make success and failure payloads mutually exclusive.** `ToolCallResult`
   and journal terminal receipts require successful content to use
   `result_content`, while failures require `error_message`. A failed result
   cannot carry success-looking result content.
4. **Expose partial failure explicitly.** Automatic memory consolidation is
   non-blocking and default-on: ordinary turns first enter a durable pending
   ingestion buffer and an independent worker batches them after the configured
   turn/time threshold. A consolidation failure preserves the pending source and
   attaches a stable warning to the successful chat turn; the compatibility list
   API raises instead of returning an indistinguishable empty list. Explicit
   `/remember` remains the immediate, user-authorized write path.
5. **Log true metadata, not raw exception bodies.** Boundary logs contain a
   stable event name, exception class, stage identifiers and sanitized provider
   diagnostics such as HTTP status and provider request ID. Raw exception text,
   response bodies, prompts, authorization data and credentials are excluded.
   The CLI writes bounded rotating diagnostics to
   `~/.agent_data/execution/agent.log` (or the configured Agent home).
   This follows OWASP guidance to log sufficient event attributes while
   excluding access tokens, passwords, keys and other sensitive material
   ([OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)).
6. **Do not translate infrastructure failure into domain absence.** Task
   routing converts only repository `KeyError` into `TaskNotFoundError`; SQLite
   and other infrastructure exceptions retain a runtime failure classification
   and safe `error_type` metadata.
7. **Reject invalid parameters at construction.** Runtime step/observation
   limits, retrieval sizes, history limits, provider URL/model/retry/timeout
   values and tool DTO identities are validated before any external or durable
   operation.

SQLite mutations continue to use explicit transactions and verify that target
rows were actually updated. `BEGIN IMMEDIATE` is retained where a read-check
must be serialized with its write; SQLite documents that it starts the write
transaction immediately and can fail explicitly with `SQLITE_BUSY` when another
writer is active ([SQLite transaction semantics](https://www.sqlite.org/lang_transaction.html)).
Provider retry behavior remains governed by ADR-0019 and HTTP status semantics
([RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html)).

## Consequences

Some previously tolerated ambiguous calls now fail explicitly. In particular,
failed tools must populate `error_message`, pending actions require a registered
matching run, and replay IDs cannot be reused with different source metadata.
This is an intentional compatibility tightening: continuing with fabricated or
ambiguous state is more dangerous than requiring the caller to repair its
input or journal.

The change does not claim mathematical proof that the program contains no
defects. Its acceptance statement is limited to: all declared deterministic
gates pass, no known failure remains in the audited paths, and real paid-provider
semantic quality still requires execution against that provider.
