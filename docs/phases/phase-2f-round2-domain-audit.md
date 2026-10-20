# Phase 2F Round-2 Pre-implementation Metric and Domain Audit

> **历史阶段记录**：本文是 2026-08 的预实施审计，使用当时保存的 Groq 预测快照；当前
> 实现状态以 `docs/project-progress.md` 和 `docs/research/codex-integration-gap-matrix-2026-08-31.md` 为准。

## Result

Round-2 staged semantic implementation is **blocked before prompt/architecture changes** by a scope-domain contract issue. The audit used the saved Groq round-1 canonical predictions under `/tmp/phase2f-checkpoint`; it made no provider requests and did not change Frozen/Hard gold.

## Metric integrity

`unsupported_field_abstention_rate` previously used the evaluator's generic `0/0 -> 0.0` convention. Frozen-v1 has zero empty-gold context cases, so its result is undefined and is now serialized/displayed as `N/A`; Hard-v1 has one eligible case and remains `1/1 = 1.0`.

All other reported Round-1 aggregate denominators are non-zero:

| Metric family | Frozen numerator / denominator | Hard numerator / denominator |
|---|---:|---:|
| Context field precision | 57 / 70 | 18 / 22 |
| Context field recall | 57 / 76 | 18 / 22 |
| Context state accuracy over union | 55 / 89 | 18 / 26 |
| Context false-known | 12 / 25 | 3 / 9 |
| Context given-field value | 46 / 57 | 16 / 18 |
| Context given-field state | 55 / 57 | 18 / 18 |
| Scope-axis precision | 24 / 33 | 6 / 10 |
| Scope-axis recall | 24 / 49 | 6 / 14 |
| Scope false invention | 9 / 33 | 4 / 10 |
| Scope state over gold/prediction union | 24 / 58 | 6 / 18 |
| Scope value over union | 19 / 58 | 4 / 18 |
| Scope operator over union | 24 / 58 | 6 / 18 |
| Scope given-field state | 24 / 24 | 6 / 6 |
| Scope given-field value | 19 / 24 | 4 / 6 |

Frozen per-state scope denominators are `EXPLICIT_CONSTRAINT=37`, `EXPLICIT_GENERAL=5`, `UNSPECIFIED=7`; Hard denominators are `8`, `2`, and `4` respectively. Their zero accuracies are therefore defined results, unlike Frozen unsupported abstention.

## Context field error matrix

Frozen false-known errors by field:

| Field | Count |
|---|---:|
| `TIME.day_type` | 3 |
| `LOCATION.environment` | 2 |
| `SCENARIO.transport_mode` | 2 |
| `TASK.meeting_mode` | 1 |
| `TIME.focus_window` | 1 |
| `LOCATION.area_noise` | 1 |
| `LOCATION.location` | 1 |
| `PERSON.travel_party` | 1 |

Frozen deterministic within-dimension field confusions were:

- `TASK.activity -> TASK.meeting_mode` (1)
- `LOCATION.destination -> LOCATION.location` (1)
- `PERSON.passengers -> PERSON.travel_party` (1)

Fields with imperfect Frozen precision or recall:

| Field | Gold | Pred | TP | Precision | Recall |
|---|---:|---:|---:|---:|---:|
| `CONSTRAINT.payment_source` | 8 | 6 | 6 | 1.000 | .750 |
| `CONSTRAINT.transport_mode` | 1 | 0 | 0 | N/A | 0 |
| `LOCATION.area_noise` | 0 | 1 | 0 | 0 | N/A |
| `LOCATION.destination` | 11 | 6 | 6 | 1.000 | .545 |
| `LOCATION.environment` | 2 | 4 | 2 | .500 | 1.000 |
| `LOCATION.location` | 0 | 1 | 0 | 0 | N/A |
| `PERSON.passengers` | 1 | 0 | 0 | N/A | 0 |
| `PERSON.travel_party` | 12 | 10 | 9 | .900 | .750 |
| `SCENARIO.fallback_activity` | 1 | 0 | 0 | N/A | 0 |
| `SCENARIO.transport_mode` | 3 | 4 | 2 | .500 | .667 |
| `TASK.activity` | 1 | 0 | 0 | N/A | 0 |
| `TASK.meal_type` | 1 | 2 | 1 | .500 | 1.000 |
| `TASK.meeting_mode` | 1 | 2 | 1 | .500 | 1.000 |
| `TASK.trip_type` | 5 | 3 | 3 | 1.000 | .600 |
| `TIME.day_type` | 0 | 3 | 0 | 0 | N/A |
| `TIME.focus_window` | 0 | 1 | 0 | 0 | N/A |
| `TIME.meeting_time` | 3 | 2 | 2 | 1.000 | .667 |
| `TIME.run_time` | 1 | 0 | 0 | N/A | 0 |

All other Frozen context fields had precision and recall `1.0` on their eligible occurrences.

Hard false-known fields were `PERSON.travel_party`, `SCENARIO.trip_type`, and `CONSTRAINT.seat` (one each). Unique within-dimension confusions were `PERSON.travel_party -> PERSON.companions`, `CONSTRAINT.vehicle_size -> CONSTRAINT.seat`, and `PERSON.passengers -> PERSON.travel_party`.

## Scope-axis error matrix

Frozen spurious axes account for all 9 false inventions:

| Predicted field | False-positive count |
|---|---:|
| `CONSTRAINT.accessibility` | 2 |
| `CONSTRAINT.noise` | 2 |
| `SCENARIO.transport_mode` | 2 |
| `CONSTRAINT.meeting_time` | 1 |
| `CONSTRAINT.seat` | 1 |
| `LOCATION.destination` | 1 |

Frozen missing gold axes account for 25 false negatives:

| Gold field | Missing count |
|---|---:|
| `LOCATION.area_noise` | 2 |
| `LOCATION.destination` | 2 |
| `PERSON.social_party` | 2 |
| `PERSON.travel_party` | 2 |
| `SCENARIO.activity` | 2 |
| `SCENARIO.transport_mode` | 2 |
| `TASK.trip_type` | 2 |
| `CONSTRAINT.weather` | 1 |
| `LOCATION.location` | 1 |
| `LOCATION.workplace` | 1 |
| `PERSON.meal_party` | 1 |
| `TASK.meal_type` | 1 |
| `TASK.shopping_category` | 1 |
| `TASK.shopping_context` | 1 |
| `TIME.day_type` | 1 |
| `TIME.departure` | 1 |
| `TIME.focus_window` | 1 |
| `TIME.meal_time` | 1 |

Hard-v1 has four spurious axes (`CONSTRAINT.hotel_budget`, one extra `CONSTRAINT.meeting_time`, `CONSTRAINT.noise`, `CONSTRAINT.transport_mode`) and eight missing axes (`LOCATION.area_noise` x2, `LOCATION.destination` x2, and one each of `PERSON.travel_party`, `TASK.shopping_category`, `TASK.trip_type`, `TIME.meeting_time`).

The checkpoint contains canonical final context values but not context evidence traces, so evidence-level Round-1 attribution cannot be reconstructed without new provider calls. Scope checkpoint entries retain revision evidence references. No evidence was invented for this audit.

## Scope-domain contract issue

Task 2B's intended distinction is:

- absent: the memory provides no evidence that a field is an applicability axis;
- `UNSPECIFIED`: the field is a relevant applicability axis, but evidence proves neither an explicit constraint nor explicit generality;
- `EXPLICIT_GENERAL`: direct evidence says the preference is general across that axis.

Several current gold rules instead use `UNSPECIFIED` for plain textual absence:

| Case | Memory meaning | Gold `UNSPECIFIED` axes | Audit |
|---|---|---|---|
| `m04` / `hm02` | likes rich hotel breakfast | `LOCATION.destination`, `TASK.trip_type` | Neither axis is mentioned or established as a qualifier. |
| `m26` | generally likes spicy food | `LOCATION.location`, `PERSON.meal_party` | Neither axis is mentioned or established as a qualifier. |
| `hm04` | prefers a quiet room | `LOCATION.area_noise` | Preference target and applicability qualifier are conflated. |
| `m16` | likes quiet small gatherings | `PERSON.social_party` | The target description is treated as a scope axis without an explicit qualifier distinction. |

`m08`/`m30` and `hm10` contain unsupported conditional semantics around busy areas and soundproofing; these may justify a relevant-but-not-representable axis, but they do not repair the absence semantics in the cases above.

Therefore the current gold mixes two meanings:

1. a genuinely relevant but unresolved applicability axis;
2. an arbitrary unmentioned axis selected by annotator expectation.

Training or tuning a selector to reproduce meaning (2) would reward field invention and directly conflict with the conservative field-activation objective. Round-2 implementation, calibration holdout creation, ADR-0016, and additional real-model requests are blocked pending a domain-contract decision. Existing Frozen/Hard files remain unchanged.

## Required decision

Before staged interpretation can proceed, define whether scope profiles are sparse evidence-backed constraints only, or whether they must enumerate a separately declared set of preference-relevant axes. If the latter is desired, those axes need an input-side annotation/ontology contract; they cannot be inferred from textual absence without circular gold assumptions.
