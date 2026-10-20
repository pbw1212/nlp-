# ADR-0016 — Memory Applicability Profiles Use Sparse Evidence-Backed Scope Rules

Status: Accepted

## Context

Task 2 must distinguish a memory's preference target from the contextual axes
that qualify when the preference applies. A dense profile that enumerates every
registry field makes absence look like uncertainty and encourages unsupported
field invention.

## Decision

`MemoryApplicabilityProfile.rules` is a sparse set of evidence-backed
qualifiers:

- no rule means `ABSENT`: there is no evidence that the axis participates;
- `UNSPECIFIED` means the axis is positively relevant but its value/condition
  is unresolved, and therefore requires axis-relevance evidence;
- `EXPLICIT_CONSTRAINT` requires an explicit constraint;
- `EXPLICIT_GENERAL` requires explicit universal/general evidence.

Absence is neither `UNSPECIFIED` nor `EXPLICIT_GENERAL`. A preference target
such as “quiet rooms” does not itself create a location/noise scope axis.

This is Task 2 derived state. Task 1 schema and canonical memory truth are not
changed.

## ApplicabilityEngine mapping

The engine evaluates only axes present in the candidate profile. Context facts
without a candidate-side rule are ignored for that candidate. An explicit
`UNSPECIFIED` rule yields `UNKNOWN` / uncertainty retention; it is neither a
match nor a mismatch.

## Benchmark migration

The v1 fixtures remain immutable historical diagnostics. Their scope gold
contains cases where plain absence was annotated as `UNSPECIFIED`. Corrected
v2 fixtures use the sparse contract, retain evidence-backed conditional axes,
and record every change in migration manifests.

## Consequences

Sparse profiles reduce unsupported scope enumeration but make axis annotation
and evidence review more important. v2 is the candidate canonical semantic
evaluation contract; promotion thresholds remain unchanged until a later
decision.

Implementation mapping: `code/src/memory/retrieval/applicability.py`,
`benchmarks/task2/context_interpretation_*_v2/`, and
`code/scripts/task2_semantic_v2_rescore.py`.

Test mapping: sparse-profile tests in `code/tests/test_applicability.py` and v2
manifest/fixture tests in `code/tests/test_interpretation_evaluation.py`.
