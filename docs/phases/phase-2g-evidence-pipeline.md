# Phase 2G — Task 2 Evidence Pipeline Finalization

## Result

Phase 2G implements the deterministic test, typed downstream of Task 2. It does not
re-open Phase 2F semantic model evaluation and does not call a real provider.
The current semantic default is Phase 2E one-shot; the Phase 2F staged path is
retained as explicit experimental code only.

## Pipeline

```text
RecallResult
  + ApplicabilityResult
  → CandidateEvidence
  → MISMATCH rejection / uncertainty retention
  → conflict assessment
  → bounded deterministic selection
  → deduplication + light aspect diversity
  → bounded extractive distillation
  → EvidenceSufficiency
  → EvidenceBundle
```

`CandidateEvidence` preserves memory/revision identity, original recall rank
and score breakdown, applicability decision/status/reasons, provenance IDs,
aspect hints, conflict status and selection trace. It is a request-local view,
not a persistent memory record.

## Conflict baseline

Cross-memory conflict first passes through a precision-first
`ConflictDetector`. It automatically identifies only the explicit structured
case where two current memories use the same scope axis/value with opposite
`REQUIRES`/`EXCLUDES` operators. Less explicit cases remain `NO_CONFLICT` and
may be supplied as audited `ConflictAnnotation` input; the baseline never
mistakes similarity for contradiction. The deterministic resolver supports
`NO_CONFLICT`, `RESOLVED` and `UNRESOLVED`. A resolved losing candidate is
omitted from the selected evidence but retained in the
`rejected_or_omitted` trace with its revision and provenance identity.

## Selection, distillation and sufficiency

Selection is rank-stable, removes duplicate current identity/content signatures,
and uses a deterministic round-robin over bounded metadata aspects. Distillation
normalizes whitespace and truncates each item and the complete bundle to
configured character budgets. It never writes a final answer. Sufficiency is a
typed `SUFFICIENT` / `PARTIAL` / `INSUFFICIENT` result with missing aspects,
uncertainty and unresolved-conflict reason codes.

## Entrypoints and safety

`DeterministicEvidencePipeline.run_typed` consumes typed applicability output and
requires no provider. `run_natural_language` composes the existing
`SemanticRetrievalPipeline`; a configured provider remains optional at the
convenience boundary and failures are not converted to UNKNOWN, BM25-only, fake
or oracle output. All Phase 2G objects are derived/read-only. Task 1 SQLite,
revisions, semantic metadata and provenance are unchanged.

## Acceptance

deterministic test coverage includes mismatch gating, UNKNOWN/AMBIGUOUS retention,
explicit conflict resolution and uncertainty, dedup/diversity, bounded output,
traceable distillation, sufficiency states, deterministic serialization and the
typed no-provider path. The acceptance runner is
`code/scripts/task2_evidence_acceptance.py`; unit coverage is in
`code/tests/test_evidence_pipeline.py`.

## Known limitations

The conflict input is explicit rather than a full semantic contradiction
resolver. Diversity uses metadata aspect hints, and the distiller is extractive
rather than query-generative. No final response generation, Task 3 integration,
or real-LLM reranker is included.
