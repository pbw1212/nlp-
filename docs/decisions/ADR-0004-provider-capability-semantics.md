# ADR-0004：显式 provider capability/error semantics

Status: **Accepted**

## Context / Problem

“无 API key 时测试运行”与“显式选择真实 provider 后失败”曾可能共享 silent fallback，导致 enhanced workflow 伪装成功。

## Options considered

1. 任意失败自动 fallback；2. 只有 online；3. explicit `deterministic test / OPTIONAL / REQUIRED` capability policy。

## Decision / Why

使用集中配置和 typed provider errors。`deterministic test` 从一开始选择 deterministic baseline；`REQUIRED` 缺配置、网络或校验失败时显式失败，不 silent fallback。`OPTIONAL` 是保留的 capability 语义：只有调用者明确接受降级时才可使用；**当前 `ProviderMode` 尚未实现 OPTIONAL，仅实现 deterministic test/REQUIRED**。

## Rejected alternatives

- silent fallback：隐藏质量/能力变化。
- 只有 online：破坏 deterministic tests 和无 key demo。

## Consequences

Provider output 必须先验证为 typed lifecycle proposals；配置和 secret 只来自环境。

## Implementation / Test mapping

- `code/src/memory/core/config.py:ProviderMode,MemoryConfig`
- `code/src/memory/llm/errors.py`
- `code/src/memory/llm/llm_client.py`
- `code/tests/test_provider_modes.py`

Introduced in phase: Phase 1A（deterministic test/REQUIRED implemented；OPTIONAL planned）。Supersedes: implicit fallback behavior。Superseded by: None。
