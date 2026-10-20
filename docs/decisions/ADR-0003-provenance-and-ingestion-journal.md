# ADR-0003：Revision-scoped provenance 与 durable ingestion journal

Status: **Accepted**

## Context / Problem

需要区分 explicit user correction 与 inferred memory，并在 provider/transaction 失败或重启后安全重试；又不能把完整私人 transcript 永久复制到每个 revision。

## Options considered

1. 只存任意 metadata；2. full event sourcing/transcript；3. lightweight `ProvenanceRef` + PENDING/APPLIED ingestion journal。

## Decision / Why

采用选项 3。Provenance 保存 source identity、authority、stable hash 和 bounded excerpt。PENDING 期间保留 raw input；atomic semantic commit 后 event APPLIED 且 raw text scrub。Journal 不是完整 transcript。

Provider 在 transaction 外运行，validated proposals 在一个 transaction 内应用。相同 event ID/hash 幂等；同 ID/不同 hash 显式冲突。

## Rejected alternatives

- 任意 metadata：无法执行完整性和 authority 查询。
- full event sourcing：超过当前 Task 1 baseline 需求。
- 网络调用放在 DB transaction 内：导致长事务和 crash ambiguity。

## Reference evidence

Graphiti Episode → EntityEdge source linkage 提供 L1/L2 动机；实现采用轻量 relational model。

## Consequences

Online retry 可能重新计算 proposal；proposal persistence 与 full transcript deferred。

## Implementation / Test mapping

- `code/src/memory/core/schema.py:ProvenanceRef,IngestionEvent,LifecycleProposal`
- `code/src/memory/storage/sqlite_manager.py:register_ingestion_event,apply_proposals_atomic`
- `code/tests/test_ingestion_journal.py`
- `code/tests/test_lifecycle.py and code/tests/test_revisions.py:scenario_e,scenario_f,scenario_g,scenario_h,scenario_i`

Introduced in phase: Phase 1B。Supersedes: process-local-only pending semantics。Superseded by: None。
