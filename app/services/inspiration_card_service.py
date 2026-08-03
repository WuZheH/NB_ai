from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models import (
    Document,
    InspirationCard,
    InspirationCardEvent,
    InspirationCardSource,
    InspirationCardTag,
    KnowledgeChunk,
    KnowledgeTag,
)


DB_STATUSES = {
    "candidate",
    "user-confirmed",
    "rejected",
    "archived",
    "superseded",
    "deleted",
}
DEFERRED_OR_INVALID_STATUSES = {"raw", "promising", "tested"}
SOURCE_GAP_REASONS = {
    "user_free_form_idea",
    "book_level_insight",
    "cross_document_synthesis",
    "source_not_yet_imported",
    "unsupported_personal_idea",
}
ALLOWED_TRANSITIONS = {
    ("candidate", "user-confirmed"),
    ("candidate", "rejected"),
    ("candidate", "archived"),
    ("user-confirmed", "archived"),
    ("user-confirmed", "superseded"),
    ("rejected", "archived"),
    ("archived", "deleted"),
    ("superseded", "archived"),
    ("superseded", "deleted"),
}
EVENT_CREATED = "created"
EVENT_STATUS_TRANSITION = "status_transition"

T = TypeVar("T")


@dataclass(frozen=True)
class CardSourceInput:
    source_doc_id: int | None = None
    source_chunk_id: int | None = None


@dataclass(frozen=True)
class InspirationCardSourceItem:
    source_id: int
    source_doc_id: int | None
    source_chunk_id: int | None
    created_at: datetime


@dataclass(frozen=True)
class InspirationCardTagItem:
    binding_id: int
    tag_id: int
    tag_name: str
    tag_type: str
    created_at: datetime


@dataclass(frozen=True)
class InspirationCardEventItem:
    event_id: int
    event_type: str
    actor: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    created_at: datetime


@dataclass(frozen=True)
class InspirationCardDetail:
    card_id: int
    title: str
    content: str
    status: str
    created_by: str
    source_gap_reason: str | None
    created_at: datetime
    updated_at: datetime
    sources: list[InspirationCardSourceItem]
    tags: list[InspirationCardTagItem]
    events: list[InspirationCardEventItem]


@dataclass(frozen=True)
class InspirationCardListItem:
    card_id: int
    title: str
    status: str
    created_by: str
    source_gap_reason: str | None
    created_at: datetime
    updated_at: datetime


def create_card(
    title: str,
    content: str,
    created_by: str,
    actor: str,
    sources: list[CardSourceInput] | None = None,
    tag_ids: list[int] | None = None,
    source_gap_reason: str | None = None,
    status: str = "candidate",
    reason: str | None = None,
    session: Session | None = None,
) -> InspirationCardDetail:
    def action(db: Session) -> InspirationCardDetail:
        clean_title = _required_text(title, "title")
        clean_content = _required_text(content, "content")
        clean_created_by = _required_text(created_by, "created_by")
        clean_actor = _required_text(actor, "actor")
        _validate_db_status(status)
        if status != "candidate":
            raise ValueError("create_card may only create candidate cards in Phase 12F.")
        clean_gap = _validate_source_gap_reason(source_gap_reason)
        clean_sources = list(sources or [])
        clean_tag_ids = list(tag_ids or [])
        if not clean_sources and clean_gap is None:
            raise ValueError("card requires at least one source link or source_gap_reason.")

        _validate_sources(db, clean_sources)
        _validate_tag_ids(db, clean_tag_ids)

        card = InspirationCard(
            title=clean_title,
            content=clean_content,
            status="candidate",
            created_by=clean_created_by,
            source_gap_reason=clean_gap,
        )
        db.add(card)
        db.flush()

        for source in clean_sources:
            db.add(
                InspirationCardSource(
                    card_id=card.id,
                    source_doc_id=source.source_doc_id,
                    source_chunk_id=source.source_chunk_id,
                )
            )
        for tag_id in clean_tag_ids:
            db.add(InspirationCardTag(card_id=card.id, tag_id=tag_id))

        db.add(
            InspirationCardEvent(
                card_id=card.id,
                event_type=EVENT_CREATED,
                actor=clean_actor,
                from_status=None,
                to_status="candidate",
                reason=reason,
            )
        )
        db.flush()
        return _detail_from_card_id(db, card.id)

    return _execute(action, session=session)


def transition_card_status(
    card_id: int,
    new_status: str,
    actor: str,
    reason: str | None = None,
    session: Session | None = None,
) -> InspirationCardDetail:
    def action(db: Session) -> InspirationCardDetail:
        clean_actor = _required_text(actor, "actor")
        _validate_db_status(new_status)
        card = db.get(InspirationCard, card_id)
        if card is None:
            raise ValueError(f"card_id does not exist: {card_id}")
        from_status = card.status
        _validate_transition(from_status, new_status, clean_actor)
        card.status = new_status
        card.updated_at = datetime.utcnow()
        db.add(
            InspirationCardEvent(
                card_id=card.id,
                event_type=EVENT_STATUS_TRANSITION,
                actor=clean_actor,
                from_status=from_status,
                to_status=new_status,
                reason=reason,
            )
        )
        db.flush()
        return _detail_from_card_id(db, card.id)

    return _execute(action, session=session)


def get_card(card_id: int, session: Session | None = None) -> InspirationCardDetail:
    def action(db: Session) -> InspirationCardDetail:
        if db.get(InspirationCard, card_id) is None:
            raise ValueError(f"card_id does not exist: {card_id}")
        return _detail_from_card_id(db, card_id)

    return _execute(action, session=session, readonly=True)


def list_cards_by_status(
    status: str,
    limit: int = 50,
    session: Session | None = None,
) -> list[InspirationCardListItem]:
    def action(db: Session) -> list[InspirationCardListItem]:
        _validate_db_status(status)
        rows = db.scalars(
            select(InspirationCard)
            .where(InspirationCard.status == status)
            .order_by(InspirationCard.id.desc())
            .limit(max(1, limit))
        ).all()
        return [_list_item(card) for card in rows]

    return _execute(action, session=session, readonly=True)


def list_candidate_cards(limit: int = 50, session: Session | None = None) -> list[InspirationCardListItem]:
    return list_cards_by_status("candidate", limit=limit, session=session)


def list_user_confirmed_cards(limit: int = 50, session: Session | None = None) -> list[InspirationCardListItem]:
    return list_cards_by_status("user-confirmed", limit=limit, session=session)


def _execute(action: Callable[[Session], T], session: Session | None, readonly: bool = False) -> T:
    if session is not None:
        return action(session)

    init_db()
    with SessionLocal() as db:
        try:
            result = action(db)
            if not readonly:
                db.commit()
            return result
        except Exception:
            db.rollback()
            raise


def _required_text(value: str, field_name: str) -> str:
    clean = (value or "").strip()
    if not clean:
        raise ValueError(f"{field_name} is required.")
    return clean


def _validate_db_status(status: str) -> None:
    if status in DEFERRED_OR_INVALID_STATUSES:
        raise ValueError(f"Invalid Phase 12F status: {status}")
    if status not in DB_STATUSES:
        raise ValueError(f"Unknown status: {status}")


def _validate_source_gap_reason(source_gap_reason: str | None) -> str | None:
    if source_gap_reason is None:
        return None
    clean = source_gap_reason.strip()
    if clean == "source_not_yet_linked":
        raise ValueError("source_not_yet_linked is not an active source_gap_reason value.")
    if clean not in SOURCE_GAP_REASONS:
        raise ValueError(f"Invalid source_gap_reason: {source_gap_reason}")
    return clean


def _validate_sources(db: Session, sources: list[CardSourceInput]) -> None:
    for source in sources:
        if source.source_doc_id is None and source.source_chunk_id is None:
            raise ValueError("source link requires source_doc_id or source_chunk_id.")
        if source.source_doc_id is not None and db.get(Document, source.source_doc_id) is None:
            raise ValueError(f"source_doc_id does not exist: {source.source_doc_id}")
        if source.source_chunk_id is not None and db.get(KnowledgeChunk, source.source_chunk_id) is None:
            raise ValueError(f"source_chunk_id does not exist: {source.source_chunk_id}")


def _validate_tag_ids(db: Session, tag_ids: list[int]) -> None:
    seen = set()
    for tag_id in tag_ids:
        if tag_id in seen:
            raise ValueError(f"duplicate tag_id: {tag_id}")
        seen.add(tag_id)
        if db.get(KnowledgeTag, tag_id) is None:
            raise ValueError(f"tag_id does not exist: {tag_id}")


def _validate_transition(from_status: str, to_status: str, actor: str) -> None:
    if from_status == "deleted":
        raise ValueError("deleted is terminal in Phase 12F.")
    if (from_status, to_status) not in ALLOWED_TRANSITIONS:
        raise ValueError(f"Transition not allowed in Phase 12F: {from_status} -> {to_status}")
    if from_status == "candidate" and to_status == "user-confirmed" and actor != "user":
        raise ValueError("candidate -> user-confirmed requires explicit user actor.")


def _detail_from_card_id(db: Session, card_id: int) -> InspirationCardDetail:
    card = db.scalar(
        select(InspirationCard)
        .where(InspirationCard.id == card_id)
        .options(
            selectinload(InspirationCard.sources),
            selectinload(InspirationCard.tags).selectinload(InspirationCardTag.tag),
            selectinload(InspirationCard.events),
        )
    )
    if card is None:
        raise ValueError(f"card_id does not exist: {card_id}")
    return InspirationCardDetail(
        card_id=card.id,
        title=card.title,
        content=card.content,
        status=card.status,
        created_by=card.created_by,
        source_gap_reason=card.source_gap_reason,
        created_at=card.created_at,
        updated_at=card.updated_at,
        sources=[
            InspirationCardSourceItem(
                source_id=source.id,
                source_doc_id=source.source_doc_id,
                source_chunk_id=source.source_chunk_id,
                created_at=source.created_at,
            )
            for source in sorted(card.sources, key=lambda item: item.id)
        ],
        tags=[
            InspirationCardTagItem(
                binding_id=tag.id,
                tag_id=tag.tag_id,
                tag_name=tag.tag.name,
                tag_type=tag.tag.tag_type,
                created_at=tag.created_at,
            )
            for tag in sorted(card.tags, key=lambda item: item.id)
        ],
        events=[
            InspirationCardEventItem(
                event_id=event.id,
                event_type=event.event_type,
                actor=event.actor,
                from_status=event.from_status,
                to_status=event.to_status,
                reason=event.reason,
                created_at=event.created_at,
            )
            for event in sorted(card.events, key=lambda item: item.id)
        ],
    )


def _list_item(card: InspirationCard) -> InspirationCardListItem:
    return InspirationCardListItem(
        card_id=card.id,
        title=card.title,
        status=card.status,
        created_by=card.created_by,
        source_gap_reason=card.source_gap_reason,
        created_at=card.created_at,
        updated_at=card.updated_at,
    )
