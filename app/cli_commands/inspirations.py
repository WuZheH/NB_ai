from __future__ import annotations

import json

import typer

from app.cli_commands.shared import register_commands
from app.services import inspiration_card_service
from app.services.inspiration_card_promotion_planner import plan_inspiration_card_promotion
from app.services.inspiration_card_service import CardSourceInput


def inspiration_card_create_command(
    title: str = typer.Option(..., "--title"),
    content: str = typer.Option(..., "--content"),
    created_by: str = typer.Option(..., "--created-by"),
    actor: str = typer.Option(..., "--actor"),
    source_doc_id: int | None = typer.Option(None, "--source-doc-id"),
    source_chunk_id: int | None = typer.Option(None, "--source-chunk-id"),
    source_gap_reason: str | None = typer.Option(None, "--source-gap-reason"),
    tag_id: list[int] | None = typer.Option(None, "--tag-id"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Create a candidate InspirationCard through the service layer."""
    sources = []
    if source_doc_id is not None or source_chunk_id is not None:
        sources.append(CardSourceInput(source_doc_id=source_doc_id, source_chunk_id=source_chunk_id))
    try:
        card = inspiration_card_service.create_card(
            title=title,
            content=content,
            created_by=created_by,
            actor=actor,
            sources=sources,
            tag_ids=list(tag_id or []),
            source_gap_reason=source_gap_reason,
            reason=reason,
        )
    except ValueError as exc:
        _raise_inspiration_card_error(exc)
    _echo_inspiration_card_write_result(card)


def inspiration_card_show_command(card_id: int = typer.Option(..., "--card-id")) -> None:
    """Show one InspirationCard with sources, tags, and events."""
    try:
        card = inspiration_card_service.get_card(card_id)
    except ValueError as exc:
        _raise_inspiration_card_error(exc)
    _echo_inspiration_card_detail(card)


def inspiration_card_list_command(
    status: str = typer.Option("candidate", "--status"),
    limit: int = typer.Option(50, "--limit", min=1),
) -> None:
    """List InspirationCards by DB lifecycle status."""
    try:
        cards = inspiration_card_service.list_cards_by_status(status=status, limit=limit)
    except ValueError as exc:
        _raise_inspiration_card_error(exc)
    if not cards:
        typer.echo("[]")
        return
    for card in cards:
        gap_marker = f"\tsource_gap_reason={card.source_gap_reason}" if card.source_gap_reason else ""
        typer.echo(
            f"card_id={card.card_id}\ttitle={card.title}\tstatus={card.status}\t"
            f"created_by={card.created_by}\tupdated_at={card.updated_at.isoformat()}{gap_marker}"
        )


def inspiration_card_confirm_command(
    card_id: int = typer.Option(..., "--card-id"),
    actor: str = typer.Option(..., "--actor"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Transition candidate -> user-confirmed."""
    _transition_inspiration_card(card_id=card_id, new_status="user-confirmed", actor=actor, reason=reason)


def inspiration_card_reject_command(
    card_id: int = typer.Option(..., "--card-id"),
    actor: str = typer.Option(..., "--actor"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Transition candidate -> rejected."""
    _transition_inspiration_card(card_id=card_id, new_status="rejected", actor=actor, reason=reason)


def inspiration_card_archive_command(
    card_id: int = typer.Option(..., "--card-id"),
    actor: str = typer.Option(..., "--actor"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Transition a valid current status to archived."""
    _transition_inspiration_card(card_id=card_id, new_status="archived", actor=actor, reason=reason)


def inspiration_card_supersede_command(
    card_id: int = typer.Option(..., "--card-id"),
    actor: str = typer.Option(..., "--actor"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Transition user-confirmed -> superseded."""
    _transition_inspiration_card(card_id=card_id, new_status="superseded", actor=actor, reason=reason)


def inspiration_card_delete_command(
    card_id: int = typer.Option(..., "--card-id"),
    actor: str = typer.Option(..., "--actor"),
    reason: str | None = typer.Option(None, "--reason"),
) -> None:
    """Transition archived or superseded cards to deleted."""
    _transition_inspiration_card(card_id=card_id, new_status="deleted", actor=actor, reason=reason)


def inspiration_card_plan_promotion_command(
    card_id: int = typer.Option(..., "--card-id"),
    target_type: str = typer.Option(..., "--target-type"),
    actor: str = typer.Option(..., "--actor"),
    promotion_reason: str = typer.Option(..., "--promotion-reason"),
    target_title: str | None = typer.Option(None, "--target-title"),
    target_description: str | None = typer.Option(None, "--target-description"),
    target_metadata_json: str | None = typer.Option(None, "--target-metadata-json"),
) -> None:
    """Plan a dry-run InspirationCard promotion without writing target objects."""
    target_metadata = _parse_target_metadata_json(target_metadata_json)
    if target_title is not None:
        target_metadata["target_title"] = target_title
    if target_description is not None:
        target_metadata["target_description"] = target_description

    try:
        card = inspiration_card_service.get_card(card_id)
    except ValueError:
        card = None

    plan = plan_inspiration_card_promotion(
        card=_promotion_card_input(card),
        target_type=target_type,
        actor=actor,
        promotion_reason=promotion_reason,
        target_metadata=target_metadata,
    )
    typer.echo(json.dumps(plan, ensure_ascii=False, sort_keys=True, default=str))
    if not plan.get("ok"):
        raise typer.Exit(code=1)


def _transition_inspiration_card(card_id: int, new_status: str, actor: str, reason: str | None) -> None:
    try:
        card = inspiration_card_service.transition_card_status(
            card_id=card_id,
            new_status=new_status,
            actor=actor,
            reason=reason,
        )
    except ValueError as exc:
        _raise_inspiration_card_error(exc)
    _echo_inspiration_card_write_result(card)


def _raise_inspiration_card_error(exc: ValueError) -> None:
    typer.echo(str(exc), err=True)
    raise typer.Exit(code=1) from exc


def _parse_target_metadata_json(value: str | None) -> dict[str, object]:
    if value is None:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"target_metadata_json must be a JSON object: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("target_metadata_json must be a JSON object.")
    return parsed


def _promotion_card_input(card: object | None) -> dict[str, object] | None:
    if card is None:
        return None
    card_input = {
        "id": card.card_id,
        "card_id": card.card_id,
        "title": card.title,
        "content": card.content,
        "status": card.status,
        "created_by": card.created_by,
        "source_gap_reason": card.source_gap_reason,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
        "sources": [
            {
                "source_id": source.source_id,
                "source_doc_id": source.source_doc_id,
                "source_chunk_id": source.source_chunk_id,
                "created_at": source.created_at,
            }
            for source in card.sources
        ],
        "tags": [
            {
                "tag_id": tag.tag_id,
                "tag_name": tag.tag_name,
                "tag_type": tag.tag_type,
                "created_at": tag.created_at,
            }
            for tag in card.tags
        ],
        "events": [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "actor": event.actor,
                "from_status": event.from_status,
                "to_status": event.to_status,
                "reason": event.reason,
                "created_at": event.created_at,
            }
            for event in card.events
        ],
    }
    if hasattr(card, "source_trace"):
        card_input["source_trace"] = getattr(card, "source_trace")
    return card_input


def _echo_inspiration_card_write_result(card: object) -> None:
    event = card.events[-1] if card.events else None
    typer.echo(f"card_id={card.card_id}")
    typer.echo(f"status={card.status}")
    if event is not None:
        typer.echo(
            f"event_id={event.event_id}\tevent_type={event.event_type}\tactor={event.actor}\t"
            f"from_status={event.from_status or ''}\tto_status={event.to_status or ''}"
        )


def _echo_inspiration_card_detail(card: object) -> None:
    typer.echo(f"card_id={card.card_id}")
    typer.echo(f"title={card.title}")
    typer.echo(f"content={card.content}")
    typer.echo(f"status={card.status}")
    typer.echo(f"created_by={card.created_by}")
    typer.echo(f"source_gap_reason={card.source_gap_reason or ''}")
    typer.echo(f"created_at={card.created_at.isoformat()}")
    typer.echo(f"updated_at={card.updated_at.isoformat()}")
    typer.echo("sources:")
    if not card.sources:
        typer.echo("  []")
    for source in card.sources:
        typer.echo(
            f"  source_id={source.source_id}\tsource_doc_id={source.source_doc_id or ''}\t"
            f"source_chunk_id={source.source_chunk_id or ''}\tcreated_at={source.created_at.isoformat()}"
        )
    typer.echo("tags:")
    if not card.tags:
        typer.echo("  []")
    for tag in card.tags:
        typer.echo(
            f"  binding_id={tag.binding_id}\ttag_id={tag.tag_id}\tname={tag.tag_name}\t"
            f"tag_type={tag.tag_type}\tcreated_at={tag.created_at.isoformat()}"
        )
    typer.echo("events:")
    if not card.events:
        typer.echo("  []")
    for event in card.events:
        typer.echo(
            f"  event_id={event.event_id}\tevent_type={event.event_type}\tactor={event.actor}\t"
            f"from_status={event.from_status or ''}\tto_status={event.to_status or ''}\t"
            f"reason={event.reason or ''}\tcreated_at={event.created_at.isoformat()}"
        )


def register_inspiration_card_commands(app: typer.Typer) -> None:
    register_commands(
        app,
        namespace="inspiration_cards",
        commands=(
            ("create", inspiration_card_create_command),
            ("show", inspiration_card_show_command),
            ("list", inspiration_card_list_command),
            ("confirm", inspiration_card_confirm_command),
            ("reject", inspiration_card_reject_command),
            ("archive", inspiration_card_archive_command),
            ("supersede", inspiration_card_supersede_command),
            ("delete", inspiration_card_delete_command),
            ("plan-promotion", inspiration_card_plan_promotion_command),
        ),
    )


__all__ = [
    name
    for name in globals()
    if name.startswith("inspiration_card_") or name == "register_inspiration_card_commands"
]
