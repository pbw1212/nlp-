"""Original-source ownership, integrity and bounded-read regressions."""

import sqlite3
from dataclasses import replace

import pytest
from src.memory.agent.journal import AgentExecutionJournal
from src.memory.conversation.models import ConversationRole
from src.memory.conversation.repository import ConversationRepository
from src.memory.core.schema import Granularity, MemoryType, ProvenanceAuthority
from src.memory.retrieval.sources import SourceAccessError, SourceResolver
from src.memory.storage.sqlite_manager import SQLiteMemoryManager


def seed_memory(
    memories,
    conversations,
    text="酒店预算上限为每晚500元",
    summary="酒店预算偏好",
    session_id="history",
):
    conversations.create_session(session_id)
    message = conversations.append_message(
        session_id, "source-turn", ConversationRole.USER, text
    )
    event, _ = memories.register_ingestion_event(
        text, session_id=session_id, message_id=message.message_id
    )
    revision = memories.create_memory(
        content=summary,
        memory_type=MemoryType.PREFERENCE_RULES,
        granularity=Granularity.SEMANTIC_INSIGHT,
        event_ids=[event.event_id],
        authority=ProvenanceAuthority.EXPLICIT_USER,
    )
    return {
        "memory_id": revision.memory_id,
        "revision_id": revision.revision_id,
    }, message


@pytest.fixture
def source_setup(tmp_path):
    memories = SQLiteMemoryManager(str(tmp_path / "memory.db"))
    conversations = ConversationRepository(str(tmp_path / "conversation.db"))
    journal = AgentExecutionJournal(str(tmp_path / "journal.db"))
    ref, message = seed_memory(memories, conversations)
    resolver = SourceResolver(memories, conversations, journal)
    return resolver, ref, message


def read_one(resolver, ref, catalog=None):
    catalog = catalog or resolver.catalog([ref], [], "task")
    return resolver.resolve([catalog[0].source_ref], catalog, [ref], "task")[0]


def test_resolves_original_after_ingestion_payload_cleanup_and_scopes_message_lookup(
    source_setup,
):
    resolver, ref, message = source_setup
    packet = read_one(resolver, ref)
    assert packet.status == "RESOLVED"
    assert packet.text == message.content
    assert packet.source.authority == "EXPLICIT_USER"
    assert (
        resolver.conversations.get_message("another-session", message.message_id)
        is None
    )
    event = resolver.memories.get_ingestion_event(packet.source.source_id)
    assert event.raw_text is None  # Cannot use this as an original-text fallback.


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("delete", "SOURCE_UNAVAILABLE"),
        ("content", "SOURCE_HASH_MISMATCH"),
        ("role", "SOURCE_ROLE_MISMATCH"),
    ],
)
def test_missing_tampered_or_wrong_role_original_never_returns_excerpt(
    source_setup, fault, expected
):
    resolver, ref, message = source_setup
    with sqlite3.connect(resolver.conversations.db_path) as db:
        if fault == "delete":
            db.execute(
                "DELETE FROM conversation_messages WHERE message_id=?",
                (message.message_id,),
            )
        elif fault == "content":
            db.execute(
                "UPDATE conversation_messages SET content='unrelated secret' WHERE message_id=?",
                (message.message_id,),
            )
        else:
            db.execute(
                "UPDATE conversation_messages SET role='ASSISTANT' WHERE message_id=?",
                (message.message_id,),
            )
    packet = read_one(resolver, ref)
    assert packet.status == expected
    assert packet.text == ""


def test_forged_source_or_revision_and_withdrawn_memory_are_rejected(source_setup):
    resolver, ref, _ = source_setup
    catalog = resolver.catalog([ref], [], "task")
    with pytest.raises(SourceAccessError, match="not granted"):
        resolver.resolve(["arbitrary-message"], catalog, [ref], "task")
    forged = [replace(catalog[0], message_id="another-message")]
    with pytest.raises(SourceAccessError, match="provenance"):
        read_one(resolver, ref, forged)
    with pytest.raises(SourceAccessError, match="stale"):
        resolver.catalog([{**ref, "revision_id": "old"}], [], "task")
    with sqlite3.connect(resolver.memories.db_path) as db:
        db.execute("UPDATE memory_items SET retention_disposition='RECYCLED'")
    with pytest.raises(SourceAccessError, match="withdrawn"):
        read_one(resolver, ref, catalog)


def test_original_reads_are_bounded_and_truncation_is_explicit(tmp_path):
    memories = SQLiteMemoryManager(str(tmp_path / "mem.db"))
    conversations = ConversationRepository(str(tmp_path / "conv.db"))
    journal = AgentExecutionJournal(str(tmp_path / "journal.db"))
    resolver = SourceResolver(memories, conversations, journal)
    refs = [
        seed_memory(memories, conversations, text="x" * 5001, session_id=f"s{i}")[0]
        for i in range(4)
    ]
    catalog = resolver.catalog(refs, [], "task")
    packets = resolver.resolve(
        [row.source_ref for row in catalog], catalog, refs, "task"
    )
    assert sum(len(row.text) for row in packets) == resolver.MAX_TOTAL_CHARS
    assert all(row.status == "TRUNCATED" and row.truncated for row in packets)
    with pytest.raises(SourceAccessError, match="limit"):
        resolver.resolve(["x"] * 5, catalog, refs, "task")


def seed_receipt(journal, task_id="task", status="SUCCEEDED"):
    journal.register_run("executor", "turn", task_id)
    journal.claim_execution(
        "executor",
        "step",
        "call",
        "calculator",
        "hash",
        "idempotency",
        "{}",
        "READ_ONLY",
        "NONE",
    )
    journal.complete_execution(
        "call",
        status,
        result_content="391" if status == "SUCCEEDED" else "",
        error_message="failed" if status == "FAILED" else "",
    )


def test_tool_receipts_require_explicit_scope_same_task_and_success(source_setup):
    resolver, _, _ = source_setup
    seed_receipt(resolver.journal)
    assert resolver.catalog([], [], "task") == ()
    with pytest.raises(SourceAccessError, match="task"):
        resolver.catalog([], ["call"], "another-task")
    catalog = resolver.catalog([], ["call"], "task")
    packet = resolver.resolve([catalog[0].source_ref], catalog, [], "task")[0]
    assert packet.status == "RESOLVED" and "391" in packet.text
    with sqlite3.connect(resolver.journal.db_path) as db:
        db.execute("UPDATE tool_execution_receipts SET result_json='{}'")
    with pytest.raises(SourceAccessError, match="changed"):
        resolver.resolve([catalog[0].source_ref], catalog, [], "task")


def test_failed_tool_receipt_never_becomes_support(source_setup):
    resolver, _, _ = source_setup
    seed_receipt(resolver.journal, status="FAILED")
    catalog = resolver.catalog([], ["call"], "task")
    packet = resolver.resolve([catalog[0].source_ref], catalog, [], "task")[0]
    assert packet.status == "SOURCE_NOT_SUCCESSFUL" and not packet.text


@pytest.mark.parametrize("mode", ["single", "multi_fixed"])
def test_natural_memory_write_binds_original_message_not_extracted_instruction(
    tmp_path, monkeypatch, mode
):
    from src.memory.agent.models import AgentResultStatus
    from src.memory.app.console import PersonalAgentApp
    from src.memory.core.config import MemoryConfig
    from test_personal_agent_app import ProviderRoutedMemoryClient

    monkeypatch.chdir(tmp_path)
    app = PersonalAgentApp(
        data_dir=str(tmp_path / "state"),
        config=MemoryConfig(),
        llm_client=ProviderRoutedMemoryClient(),
        execution_mode=mode,
    )
    raw = "请记住：我solotrip喜欢住在市中心"
    try:
        result = app._process_chat_turn_with_id(raw, "natural-turn", resume=False)
        assert result.status is AgentResultStatus.SUCCESS
        snapshot = app.long_term_memory_mgr.sqlite_mgr.list_current()[0]
        ref = {
            "memory_id": snapshot.memory_id,
            "revision_id": snapshot.revision.revision_id,
        }
        resolver = SourceResolver(
            app.long_term_memory_mgr.sqlite_mgr, app.conversation_repo, app.journal
        )
        packet = read_one(resolver, ref)
        assert packet.status == "RESOLVED" and packet.text == raw
        assert packet.source.message_id is not None
        assert snapshot.provenance[0].session_id == app.active_session_id
        # Terminal replay is a multi_fixed contract; single resumes unfinished turns.
        if mode == "multi_fixed":
            replay = app._process_chat_turn_with_id(raw, "natural-turn", resume=True)
            assert replay.status is AgentResultStatus.SUCCESS
        assert (
            len(app.long_term_memory_mgr.sqlite_mgr.get_history(snapshot.memory_id))
            == 1
        )
    finally:
        app.close()


def test_legacy_natural_memory_replays_without_rewriting_its_provenance(
    tmp_path, monkeypatch
):
    from src.memory.agent.models import AgentResultStatus
    from test_collaboration import make_app
    from test_personal_agent_app import ProviderRoutedMemoryClient

    app = make_app(tmp_path, monkeypatch, ProviderRoutedMemoryClient())
    current_writer = app.agent.memory_writer

    def legacy_writer(content, turn_id):
        return app.long_term_memory_mgr.process_user_interaction(
            content,
            is_explicit=True,
            source_event_id=f"natural-memory:{turn_id}",
            session_id=app.active_session_id,
        )

    raw = "请记住：我solotrip喜欢住在市中心"
    app.agent.memory_writer = legacy_writer
    try:
        original = app._process_chat_turn_with_id(raw, "legacy-turn", resume=False)
        assert original.status is AgentResultStatus.SUCCESS
        snapshot = app.long_term_memory_mgr.sqlite_mgr.list_current()[0]
        app.agent.memory_writer = current_writer
        replay = app._process_chat_turn_with_id(raw, "legacy-turn", resume=True)
        assert replay.status is AgentResultStatus.SUCCESS
        assert (
            app.long_term_memory_mgr.sqlite_mgr.get_current(
                snapshot.memory_id
            ).provenance
            == snapshot.provenance
        )
        assert snapshot.provenance[0].message_id is None
    finally:
        app.close()
