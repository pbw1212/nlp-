# Phase 1B — Canonical Revision, Provenance & Crash-Safe Lifecycle

## Starting state / Goal

Phase 1A 使用 mutable memory/status，不能保留语义历史或 crash-safe ingestion。目标是稳定 identity、revision history、provenance、正交 validity/retention 和最小 durable journal。

## Decisions / Implementation

- `MemoryItem` stable identity + `MemoryRevision` semantic versions（ADR-0002）。
- `SemanticValidity != RetentionDisposition`；restore 不复活旧 revision。
- revision-scoped `ProvenanceRef`，authority/hash/bounded excerpt（ADR-0003）。
- SQLite ingestion journal：PENDING/APPLIED、raw scrub、event ID idempotency。
- provider outside transaction；typed proposals validate-all-before-write；atomic batch apply。
- schema v2/versioned explicit migration；legacy 普通启动不自动修改。
- `MemoryReadRepository`/`MemorySnapshot` current-safe read boundary（ADR-0005）。

## Components

`schema.py`、`sqlite_manager.py`、`repository.py`、`memory_manager.py`、`crud_engine.py`、revision/journal/migration tests 与 deterministic test revision demo。

## Tests / Acceptance

阶段审核：25 tests passed；Phase 1A regression 保持；historical DB hashes unchanged。

## Limitations / Deferred

Proposal persistence、maintenance journal、migration CLI、cross-identity destructive merge、as-of retrieval、complex trust fusion。

## Git archive

Phase 1B baseline 归档于 canonical `master`；通过 Task 1 frozen tag `phase-1c-task1-frozen` 定位已验收的最小上界。

