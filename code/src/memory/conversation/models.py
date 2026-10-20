"""Domain models for Conversation Sessions and Dialogue Transcripts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Optional, Tuple


class ConversationRole(str, Enum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"
    TOOL = "TOOL"


@dataclass(frozen=True)
class ConversationMessage:
    """Individual dialogue message record within a session transcript."""

    message_id: str
    session_id: str
    turn_id: str
    role: ConversationRole
    content: str
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ConversationSession:
    """Durable conversation session metadata."""

    session_id: str
    title: str
    active_task_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: str = "ACTIVE"
    # The authorized local folders belong to the conversation session.  Keep
    # this separate from Task 3 so resuming a session restores its file scope
    # without leaking another session's workspace into the active prompt.
    workspace_roots: Tuple[str, ...] = ()
    current_workspace_root: Optional[str] = None


@dataclass(frozen=True)
class ConversationMessageView:
    """Read-only lightweight view of a historical dialogue message."""

    role: ConversationRole
    content: str


@dataclass(frozen=True)
class ConversationContext:
    """Model-facing transcript projection with durable-summary metadata.

    The repository keeps the complete transcript.  This object is only the
    current model-window projection: an optional persisted compaction summary
    followed by an intact recent tail.
    """

    session_id: str
    summary: Optional[str] = None
    recent_messages: Tuple[ConversationMessageView, ...] = ()
    omitted_message_count: int = 0
    summarized_message_count: int = 0
    compaction_source: str = "NONE"
    compaction_degraded: bool = False


@dataclass(frozen=True)
class ConversationCompaction:
    """Durable compaction checkpoint; raw messages remain authoritative."""

    compaction_id: str
    session_id: str
    boundary_message_id: str
    summary: str
    summarized_message_count: int
    source: str = "MODEL"
    degraded_reason: Optional[str] = None
    created_at: float = field(default_factory=time.time)
