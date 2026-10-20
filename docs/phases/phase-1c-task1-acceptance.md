# Phase 1C — Task 1 Acceptance & Integrity Hardening

## Starting state / Goal

Phase 1B architecture 已通过。目标不是增加功能，而是用 A-J deterministic semantic scenarios 和真实 SQLite DDL 验收 Task 1；只修验收暴露的 correctness bugs。

## Problems discovered / Fixes

- `current_revision_id` 缺少 DB FK/同 identity CURRENT 约束：增加 deferred FK 和 triggers。
- future `user_version=999` 空库可能被初始化：现在显式拒绝。
- canonical internal path 仍读取 compatibility facade：增加 typed ADD/read path并限制 facade 到 deprecated adapters。
- explicit UPDATE provenance authority 修正为 `EXPLICIT_USER`。

## Acceptance

`code/tests/test_lifecycle.py and code/tests/test_revisions.py` 覆盖：preference evolution、explicit correction、recycle/restore、historical reaffirmation、event replay/collision、provider failure/restart、transaction rollback/retry、NOOP、RISE、SQLite integrity 和 migration/version safety。

最终结果：30 tests passed；两个 demo、runner、compileall、wheel import、secret guard、historical DB hashes 全部 PASS。

## Architecture changes

没有改变 Task 1/2/3 边界；只强化 ADR-0002/0003/0005 已规定的不变量。Task 1 baseline 冻结，可进入 Task 2。

## Git archive

- tag `phase-1c-task1-frozen`

Protected-local 原始材料曾误入 baseline；后续经单独授权的 history rewrite 从 canonical 可达历史移除。长期规则见 ADR-0008。
