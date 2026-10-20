# Phase 2C — Applicability Benchmark & Controlled Ablation

## Starting state / Goal

Frozen baselines：Task 1 `phase-1c-task1-frozen`、Task 2A `phase-2a-recall-baseline`、Task 2B `phase-2b-applicability-baseline`。本阶段不调算法，只建立 deterministic controlled evaluation v1、weak baselines、metrics 与 error analysis。

## Dataset schema

目录 `benchmarks/task2/applicability_eval_v1/`：

- `memories.json`：32 Task 1 temporary inputs；一条 preference evolution 含 old/current content。
- `profiles.json`：32 个独立 Task 2 profiles。
- `queries.json`：28 queries、domain/family、每案 8-memory universe、recall positives。
- `contexts.json`：28 caller-supplied structured contexts。
- `labels.json`：224 gold pairs、reason dimensions、supported/rich subset。
- `README.md`：证据边界。

Runtime files 不含 gold。Validator 检查 schema version、unique IDs、所有 refs、完整 pair coverage、enum、每维 MATCH/MISMATCH/UNKNOWN、multi-dimension AMBIGUOUS 和 rich subset。

## Scenario taxonomy

A person/party shift；B location shift；C task shift；D hard constraint；E preference evolution；F explicit general；G unspecified scope；H ambiguous request。Domain 覆盖 travel/hotel、food、work、exercise、shopping、transport、social、schedule。

## Baselines

- B0 Recall-only：全部 retain/预测 MATCH。
- B1-permissive：known explicit conflict reject；missing/unknown/ambiguous pass-through并预测 MATCH。
- B1-strict：known conflict 与 missing/unknown/ambiguous 全部 reject并预测 MISMATCH。
- B2：frozen Phase 2B engine；MISMATCH reject，UNKNOWN/AMBIGUOUS typed retention。

B1 独立实现，没有调用 B2 engine。

## Metric definitions

```text
Recall@K = retrieved relevant targets / all relevant targets
MRR = mean(1 / first relevant rank)
Mismatch rejection = rejected gold MISMATCH / retrieved gold MISMATCH
Applicable retention = retained gold MATCH / retrieved gold MATCH
False rejection = rejected gold MATCH / retrieved gold MATCH
False acceptance = retained gold MISMATCH / retrieved gold MISMATCH
Contamination = retained gold MISMATCH / all retained
Unknown preservation = retained + predicted UNKNOWN / retrieved gold UNKNOWN
Ambiguous preservation = retained + predicted AMBIGUOUS / retrieved gold AMBIGUOUS
Decision coverage = predicted MATCH or MISMATCH / assessed
Uncertainty rate = predicted UNKNOWN or AMBIGUOUS / assessed
```

另报告 unknown/ambiguous row retention，区分“保留 row”与“保留 uncertainty semantics”。空 denominator 行为在 evaluator 中显式定义。

## Main observed results at K=4

```text
Method           Recall  MRR     MatchRetain  MismatchReject  FalseReject  FalseAccept  Contam
B0 Recall        1.0000  0.9821  1.0000       0.0000          0.0000       1.0000       0.4128
B1 permissive    1.0000  0.9821  1.0000       1.0000          0.0000       0.0000       0.0000
B1 strict        1.0000  0.9821  1.0000       1.0000          0.0000       0.0000       0.0000
B2 applicability 1.0000  0.9821  1.0000       1.0000          0.0000       0.0000       0.0000
```

B2 uncertainty preservation=`1.0`，decision coverage=`0.6147`，uncertainty rate=`0.3853`。B1-permissive保留 uncertain rows但将其当 definite MATCH；B1-strict删除 uncertain rows。B0 contamination 随 K 从 `0.071`（K1）升至 `0.456`（K8）；B2 在当前 supported rules 上保持 `0`。

## Error analysis / limitations

- 14 个 rank≤2 gold mismatch，证明 hard negatives 不只出现在尾部。
- B2@4 false accepts/rejects 均为 0；42 个 assessed candidates 保持 uncertainty。
- rich-reasoning subset 只有一条“busy area requires soundproof”conditional case，正确保留 UNKNOWN；规模太小，不能代表 semantic reasoning performance。
- B1 与 B2 在 explicit mismatch safety 上相同：当前 evidence 不能证明复杂 assessor 比 exact filter 更安全；差异仅在 uncertainty semantics。
- context/profile 都是人工正确输入，未测 NL extraction、profile inference、real-world agent outcome 或 academic novelty。

## Reproducibility

Evaluator 使用 temporary SQLite、stable IDs/order、无 timestamp 输出；连续两次 report/JSON 完全一致。Phase 2A/2B algorithms 未修改，历史 revision 不进入 current corpus。

## Final acceptance / Git archive

最终结果：64 tests、Task 1 acceptance、Task 2A recall acceptance、Task 2B applicability acceptance、Task 2C controlled evaluation、两次 deterministic JSON compare、compileall、wheel import、production/benchmark import guard、secret guard 与 historical DB hash guard 全部 PASS。

Git archive：

- annotated tag `phase-2c-applicability-eval`
