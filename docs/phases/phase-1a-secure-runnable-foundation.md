# Phase 1A — Secure & Runnable Foundation

## Starting state / Goal

Task 1 原型无法作为安全、deterministic baseline。目标是最小修复运行、配置、provider semantics、router/consolidation correctness 和 packaging，不改变 SQLite schema。

## Implementation

- 删除源码 credential，增加 `.env.example` 与集中 `MemoryConfig`。
- Python >=3.10 packaging：`pyproject.toml`/`setup.cfg`。
- `deterministic test / REQUIRED` provider modes 与 typed failures；`OPTIONAL` 仅保留为未来显式降级语义，尚未实现。
- 修复 public router bypass、double consolidation 和失败后 pending input 被静默清除。
- 字符 2-gram 正确标注为 lexical placeholder，并排除 zero-score candidates。
- demo/tests 使用 temporary DB，不污染历史库。

## Tests / Acceptance

阶段审核时 12 tests passed；deterministic test demo 可运行，REQUIRED failure 显式。Phase 1B/1C regression 后最终为 30 tests passed。

## Known limitations

无 revision history、provenance 不完整、pending buffer process-local、schema 未版本化。

## Git archive

Phase 1A 与 Phase 1B 在首次诚实 baseline 中共同归档到 canonical `master`。没有伪造独立历史 commit/tag；Task 1 frozen 状态由 `phase-1c-task1-frozen` 定位。

