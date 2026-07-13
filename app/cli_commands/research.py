from __future__ import annotations

import json

import typer

from app.cli_commands.shared import echo_relation, register_commands
from app.services.hypothesis_service import generate_hypothesis_dry_run
from app.services.research_copilot_service import (
    build_research_copilot_sections,
    run_research_copilot_dry_run,
)
from app.services.research_session_service import (
    build_research_session_sections,
    run_research_session_dry_run,
)


def generate_hypothesis_command(
    question: str,
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    limit: int = typer.Option(5, "--limit", min=1),
) -> None:
    """Phase 8A dry-run evidence preparation for a research question."""
    if not dry_run:
        raise typer.BadParameter("Phase 8A only supports --dry-run. LLM/API generation is not implemented.")
    try:
        report = generate_hypothesis_dry_run(question=question, limit=limit)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo("Phase 8A dry-run evidence report")
    typer.echo(f"research_question={report.research_question}")
    typer.echo("note=This command does not generate final innovation points or final research hypotheses.")

    typer.echo("evidence_chunks:")
    if not report.evidence_chunks:
        typer.echo("  []")
    for item in report.evidence_chunks:
        typer.echo(
            f"  chunk_id={item.chunk_id}\tdocument_title={item.document_title}\t"
            f"heading_path={item.heading_path}\tpdf_path={item.pdf_path or ''}\t"
            f"page={item.pdf_page_start or ''}\tchunk_tags={', '.join(item.chunk_tags) if item.chunk_tags else '[]'}"
        )
        typer.echo(f"    snippet={item.chunk_text_snippet}")

    typer.echo("related_notes:")
    if not report.related_notes:
        typer.echo("  []")
    for note in report.related_notes:
        typer.echo(
            f"  note_id={note.note_id}\ttitle={note.title}\tnote_type={note.note_type}\t"
            f"linked_chunk_ids={note.linked_chunk_ids}\tnote_tags={', '.join(note.note_tags) if note.note_tags else '[]'}"
        )
        typer.echo(f"    snippet={note.snippet}")

    typer.echo("related_tags:")
    if not report.related_tags:
        typer.echo("  []")
    for tag in report.related_tags:
        typer.echo(
            f"  tag_id={tag.tag_id}\tname={tag.name}\ttag_type={tag.tag_type}\t"
            f"description={tag.description or ''}"
        )

    typer.echo("related_relations:")
    if not report.related_relations:
        typer.echo("  []")
    for relation in report.related_relations:
        echo_relation(relation)

    typer.echo("evidence_gaps:")
    if not report.evidence_gaps:
        typer.echo("  []")
    for gap in report.evidence_gaps:
        typer.echo(f"  - {gap}")

    typer.echo("suggested_next_actions:")
    for step in report.suggested_next_actions:
        typer.echo(f"  - {step}")

    typer.echo("execution_flags:")
    typer.echo(f"  dry_run={report.dry_run}")
    typer.echo(f"  llm_called={report.llm_called}")
    typer.echo(f"  api_called={report.api_called}")
    typer.echo(f"  final_hypothesis_generated={report.final_hypothesis_generated}")


def research_session_command(
    question: str,
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    top_k: int = typer.Option(5, "--top-k", min=1),
    rerank: str = typer.Option("heuristic", "--rerank"),
    output_format: str = typer.Option("text", "--format"),
    verify: bool = typer.Option(False, "--verify/--no-verify"),
) -> None:
    """Phase 9B local Research Session dry-run based on the internal read library."""
    if not dry_run:
        raise typer.BadParameter("Phase 9B.1 only supports --dry-run. Research generation is not implemented.")
    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be text or json.")
    try:
        report = run_research_session_dry_run(question, top_k=top_k, dry_run=dry_run, rerank=rerank)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    sections = build_research_session_sections(report, evidence_limit=top_k)
    if output_format == "json":
        typer.echo(json.dumps(sections, ensure_ascii=False, indent=2))
        return
    _echo_research_session_text(sections)


def research_copilot_command(
    question: str,
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    top_k: int = typer.Option(5, "--top-k", min=1),
    rerank: str = typer.Option("heuristic", "--rerank"),
    output_format: str = typer.Option("text", "--format"),
    verify: bool = typer.Option(False, "--verify/--no-verify"),
    multi_candidate: bool = typer.Option(False, "--multi-candidate/--single-candidate"),
) -> None:
    """Phase 10B local Research Copilot dry-run with controlled candidate drafts."""
    if not dry_run:
        raise typer.BadParameter("Phase 10B.0 only supports --dry-run. Controlled Research Copilot generation is not enabled.")
    if output_format not in {"text", "json"}:
        raise typer.BadParameter("--format must be text or json.")
    try:
        report = run_research_copilot_dry_run(
            question,
            top_k=top_k,
            dry_run=dry_run,
            rerank=rerank,
            verify=verify,
            multi_candidate=multi_candidate,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    sections = build_research_copilot_sections(report)
    if output_format == "json":
        typer.echo(json.dumps(sections, ensure_ascii=False, indent=2))
        return
    _echo_research_copilot_text(sections)


def _echo_research_copilot_text(sections: dict[str, object]) -> None:
    question = sections["question"]
    readiness = sections["evidence_readiness"]
    safety_flags = sections["safety_flags"]

    typer.echo("Research Copilot dry-run report")
    typer.echo("question:")
    typer.echo(f"  research_question={question['research_question']}")
    typer.echo(f"  top_k={question['top_k']}")
    typer.echo(f"status={sections['status']}")
    typer.echo(f"verify={sections['verify']}")
    typer.echo(f"multi_candidate={sections['multi_candidate']}")

    typer.echo("evidence_readiness:")
    typer.echo(f"  ready_for_hypothesis_dry_run={readiness['ready_for_hypothesis_dry_run']}")
    typer.echo("  blocking_gaps=" + ("[]" if not readiness["blocking_gaps"] else ""))
    for gap in readiness["blocking_gaps"]:
        typer.echo(f"    - {gap}")
    typer.echo("  warning_gaps=" + ("[]" if not readiness["warning_gaps"] else ""))
    for gap in readiness["warning_gaps"]:
        typer.echo(f"    - {gap}")

    typer.echo("candidate_hypothesis_drafts:")
    drafts = sections["candidate_hypothesis_drafts"]
    if not drafts:
        typer.echo("  []")
    for draft in drafts:
        typer.echo(f"  hypothesis_id={draft['hypothesis_id']}")
        typer.echo(f"    core_idea={draft['core_idea']}")
        typer.echo(f"    target_problem={draft['target_problem']}")
        typer.echo(f"    supporting_evidence_ids={draft['supporting_evidence_ids']}")
        typer.echo(f"    supporting_note_ids={draft['supporting_note_ids']}")
        typer.echo(f"    supporting_relation_ids={draft['supporting_relation_ids']}")
        typer.echo(f"    expected_difference_from_existing_methods={draft['expected_difference_from_existing_methods']}")
        typer.echo(f"    minimum_validation_experiment={draft['minimum_validation_experiment']}")
        typer.echo(f"    confidence_level={draft['confidence_level']}")
        typer.echo("    risks:")
        for risk in draft["risks"]:
            typer.echo(f"      - {risk}")
        typer.echo("    missing_evidence:")
        for item in draft["missing_evidence"]:
            typer.echo(f"      - {item}")

    if sections["verify"]:
        typer.echo("candidate_verifications:")
        verifications = sections["candidate_verifications"]
        if not verifications:
            typer.echo("  []")
        for verification in verifications:
            typer.echo(f"  hypothesis_id={verification['hypothesis_id']}")
            typer.echo(f"    verification_status={verification['verification_status']}")
            typer.echo(f"    evidence_support_score={verification['evidence_support_score']}")
            typer.echo(f"    minimum_validation_experiment_check={verification['minimum_validation_experiment_check']}")
            typer.echo(f"    downgrade_to_next_action={verification['downgrade_to_next_action']}")
            typer.echo(f"    unsupported_claims={verification['unsupported_claims']}")
            typer.echo(f"    missing_evidence={verification['missing_evidence']}")
            typer.echo(f"    risk_flags={verification['risk_flags']}")

        typer.echo("verified_candidate_hypothesis_drafts:")
        verified = sections["verified_candidate_hypothesis_drafts"]
        if not verified:
            typer.echo("  []")
        for draft in verified:
            typer.echo(f"  - {draft['hypothesis_id']}")

        typer.echo("downgraded_candidates:")
        downgraded = sections["downgraded_candidates"]
        if not downgraded:
            typer.echo("  []")
        for item in downgraded:
            typer.echo(f"  - hypothesis_id={item['hypothesis_id']}")
            typer.echo(f"    verification_status={item['verification_status']}")
            typer.echo(f"    suggested_next_action={item['suggested_next_action']}")

        typer.echo("critic_summary:")
        for key, value in sections["critic_summary"].items():
            typer.echo(f"  {key}={value}")

    typer.echo("human_review_queue:")
    review_queue = sections["human_review_queue"]
    if not review_queue:
        typer.echo("  []")
    for item in review_queue:
        typer.echo(f"  review_id={item['review_id']}")
        typer.echo(f"    hypothesis_id={item['hypothesis_id']}")
        typer.echo(f"    review_status={item['review_status']}")
        typer.echo(f"    recommended_action={item['recommended_action']}")
        typer.echo(f"    review_reason={item['review_reason']}")
        typer.echo(f"    evidence_to_inspect={item['evidence_to_inspect']}")
        typer.echo("    required_human_checks:")
        for check in item["required_human_checks"]:
            typer.echo(f"      - {check}")

    typer.echo(f"final_hypothesis={sections['final_hypothesis']}")
    typer.echo("external_candidate_queries:")
    queries = sections["external_candidate_queries"]
    if not queries:
        typer.echo("  []")
    for query in queries:
        typer.echo(f"  - {query}")

    typer.echo("suggested_next_actions:")
    for step in sections["suggested_next_actions"]:
        typer.echo(f"  - {step}")

    typer.echo("safety_flags:")
    for key in [
        "dry_run",
        "llm_called",
        "api_called",
        "external_search_called",
        "external_llm_called",
        "final_hypothesis_generated",
    ]:
        typer.echo(f"  {key}={safety_flags[key]}")


def _echo_research_session_text(sections: dict[str, object]) -> None:
    question = sections["question"]
    retrieval_summary = sections["retrieval_summary"]
    readiness = sections["readiness_judgement"]
    external_candidate_section = sections["external_candidate_section"]
    safety_flags = sections["safety_flags"]

    typer.echo("Research Session dry-run report")
    typer.echo("question:")
    typer.echo(f"  research_question={question['research_question']}")
    typer.echo(f"  top_k={question['top_k']}")

    typer.echo("retrieval_summary:")
    typer.echo(f"  total_results={retrieval_summary['total_results']}")
    typer.echo(f"  high_confidence_count={retrieval_summary['high_confidence_count']}")
    typer.echo(f"  evidence_backed_count={retrieval_summary['evidence_backed_count']}")
    typer.echo(f"  tag_or_relation_supported_count={retrieval_summary['tag_or_relation_supported_count']}")
    typer.echo(f"  vector_index_available={retrieval_summary['vector_index_available']}")
    typer.echo(f"  degraded_reason={retrieval_summary['degraded_reason'] or ''}")

    typer.echo("evidence_summary:")
    evidence_summary = sections["evidence_summary"]
    if not evidence_summary:
        typer.echo("  []")
    for index, evidence in enumerate(evidence_summary, start=1):
        typer.echo(
            f"  [{index}] title={evidence['document_title']}\t"
            f"heading_path={evidence['heading_path']}\tpage={evidence['pdf_page_start'] or ''}"
        )
        typer.echo(
            f"      source_channels={', '.join(evidence['source_channels']) if evidence['source_channels'] else '[]'}\t"
            f"confidence={evidence['confidence']}\t"
            f"fusion_score={evidence['fusion_score']:.4f}\trerank_score={evidence['rerank_score']:.4f}"
        )
        typer.echo(
            f"      matched_terms={', '.join(evidence['matched_terms']) if evidence['matched_terms'] else '[]'}\t"
            f"tag_match_count={evidence['tag_match_count']}\t"
            f"related_note_count={evidence['related_note_count']}\t"
            f"relation_count={evidence['relation_count']}"
        )
        typer.echo(f"      snippet={evidence['snippet']}")

    typer.echo("related_notes:")
    related_notes = sections["related_notes"]
    if not related_notes:
        typer.echo("  []")
    for note in related_notes[:5]:
        typer.echo(f"  note_id={note['note_id']}\ttitle={note['title']}\tnote_type={note['note_type']}")

    typer.echo("related_tags:")
    related_tags = sections["related_tags"]
    if not related_tags:
        typer.echo("  []")
    for tag in related_tags[:8]:
        typer.echo(f"  tag_id={tag['tag_id']}\tname={tag['name']}\ttag_type={tag['tag_type']}")

    typer.echo("related_relations:")
    related_relations = sections["related_relations"]
    if not related_relations:
        typer.echo("  []")
    for relation in related_relations[:5]:
        typer.echo(
            f"  relation_id={relation['relation_id']}\ttype={relation['relation_type']}\t"
            f"evidence_chunk_id={relation['evidence_chunk_id'] or ''}"
        )

    typer.echo("evidence_gaps:")
    evidence_gaps = sections["evidence_gaps"]
    if not evidence_gaps:
        typer.echo("  []")
    for gap in evidence_gaps:
        typer.echo(f"  - {gap}")

    typer.echo("readiness_judgement:")
    typer.echo(f"  ready_for_hypothesis_dry_run={readiness['ready_for_hypothesis_dry_run']}")
    typer.echo("  blocking_gaps=" + ("[]" if not readiness["blocking_gaps"] else ""))
    for gap in readiness["blocking_gaps"]:
        typer.echo(f"    - {gap}")
    typer.echo("  warning_gaps=" + ("[]" if not readiness["warning_gaps"] else ""))
    for gap in readiness["warning_gaps"]:
        typer.echo(f"    - {gap}")

    typer.echo("external_candidate_section:")
    typer.echo(f"  enabled={external_candidate_section['enabled']}")
    typer.echo(f"  called={external_candidate_section['called']}")
    typer.echo(f"  degraded_reason={external_candidate_section['degraded_reason']}")
    typer.echo(f"  safety_note={external_candidate_section['safety_note']}")
    typer.echo("  candidate_queries:")
    if not external_candidate_section["candidate_queries"]:
        typer.echo("    []")
    for query, reason in zip(
        external_candidate_section["candidate_queries"],
        external_candidate_section["reasons"],
    ):
        typer.echo(f"    - query={query}")
        typer.echo(f"      reason={reason}")

    typer.echo("suggested_next_actions:")
    for step in sections["suggested_next_actions"]:
        typer.echo(f"  - {step}")

    typer.echo("safety_flags:")
    for key in [
        "dry_run",
        "llm_called",
        "api_called",
        "external_api_enabled",
        "external_search_called",
        "external_rerank_called",
        "external_llm_called",
        "final_hypothesis_generated",
        "privacy_mode",
    ]:
        typer.echo(f"  {key}={safety_flags[key]}")
    typer.echo("external_call_audit:")
    audit_records = safety_flags.get("external_call_audit") or []
    if not audit_records:
        typer.echo("  []")
    for audit in audit_records:
        typer.echo(
            f"  feature={audit.get('feature')}\taction={audit.get('action')}\t"
            f"provider={audit.get('provider') or ''}\tallowed={audit.get('allowed')}\t"
            f"called={audit.get('called')}\tdegraded_reason={audit.get('degraded_reason') or ''}"
        )


def register_research_commands(app: typer.Typer) -> None:
    register_commands(
        app,
        namespace="research",
        commands=(
            ("generate-hypothesis", generate_hypothesis_command),
            ("research-session", research_session_command),
            ("research-copilot", research_copilot_command),
        ),
    )


__all__ = [
    "generate_hypothesis_command",
    "register_research_commands",
    "research_copilot_command",
    "research_session_command",
]
