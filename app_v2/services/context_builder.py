from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from app_v2.domain.decisions import DispatcherDecision
from app_v2.domain.enums import ScopeType
from app_v2.domain.events import EventEnvelope, SceneAnalysis
from app_v2.domain.personality import PersonalityState
from app_v2.repositories.message_repo import HotMessage
from app_v2.repositories.memory_repo import RankedMemory


def estimate_tokens(text: str) -> int:
    """Cheap deterministic budget estimate; avoids adding tokenizer dependency in MVP."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True)
class GenerationMemory:
    id: str
    memory_type: str
    summary: str
    confidence: float
    importance: float
    evidence: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class GenerationContext:
    scope_type: ScopeType
    scope_id: str
    event: dict[str, Any]
    scene: dict[str, Any]
    decision: dict[str, Any]
    personality: dict[str, Any]
    hot_messages: tuple[dict[str, Any], ...]
    memories: tuple[GenerationMemory, ...]
    target_user_id: str | None
    action_state: dict[str, Any]
    estimated_hot_tokens: int
    estimated_memory_tokens: int
    external_context: tuple[dict[str, Any], ...] = ()
    estimated_external_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scope_type"] = self.scope_type.value
        return data


class ContextBuilder:
    def __init__(
        self,
        *,
        message_repo: Any,
        retrieval_engine: Any,
        hot_max_messages: int = 100,
        hot_token_budget: int = 4000,
        memory_min_cards: int = 3,
        memory_max_cards: int = 8,
        memory_token_budget: int = 1200,
        external_token_budget: int = 8000,
    ) -> None:
        self.message_repo = message_repo
        self.retrieval_engine = retrieval_engine
        self.hot_max_messages = max(1, hot_max_messages)
        self.hot_token_budget = max(100, hot_token_budget)
        self.memory_min_cards = max(1, memory_min_cards)
        self.memory_max_cards = max(self.memory_min_cards, memory_max_cards)
        self.memory_token_budget = max(100, memory_token_budget)
        self.external_token_budget = max(500, external_token_budget)

    def mapper_context(
        self,
        event: EventEnvelope,
        *,
        limit: int = 12,
        char_budget: int = 3000,
    ) -> tuple[dict[str, Any], ...]:
        """Return small, replay-safe, trusted-local context for MemoryMapper.

        The messages table currently has no persisted forum topic id. For a
        threaded Telegram event we therefore fail closed to empty context
        rather than mix independent topics and pretend isolation is guaranteed.
        """
        if event.metadata.get("message_thread_id") is not None:
            return ()
        reader = getattr(self.message_repo, "recent_before_event", None)
        if reader is None:
            return ()
        try:
            rows = reader(
                event.scope_type,
                event.scope_id,
                before=event.occurred_at,
                before_message_id=event.message_id,
                boundary_event_id=event.event_id,
                limit=max(1, min(limit, 20)),
            )
        except Exception:
            return ()

        selected: list[dict[str, Any]] = []
        used = 0
        budget = max(400, min(char_budget, 6000))
        for item in reversed(rows):
            text = item.text.strip()
            if not text:
                continue
            # Bound individual old messages as well as the whole context. The
            # current event itself is supplied separately and is never clipped here.
            text = text[:800]
            cost = len(text) + 120
            if selected and used + cost > budget:
                break
            selected.append(
                {
                    "message_id": item.message_id,
                    "author_user_id": item.author_user_id,
                    "text": text,
                    "created_at": item.created_at.isoformat(),
                    "reply_to_message_id": item.reply_to_message_id,
                }
            )
            used += cost
            if len(selected) >= max(1, min(limit, 20)):
                break
        selected.reverse()
        return tuple(selected)

    def build(
        self,
        *,
        event: EventEnvelope,
        scene: SceneAnalysis,
        decision: DispatcherDecision,
        personality: PersonalityState,
        subject_keys: Iterable[str] = (),
        memory_usage: str = "assist",
        callback_fatigue_minutes: int = 60,
        action_state: dict[str, Any] | None = None,
        external_context: Iterable[dict[str, Any]] = (),
    ) -> GenerationContext:
        if not event.scope_id.strip():
            raise ValueError("event.scope_id must not be empty")

        degraded: dict[str, bool] = {}
        try:
            hot = self.message_repo.recent_for_scope(
                event.scope_type,
                event.scope_id,
                limit=self.hot_max_messages,
            )
            hot_messages, hot_tokens = self._fit_hot(hot)
        except Exception:
            # HOT history is optional context. A storage hiccup must not force a
            # Personal reply to invent history or crash the whole pipeline.
            hot_messages, hot_tokens = [], 0
            degraded["hot_messages_unavailable"] = True

        try:
            ranked = self.retrieval_engine.retrieve(
                event.scope_type,
                event.scope_id,
                usage=memory_usage,
                subject_keys=subject_keys,
                callback_fatigue_minutes=callback_fatigue_minutes,
                limit=self.memory_max_cards,
            )
            memories, memory_tokens = self._fit_memories(ranked)
        except Exception:
            # Fail closed on LONG memory: no callback claims are safer than a
            # fabricated memory. ResponseGenerator separately rejects a Group
            # callback with zero provided Memory Cards.
            memories, memory_tokens = [], 0
            degraded["memory_unavailable"] = True

        external_items, external_tokens = self._fit_external(external_context)

        effective_action_state = dict(action_state or {})
        if degraded:
            effective_action_state["_degraded_context"] = degraded

        return GenerationContext(
            scope_type=event.scope_type,
            scope_id=event.scope_id,
            event=event.model_dump(mode="json"),
            scene=scene.model_dump(mode="json"),
            decision=decision.model_dump(mode="json"),
            personality=personality.model_dump(mode="json"),
            hot_messages=tuple(hot_messages),
            memories=tuple(memories),
            target_user_id=decision.target_user_id,
            action_state=effective_action_state,
            estimated_hot_tokens=hot_tokens,
            estimated_memory_tokens=memory_tokens,
            external_context=tuple(external_items),
            estimated_external_tokens=external_tokens,
        )

    def _fit_external(
        self,
        items: Iterable[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        selected: list[dict[str, Any]] = []
        used = 0
        for raw in items:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            metadata_cost = estimate_tokens(
                " ".join(
                    str(item.get(key) or "")
                    for key in ("url", "final_url", "title", "source", "content_type")
                )
            ) + 32
            remaining = self.external_token_budget - used - metadata_cost
            if remaining <= 0:
                break
            content_cost = estimate_tokens(content)
            if content_cost > remaining:
                marker = "\n[…external context clipped…]"
                marker_cost = estimate_tokens(marker)
                payload_budget = remaining - marker_cost
                if payload_budget <= 0:
                    break
                clipped = content[: max(1, payload_budget * 4)]
                boundary = clipped.rfind("\n")
                if boundary < len(clipped) // 2:
                    boundary = clipped.rfind(" ")
                if boundary > 0:
                    clipped = clipped[:boundary]
                bounded = clipped.rstrip() + marker
                while clipped and estimate_tokens(bounded) > remaining:
                    overflow = estimate_tokens(bounded) - remaining
                    clipped = clipped[: max(0, len(clipped) - max(4, overflow * 4))]
                    bounded = clipped.rstrip() + marker
                if not clipped or estimate_tokens(bounded) > remaining:
                    break
                item["content"] = bounded
                item["truncated"] = True
                content_cost = estimate_tokens(bounded)
            selected.append(item)
            used += metadata_cost + content_cost
            if used >= self.external_token_budget:
                break
        return selected, used

    def _fit_hot(self, messages: list[HotMessage]) -> tuple[list[dict[str, Any]], int]:
        selected: list[dict[str, Any]] = []
        used = 0
        for item in reversed(messages):
            text = item.text.strip()
            cost = estimate_tokens(text) + 12
            if selected and used + cost > self.hot_token_budget:
                break
            if cost > self.hot_token_budget:
                text = text[-self.hot_token_budget * 4 :]
                cost = estimate_tokens(text)
            selected.append(
                {
                    "message_id": item.message_id,
                    "author_user_id": item.author_user_id,
                    "text": text,
                    "created_at": item.created_at.isoformat(),
                    "reply_to_message_id": item.reply_to_message_id,
                }
            )
            used += cost
            if len(selected) >= self.hot_max_messages:
                break
        selected.reverse()
        return selected, used

    def _fit_memories(self, ranked: list[RankedMemory]) -> tuple[list[GenerationMemory], int]:
        selected: list[GenerationMemory] = []
        used = 0
        for ranked_item in ranked[: self.memory_max_cards]:
            card = ranked_item.card
            evidence = tuple(
                {
                    "message_id": item.message_id,
                    "author_id": item.author_id,
                    "timestamp": item.timestamp.isoformat(),
                    "excerpt": item.excerpt,
                }
                for item in card.evidence[:3]
            )
            evidence_text = " ".join(str(item.get("excerpt", "")) for item in evidence)
            cost = estimate_tokens(card.summary) + estimate_tokens(evidence_text) + 18
            if selected and used + cost > self.memory_token_budget:
                break
            if cost > self.memory_token_budget:
                continue
            selected.append(
                GenerationMemory(
                    id=card.id,
                    memory_type=card.memory_type,
                    summary=card.summary,
                    confidence=card.confidence,
                    importance=card.importance,
                    evidence=evidence,
                )
            )
            used += cost
        return selected, used
