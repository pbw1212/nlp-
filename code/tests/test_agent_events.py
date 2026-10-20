from __future__ import annotations

import threading

import pytest

from src.memory.runtime.events import AgentEventStore, AgentEventStream


def test_event_store_assigns_monotonic_sequence_and_replays_by_cursor(tmp_path):
    store = AgentEventStore(str(tmp_path / "execution" / "events.db"))
    first = store.append("turn.started", session_id="session_1", turn_id="turn_1", payload={"kind": "user"})
    second = store.append("turn.completed", session_id="session_1", turn_id="turn_1", payload={"status": "SUCCESS"})
    other = store.append("turn.started", session_id="session_2", turn_id="turn_2")

    assert (first.sequence, second.sequence, other.sequence) == (1, 2, 3)
    assert [event.event_id for event in store.list_events(after_sequence=first.sequence, session_id="session_1")] == [second.event_id]
    assert store.latest_sequence() == other.sequence


def test_event_store_rejects_idempotency_identity_mismatch(tmp_path):
    store = AgentEventStore(str(tmp_path / "events.db"))
    store.append("item.completed", event_id="evt_fixed", payload={"value": 1})

    with pytest.raises(ValueError, match="identity mismatch"):
        store.append("item.completed", event_id="evt_fixed", payload={"value": 2})


def test_event_stream_persists_before_notifying_and_ignores_dead_subscriber(tmp_path):
    store = AgentEventStore(str(tmp_path / "events.db"))
    stream = AgentEventStream(store)
    received = []
    lock = threading.Lock()

    def subscriber(event):
        with lock:
            received.append(event)

    def broken(_event):
        raise RuntimeError("client disconnected")

    stream.subscribe(subscriber)
    stream.subscribe(broken)
    event = stream.emit("turn.started", session_id="session_1")

    assert received == [event]
    assert store.list_events()[0] == event


def test_event_store_validates_replay_bounds(tmp_path):
    store = AgentEventStore(str(tmp_path / "events.db"))
    with pytest.raises(ValueError):
        store.list_events(limit=0)
    with pytest.raises(ValueError):
        store.list_events(after_sequence=-1)


def test_event_store_identifies_only_turns_without_terminal_event(tmp_path):
    store = AgentEventStore(str(tmp_path / "events.db"))
    store.append("turn.started", session_id="session_1", turn_id="turn_open")
    store.append("turn.started", session_id="session_1", turn_id="turn_done")
    store.append("turn.completed", session_id="session_1", turn_id="turn_done", payload={"status": "SUCCESS"})

    unfinished = store.list_unfinished_turns(session_id="session_1")

    assert [event.turn_id for event in unfinished] == ["turn_open"]
