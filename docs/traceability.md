> 2026-10-14 维护覆盖：完整 UI/HTTP Demo/桌面包装已移除，保留 CLI/JSONL；下文包含历史冻结阶段说明。当前架构、运行入口和验证以 [维护报告](maintenance/2026-10-14/Agent系统维护与升级准备报告.md) 为准。

# Requirement / Decision / Implementation Traceability

状态：Task 1 frozen baseline；Task 2A-D recall/applicability/evaluation + Phase 2E semantic interpretation foundation implemented；Phase 2G provider-independent EvidenceBundle downstream 已实现并完成测试验收；Task 3A-D durable task-state/working-context 已实现并冻结；Phase 4A AgentRuntime 与 Phase 4B MainAgent 已冻结；MainAgent v1.3 对话 Context 已冻结；Phase 4C/4D 完成运行时契约、幂等性、来源追踪、失败真实性、日志脱敏与 Journal 完整性加固；Codex-style event host、turn resume、usage/compaction events、tool catalog/approval events、workspace search/patch 和 MCP stdio 已集成。Task 4 人格与认知模块暂缓。

归档说明：protected-local 原始材料已从 canonical branch/tag 可达历史清除。Traceability 使用 stable phase tag 和 component/test reference，不维护重写前后 SHA 对照表。

| Requirement / research question | Decision | Component/code | Tests/benchmark | Phase | Git archive |
|---|---|---|---|---|---|
| Task 1/2/3 ownership 不混用 | ADR-0001 | `MemoryReadRepository`, `MemorySnapshot`; Task 2/3 docs | `test_storage.py`, `test_current_safe_read_matrix` | 0C, 1B | tag `phase-1c-task1-frozen` |
| Stable identity + semantic history | ADR-0002 | `MemoryItem`, `MemoryRevision`, `apply_revision` | `test_revisions.py`; acceptance A/B/D | 1B | tag `phase-1c-task1-frozen` |
| semantic validity != retention | ADR-0002 | `SemanticValidity`, `RetentionDisposition`, retention APIs | lifecycle tests；acceptance C | 1B/1C | tag `phase-1c-task1-frozen` |
| Revision-scoped provenance | ADR-0003 | `ProvenanceRef`, join table, snapshot hydration | ingestion/revision tests；acceptance B/G/J | 1B/1C | tag `phase-1c-task1-frozen` |
| Durable pending + atomic apply | ADR-0003 | `IngestionEvent`, `register_ingestion_event`, `apply_proposals_atomic` | journal tests；acceptance E-I | 1B/1C | tag `phase-1c-task1-frozen` |
| Explicit provider capability semantics | ADR-0004 | deterministic test/REQUIRED implemented in `MemoryConfig`, `ProviderMode` | `test_provider_modes.py` | 1A | tag `phase-1c-task1-frozen` |
| SQLite truth + derived lexical index | ADR-0005 | `SQLiteMemoryManager`; `InMemoryVectorStore` placeholder | storage/integrity tests | 1A-1C | tag `phase-1c-task1-frozen` |
| Current-safe default read | ADR-0005 | `get_current`, `list_current`, `get_history` | current-safe matrix；acceptance A/SQLite | 1B/1C | tag `phase-1c-task1-frozen` |
| Explicit legacy migration / future version safety | ADR-0005 | `migrate_legacy_database`, `_open_or_initialize` | `test_migration.py`, future-schema test | 1B/1C | tag `phase-1c-task1-frozen` |
| TaskLedger != Checkpoint != Active Context | ADR-0006 | `code/src/memory/task_state/`, `TaskStateRepository`, `TaskContextBuilder` | `code/tests/test_task_state.py`; Task3A acceptance | 3A | phase-3a tag |
| LLM/task event proposes; deterministic validator owns durable mutation | ADR-0006 | `TaskEvent`, `TaskStateProposalBatch`, `TaskStateProposalValidator`, `TaskStateUpdater` | `test_task_state_updates.py`; Task3B acceptance | 3B | phase-3b tag |
| Working context is bounded/rebuildable; checkpoint refresh and compaction stay derived | ADR-0006 | `WorkingContextPolicy`, `TaskContextMaintenanceService`, `DeterministicTaskCompactor` | `test_task_state_maintenance.py`; Task3C acceptance | 3C | phase-3c tag |
| Model-agnostic natural-language proposals and Task lifecycle facade | ADR-0006 | `OpenAICompatibleTaskStateInterpreter`, `TaskStateService`, `TaskStatus` | `test_task3d_final.py`; Task3D acceptance | 3D | phase-3d tag |
| Persistent Conversation Session Context | ADR-0006 | `ConversationSessionService`, `ConversationSessionRepository` (`~/.agent_data/conversations/conversation.db`, centralized transcript/compaction storage) | `test_conversation_session.py`, `test_main_agent_conversation_history` | MainAgent v1.3 | tag `main-agent-mechanism-v1.3` |
| CLI MVP & Readline Line Editing | UX Integration | `PersonalAgentApp`, GNU `readline` line editing, `~/.agent_data/cli/cli_history` | `test_cli_line_editing.py`, `test_personal_agent_app.py` | Product CLI | master commit |
| Post-freeze Task 3 invalid proposal recovery | Runtime Patch | `TaskStateInterpreter` invalid `support_span` proposal recovery | `test_invalid_support_span_proposal_recovery` | Runtime Patch | master commit |
| Autonomous Product Trial Harness | Test Infrastructure | `code/src/memory/product_trial/`, `code/scripts/personal_agent_trial.py` | `code/tests/test_product_trial_harness.py`, 14 scenarios PASS | Product Trial | current release |
| Explicit Memory Generic Lifecycle Routing | Bugfix / Architecture Restoration | Forensic Audit REJECTED `642fbd`; `bad9e0b` RESTORED `NemoriDistiller.propose_explicit_lifecycle` + `_validate_proposals` + `apply_proposals_atomic` | `test_production_hardcoding_overfit_guard`, `test_generic_explicit_route_arbitrary_unseen_domain` | Explicit Fix | main history commit `bad9e0b` |
| Agent-selected Memory Update | Agent internal tool + NEMORI lifecycle pipeline | `update_long_term_memory`, `run_autonomous_dream_pipeline`, durable ingestion journal | `test_personal_agent_app.py`, `test_ingestion_journal.py`, lifecycle regression tests | current release | main |
| Inspectable adaptive user profile + recoverable implicit pruning | ADR-0021 | `get_profile_repository`, profile HTTP API, Provider `LifecycleOperation.REMOVE`, pinned/profile-type persistence guards | automatic profile removal/protection tests; structured frontend API tests | Adaptive Profile | current release |
| Runtime DTO / prompt trust boundary | ADR-0019 | `PromptComposer`, `TurnRequest`, `DistilledEvidence.text` | `test_prompt_composer_uses_distilled_text_and_keeps_current_request_last`, runtime hardening tests | 4C | current release |
| Inferred batch provenance and replay identity | ADR-0019 | `run_autonomous_dream_pipeline`, `LongTermMemoryManager`, ingestion `result_metadata` | batch consolidation + NOOP replay hardening tests | 4C | current release |
| Concurrent conversation idempotency and strict budgets | ADR-0019 | `ConversationRepository.append_message`, `build_conversation_context` | concurrent append/conflict + budget boundary tests | 4C | current release |
| Retry/decay/config boundary invariants | ADR-0019 | `LLMClientBase`, `MemoryConfig`, `ConsolidationEngine` | provider retry, invalid configuration, clock rollback and default-off forgetting tests | 4C | current release |
| Failure is never represented as success/absence | ADR-0020 | `core.diagnostics`, batch consolidation, `ToolCallResult`, Product Trial observer | ingestion/lifecycle failure tests; full product trial | 4D | current release |
| Durable replay uses complete source identity | ADR-0020 | ingestion journal, `TaskStateRepository.register_event`, `AgentExecutionJournal` | source collision, corrupt receipt and parent-run tests | 4D | current release |
| Provider/runtime diagnostics are useful but non-secret | ADR-0020 | `ProviderError.to_safe_projection`, `log_boundary_failure`, CLI rotating log | provider sanitization + raw-exception exclusion tests | 4D | current release |
| Global runtime home and separated storage | ADR-0006 / storage contract | `default_agent_data_dir`, `AgentStorageLayout`, global `~/.agent_data/` | workspace/storage migration tests | current main | `a3394cae` |
| Codex-style host lifecycle and replay | Runtime integration | `AgentEventStore`, `AgentEventStream`, JSONL app server | `test_agent_events.py`, `test_app_server_protocol.py` | current main | `c021f5b6` |
| Resumable turns and usage/compaction observability | Runtime integration | turn lifecycle, `turn/resume`, `turn.usage`, compaction and memory events | agent event/app server/console tests | current main | `21e4b94b` |
| Public tool catalog and approval lifecycle | Runtime integration | `ToolRegistry.describe`, tool/approval events, protocol catalog | tooling/app server tests | current main | `ffe3e8a9` |
| Workspace search/patch and MCP stdio | Runtime integration | scoped `search_files`, `apply_patch`, `McpManager` | workspace/MCP/app server tests | current main | `50ac4cf9`, `8449bfda` |
| Protected raw project materials stay local | ADR-0008 | `.gitignore`; canonical Git excludes protected-local set | ignore/tracking/reachable-history audit | repository governance | canonical `master` |

## Maintenance rule

任何改变 domain semantics、persistent schema、public API、failure behavior 或 research baseline 的 logical change，必须同时更新：current architecture、对应 ADR（或新 superseding ADR）、phase record 和本表。Accepted ADR 不通过静默编辑抹去历史原因。
