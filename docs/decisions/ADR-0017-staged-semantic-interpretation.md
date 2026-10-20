# ADR-0017 — Semantic Interpretation Separates Field Selection from Field-Conditioned Extraction

Status: Implemented as a gated Phase 2F experiment; not promoted by controlled DEV

> 本 ADR 记录的是受控实验决策，不是当前 Provider 的质量结论。当前模型和发布状态以
> `docs/project-progress.md` 与 `docs/architecture/` 为准。

## Context

Round-1-on-v2 showed that state classification conditional on the correct field
was perfect, while context unsupported activation and scope-axis selection were
the dominant errors. A one-shot field/value/rule prompt cannot attribute those
errors cleanly.

## Decision

Provide a derived, opt-in staged path:

```text
ContextFieldSelector → ContextFactInterpreter
ScopeFieldSelector → ScopeRuleInterpreter
```

The public `ContextInterpreter` and `MemoryScopeInterpreter` DTOs remain stable;
`staged=True` enables the new path while the historical one-shot path remains
available for regression comparison. Selectors emit only closed registry field
IDs with evidence. Conditioned interpreters cannot change the selected field.

No independent LLM verifier is added yet. It is only justified if DEV evidence
shows selector evidence is frequently unsupported after this split.

## Metrics

Canonical context safety uses `Unsupported-KNOWN Rate` (unsupported predicted
KNOWN facts divided by all predicted KNOWN facts). The old false-known rate is
retained as a historical annotation-opportunity diagnostic. Sparse scope uses
scope-axis precision/recall/F1 in addition to scope-state accuracy.

## Consequences

The staged path improves failure attribution but increases request count and is
not production-promoted by architecture alone. All interpretation remains
request-local, derived, read-only, and provider-injected. Real DEV evaluation
must pass before holdout results are opened.

## Controlled DEV outcome (2026-08-14)

The same-model Groq `openai/gpt-oss-20b` comparison on
`semantic_calibration_dev_v1` did not support the staged path:

| path | Context F1 | Unsupported-KNOWN | Scope-axis F1 | Scope-state | D | requests | total tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| one-shot | 0.7273 | 0.3750 | 0.6154 | 0.4444 | 0.2000 | 44 | 76592 |
| staged | 0.6774 | 0.3214 | 0.5957 | 0.4242 | 0.1000 | 109 | 111263 |

The staged run also recorded 5/20 scope validation failures. Therefore the
current operational baseline is the one-shot interpreter (`staged=False`);
staged selectors remain an explicit experimental path only. This result does
not claim that 20B is unsupported, that one-shot is production-optimal, or
that field-first decomposition is permanently invalid. It records only that
this implementation was not supported by this controlled backend/dataset
comparison. No holdout, Frozen-v2, Hard-v2, or Phase 2F promotion tag was
created.
