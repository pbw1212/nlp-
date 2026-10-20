# ADR-0018 — Task 2 Terminates in a Bounded, Traceable EvidenceBundle

Status: Implemented in Phase 2G

## Context

Recall and applicability produce request-local candidates, not an answer. A
downstream agent needs a bounded evidence view that preserves the distinction
between retrieval score, applicability uncertainty, provenance and conflict.
Writing this view into Task 1 would create a second canonical memory schema and
would make a lossy query-specific derivation durable.

## Decision

Phase 2G adds a provider-independent derived pipeline:

```text
RecallResult + ApplicabilityResult
  → CandidateEvidence
  → applicability gate
  → explicit conflict assessment
  → deterministic selection/dedup/diversity
  → bounded extractive DistilledEvidence
  → EvidenceSufficiency
  → EvidenceBundle
```

`MISMATCH` candidates are rejected. `MATCH` candidates are eligible, while
`UNKNOWN` and `AMBIGUOUS` are retained with uncertainty. The first conflict
baseline automatically detects only explicit structured opposite operators
(`REQUIRES` versus `EXCLUDES`) on the same axis/value, and also accepts audited
`ConflictAnnotation` objects. It supports `NO_CONFLICT`, `RESOLVED` and
`UNRESOLVED`; a resolved loser is omitted from
answer-facing selection but its identity and provenance trace remain in the
bundle omission trace. It does not infer conflicts from lexical similarity.

`EvidenceBundle` has schema identity, query/request identity, selected evidence,
applicability/conflict traces, warnings, sufficiency status and explicit item /
character budgets. Deterministic extraction is the deterministic test baseline; an LLM
distiller, if added later, is a replaceable provider and never the only path.

Two entry points are normative:

- typed input: accepts `ApplicabilityResult` and never calls a provider;
- natural-language convenience: composes the existing recall and one-shot
  semantic pipeline, with provider failures remaining typed and visible.

Neither entry point writes `MemoryRevision`, semantic metadata, provenance or
SQLite. The bundle is derived, lossy and rebuildable, and it does not generate
a final answer or recommendation.

## Consequences

Task 2 can be evaluated deterministic test for applicability contamination, conflict
handling, selection precision, boundedness and traceability. A future richer
resolver or reranker can implement the same DTO boundary without changing Task
1. The first deterministic conflict and diversity policies are deliberately
conservative; they are engineering baselines rather than research claims.

