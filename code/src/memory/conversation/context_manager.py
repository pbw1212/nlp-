"""Model-aware conversation windowing and durable transcript compaction."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from src.memory.conversation.models import ConversationContext, ConversationMessage
from src.memory.conversation.repository import ConversationRepository
from src.memory.core.diagnostics import log_boundary_failure
from src.memory.llm.errors import ProviderError
from src.memory.llm.llm_client import LLMClientBase


logger = logging.getLogger(__name__)


# This is deliberately a capability table, not a semantic memory rule.  An
# explicit config value always wins.  The production model is DeepSeek V4
# Flash, whose documented context length is 1M tokens.
KNOWN_CONTEXT_WINDOWS = {
    "deepseek-v4-flash": 1_000_000,
}
DEFAULT_CONTEXT_WINDOW_TOKENS = 32_768


def resolve_context_window_tokens(model_name: str, configured: Optional[int] = None) -> tuple[int, str]:
    """Resolve a conservative model-window capability without pretending API discovery is universal."""
    if configured is not None and configured > 0:
        return configured, "configured"
    normalized = model_name.casefold()
    for prefix, tokens in KNOWN_CONTEXT_WINDOWS.items():
        if prefix in normalized:
            return tokens, f"model-capability:{prefix}"
    return DEFAULT_CONTEXT_WINDOW_TOKENS, "fallback-conservative"


def estimate_tokens(text: str) -> int:
    """Cheap multilingual estimate used only for window budgeting.

    Provider usage remains the authoritative measurement when available.  The
    estimator is intentionally conservative for CJK text and does not alter
    stored transcript content.
    """
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u2e80" <= char <= "\u9fff")
    non_cjk_chars = len(text) - cjk
    return max(1, cjk + math.ceil(non_cjk_chars / 4))


@dataclass(frozen=True)
class ConversationWindowPolicy:
    context_window_tokens: int
    context_window_source: str
    reserve_tokens: int = 8_192
    compact_at_ratio: float = 0.78
    keep_recent_tokens: int = 20_000

    def __post_init__(self) -> None:
        if self.context_window_tokens <= 0 or self.reserve_tokens <= 0 or self.keep_recent_tokens <= 0:
            raise ValueError("conversation window budgets must be positive")
        if self.context_window_tokens <= self.reserve_tokens:
            raise ValueError("reserve_tokens must be smaller than the model context window")
        if not 0.5 <= self.compact_at_ratio < 1.0:
            raise ValueError("compact_at_ratio must be in [0.5, 1)")

    @property
    def usable_tokens(self) -> int:
        ratio_budget = int(self.context_window_tokens * self.compact_at_ratio) - self.reserve_tokens
        hard_budget = self.context_window_tokens - self.reserve_tokens
        return max(1, min(ratio_budget, hard_budget))

    @property
    def summary_token_budget(self) -> int:
        # Keep the checkpoint a minority of the projected window. The cap is a
        # resource safeguard, not a content-selection rule; raw transcript is
        # always retained for a later rebuild.
        return max(256, min(8_192, self.usable_tokens // 4))


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

SUMMARY_SYSTEM_PROMPT = """You maintain a durable conversation checkpoint for a personal agent.
Summarize the supplied older transcript for another assistant that will continue the same
conversation. Preserve user-stated facts, explicit preferences, unresolved questions,
decisions, constraints, corrections, commitments, tool outcomes, and exact opaque IDs/URLs
that are needed for continuity. Distinguish user statements from assistant suggestions and
do not invent conclusions. This is a checkpoint, not a reply to the user. Return only JSON
with one string field named summary.
"""


class ConversationContextManager:
    """Builds a dynamic transcript projection and persists compaction checkpoints."""

    def __init__(
        self,
        repository: ConversationRepository,
        llm_client: LLMClientBase,
        *,
        model_name: str,
        configured_context_window_tokens: Optional[int] = None,
        reserve_tokens: int = 8_192,
        compact_at_ratio: float = 0.78,
        keep_recent_tokens: int = 20_000,
    ) -> None:
        self.model_name = model_name
        window, source = resolve_context_window_tokens(model_name, configured_context_window_tokens)
        self.repository = repository
        self.llm_client = llm_client
        self.policy = ConversationWindowPolicy(
            context_window_tokens=window,
            context_window_source=source,
            reserve_tokens=reserve_tokens,
            compact_at_ratio=compact_at_ratio,
            keep_recent_tokens=min(keep_recent_tokens, max(1_024, window // 2)),
        )

    def build_for_turn(
        self,
        session_id: str,
        *,
        force_compact: bool = False,
        exclude_turn_id: Optional[str] = None,
    ) -> ConversationContext:
        all_messages = self.repository.list_messages(session_id)
        latest = self.repository.get_latest_compaction(session_id)
        if latest is not None:
            tail = self.repository.list_messages_after(session_id, latest.boundary_message_id)
            estimated_tokens = estimate_tokens(latest.summary) + sum(
                estimate_tokens(self._message_text(message)) for message in tail
            )
        else:
            tail = all_messages
            estimated_tokens = sum(estimate_tokens(self._message_text(message)) for message in all_messages)

        # Provider usage includes system/task/tool overhead that the local
        # transcript estimator cannot see. Once a real provider reports a
        # larger prompt, use that observation as a conservative lower bound
        # for the next window decision.
        provider_input_tokens = self._last_provider_usage().get("input_tokens", 0)
        if isinstance(provider_input_tokens, int) and provider_input_tokens > 0:
            estimated_tokens = max(estimated_tokens, provider_input_tokens)

        if force_compact or estimated_tokens > self.policy.usable_tokens:
            self._compact(session_id, all_messages, latest)

        # The repository applies the latest durable boundary and keeps the
        # intact tail. Character budget is derived from the model capability,
        # not a product-level "8 messages/6000 chars" constant. Reserve room
        # for the checkpoint itself when one exists.
        latest = self.repository.get_latest_compaction(session_id)
        summary_tokens = estimate_tokens(latest.summary) if latest is not None else 0
        tail_char_budget = max(0, self.policy.usable_tokens - summary_tokens) * 3
        return self.repository.build_conversation_context(
            session_id,
            max_messages=None,
            max_chars=tail_char_budget,
            exclude_turn_id=exclude_turn_id,
        )

    def describe(self, session_id: str) -> dict[str, object]:
        """Return safe, read-only diagnostics for the active model projection."""
        all_messages = self.repository.list_messages(session_id)
        latest = self.repository.get_latest_compaction(session_id)
        tail_count = len(all_messages)
        estimated_tokens = sum(estimate_tokens(self._message_text(message)) for message in all_messages)
        if latest is not None:
            tail_count = len(self.repository.list_messages_after(session_id, latest.boundary_message_id))
            estimated_tokens = estimate_tokens(latest.summary) + sum(
                estimate_tokens(self._message_text(message))
                for message in self.repository.list_messages_after(session_id, latest.boundary_message_id)
            )
        return {
            "model": self.model_name,
            "context_window_tokens": self.policy.context_window_tokens,
            "context_window_source": self.policy.context_window_source,
            "usable_tokens": self.policy.usable_tokens,
            "reserve_tokens": self.policy.reserve_tokens,
            "compaction_ratio": self.policy.compact_at_ratio,
            "keep_recent_tokens": self.policy.keep_recent_tokens,
            "transcript_message_count": len(all_messages),
            "projected_tail_message_count": tail_count,
            "estimated_projected_tokens": estimated_tokens,
            "compaction_id": getattr(latest, "compaction_id", None) if latest is not None else None,
            "summarized_message_count": getattr(latest, "summarized_message_count", 0) if latest is not None else 0,
            "compaction_source": getattr(latest, "source", "NONE") if latest is not None else "NONE",
            "compaction_degraded": bool(latest is not None and getattr(latest, "source", "MODEL") == "FALLBACK"),
            "compaction_degraded_reason": getattr(latest, "degraded_reason", None) if latest is not None else None,
            "provider_last_usage": self._last_provider_usage(),
        }

    def _compact(
        self,
        session_id: str,
        all_messages: Sequence[ConversationMessage],
        latest: Optional[object],
    ) -> None:
        if not all_messages:
            return
        boundary_id = getattr(latest, "boundary_message_id", None)
        previous_summary = getattr(latest, "summary", "") if latest is not None else ""
        start_index = 0
        if boundary_id:
            positions = {message.message_id: index for index, message in enumerate(all_messages)}
            start_index = positions.get(boundary_id, -1) + 1
        unsummarized = list(all_messages[start_index:])
        if not unsummarized:
            return

        keep_tokens = min(self.policy.keep_recent_tokens, max(1_024, self.policy.usable_tokens // 3))
        tail_start = len(unsummarized)
        tail_tokens = 0
        # Select complete turns from newest to oldest. The previous reverse
        # message loop stopped after the newest user/assistant pair and could
        # silently throw away most of the configured recent-token budget.
        while tail_start > 0:
            turn_id = unsummarized[tail_start - 1].turn_id
            group_start = tail_start - 1
            group_tokens = estimate_tokens(self._message_text(unsummarized[group_start]))
            while group_start > 0 and unsummarized[group_start - 1].turn_id == turn_id:
                group_start -= 1
                group_tokens += estimate_tokens(self._message_text(unsummarized[group_start]))
            if tail_tokens + group_tokens > keep_tokens and tail_start < len(unsummarized):
                break
            tail_start = group_start
            tail_tokens += group_tokens
        prefix = unsummarized[:tail_start]
        if not prefix:
            return

        input_budget = max(
            256,
            self.policy.usable_tokens
            - self.policy.summary_token_budget
            - min(self.policy.keep_recent_tokens, self.policy.usable_tokens // 3),
        )
        compaction_source = "MODEL"
        degraded_reason: Optional[str] = None
        try:
            summary = self._summarize_prefix(previous_summary, prefix, input_budget)
        except ProviderError as exc:
            log_boundary_failure(logger, "conversation_compaction_provider_failed", exc, session_id=session_id)
            summary = self._fallback_summary(previous_summary, prefix)
            compaction_source = "FALLBACK"
            degraded_reason = type(exc).__name__
        except Exception as exc:
            log_boundary_failure(logger, "conversation_compaction_failed", exc, session_id=session_id)
            summary = self._fallback_summary(previous_summary, prefix)
            compaction_source = "FALLBACK"
            degraded_reason = type(exc).__name__

        summarized_count = (getattr(latest, "summarized_message_count", 0) if latest is not None else 0) + len(prefix)
        self.repository.save_compaction(
            session_id,
            prefix[-1].message_id,
            summary,
            summarized_count,
            source=compaction_source,
            degraded_reason=degraded_reason,
        )

    def _summarize_prefix(
        self,
        previous_summary: str,
        messages: Sequence[ConversationMessage],
        input_budget: int,
    ) -> str:
        """Summarize every prefix segment before synthesizing one checkpoint.

        A head/tail character cut is not an acceptable semantic compactor: it
        can remove a correction or decision in the middle of a long turn. The
        chunks are resource-bounded by the resolved model window, while the
        raw transcript remains the authoritative source for retry/rebuild.
        """
        chunks: list[str] = []
        current: list[str] = []
        current_tokens = 0
        for message in messages:
            line = self._message_text(message)
            line_tokens = estimate_tokens(line)
            if current and current_tokens + line_tokens > input_budget:
                chunks.append("\n".join(current))
                current = []
                current_tokens = 0
            if line_tokens > input_budget:
                # A single oversized message is split into contiguous pieces;
                # unlike the old head/tail cut, no middle segment is discarded.
                char_budget = max(256, input_budget * 3)
                for start in range(0, len(line), char_budget):
                    piece = line[start : start + char_budget]
                    if current:
                        chunks.append("\n".join(current))
                        current = []
                        current_tokens = 0
                    chunks.append(piece)
                continue
            current.append(line)
            current_tokens += line_tokens
        if current:
            chunks.append("\n".join(current))
        if not chunks:
            return previous_summary

        chunk_summaries = [self._call_summary(chunk) for chunk in chunks]
        if len(chunk_summaries) == 1 and not previous_summary:
            return self._fit_text_to_tokens(chunk_summaries[0], self.policy.summary_token_budget)

        synthesis_input = "<previous-summary>\n" + (previous_summary or "(none)")
        synthesis_input += "\n</previous-summary>\n\n<segment-summaries>\n"
        synthesis_input += "\n\n".join(
            f"[segment {index}]\n{summary}" for index, summary in enumerate(chunk_summaries, start=1)
        )
        synthesis_input += "\n</segment-summaries>"
        synthesis_input = self._fit_text_to_tokens(synthesis_input, input_budget)
        return self._call_summary(synthesis_input)

    def _call_summary(self, prompt: str) -> str:
        raw = self.llm_client.generate_json(
            SUMMARY_SYSTEM_PROMPT,
            prompt,
            response_schema=SUMMARY_SCHEMA,
            schema_name="conversation_compaction",
        )
        summary = raw.get("summary") if isinstance(raw, dict) else None
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("conversation compaction response has no summary")
        return self._fit_text_to_tokens(summary.strip(), self.policy.summary_token_budget)

    @staticmethod
    def _message_text(message: ConversationMessage) -> str:
        return f"{message.role.value}: {message.content}"

    def _fallback_summary(self, previous_summary: str, messages: Sequence[ConversationMessage]) -> str:
        lines = [previous_summary] if previous_summary else []
        lines.extend(self._message_text(message) for message in messages)
        return self._fit_text_to_tokens("\n".join(lines), self.policy.summary_token_budget)

    def _last_provider_usage(self) -> dict[str, int]:
        raw = getattr(self.llm_client, "last_usage", {})
        if not isinstance(raw, Mapping):
            return {}
        return {
            key: value
            for key, value in raw.items()
            if key in {"input_tokens", "output_tokens"} and isinstance(value, int) and value >= 0
        }

    @staticmethod
    def _fit_text_to_tokens(text: str, token_budget: int) -> str:
        if estimate_tokens(text) <= token_budget:
            return text
        # The estimator is intentionally simple; use a conservative character
        # budget and preserve both ends so IDs/decisions near either boundary
        # have a chance to survive. The complete source remains durable.
        char_budget = max(1, token_budget * 3)
        marker = "\n[compaction input bounded; full transcript retained]\n"
        if char_budget <= len(marker):
            return text[:char_budget]
        side = (char_budget - len(marker)) // 2
        return text[:side] + marker + text[-side:]
