from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.zotero_inspiration import ZoteroMechanismReadinessDryRunResponse


InspirationType = Literal[
    "analogy",
    "method_seed",
    "problem_gap",
    "experiment_idea",
    "critique",
    "question",
    "ordinary_note",
]
MechanismConfidence = Literal["low", "medium", "high"]
MechanismSourceMode = Literal["note_led", "source_led", "joint_led", "unknown"]


class MechanismCoreVariable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    role: str
    grounding: str


class MechanismSourceDomainExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    original_problem: str
    how_it_works_in_source: str


class MechanismLinkedObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_name: str
    object_type: str
    link_reason: str
    evidence_chunk_ids: list[int] = Field(default_factory=list)


class MechanismTransferDirection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_domain: str
    why_transferable: str
    required_assumptions: list[str] = Field(default_factory=list)


class MechanismCandidateMethod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method_name: str
    core_idea: str
    expected_benefit: str
    required_assumptions: list[str] = Field(default_factory=list)


class MechanismResearchHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis: str
    measurable_prediction: str
    required_evidence: list[str] = Field(default_factory=list)


class MechanismExperimentHints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baselines: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    ablations: list[str] = Field(default_factory=list)
    expected_observations: list[str] = Field(default_factory=list)
    possible_negative_results: list[str] = Field(default_factory=list)


class MechanismFailureMode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failure_mode: str
    why_it_matters: str
    mitigation_or_test: str


class MechanismWritingAngle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: Literal["introduction", "method", "limitation", "related_work"]
    angle: str


class MechanismDraftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    should_generate_mechanism: bool
    inspiration_type: InspirationType
    source_mode: MechanismSourceMode = "unknown"
    user_note_contribution: str | None = None
    source_excerpt_contribution: str | None = None
    linked_object_contribution: str | None = None
    evidence_alignment: str | None = None
    source_balance_warnings: list[str] = Field(default_factory=list)
    mechanism_key: str | None
    mechanism_name_cn: str | None
    mechanism_name_en: str | None
    mechanism_type: str | None
    short_explanation: str | None
    long_explanation: str | None
    abstract_form: str | None
    core_variables: list[MechanismCoreVariable] = Field(default_factory=list)
    source_domain_explanation: MechanismSourceDomainExplanation | None
    linked_objects: list[MechanismLinkedObject] = Field(default_factory=list)
    transfer_principle: str | None
    transfer_directions: list[MechanismTransferDirection] = Field(default_factory=list)
    candidate_methods: list[MechanismCandidateMethod] = Field(default_factory=list)
    research_hypotheses: list[MechanismResearchHypothesis] = Field(default_factory=list)
    experiment_hints: MechanismExperimentHints
    failure_modes: list[MechanismFailureMode] = Field(default_factory=list)
    similar_mechanisms: list[Any] = Field(default_factory=list)
    opposite_mechanisms: list[Any] = Field(default_factory=list)
    writing_angles: list[MechanismWritingAngle] = Field(default_factory=list)
    evidence_use_summary: str
    evidence_chunk_ids: list[int] = Field(default_factory=list)
    source_inspiration_note_ids: list[str | int] = Field(default_factory=list)
    hallucination_guard_reason: str
    confidence: MechanismConfidence
    needs_user_review_reason: str
    review_status: Literal["pending"]


class MechanismDraftPromptDryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    readiness_report: ZoteroMechanismReadinessDryRunResponse
    include_prompt_text: bool = True
    include_expected_schema: bool = True


class MechanismDraftPromptDryRunResponse(BaseModel):
    status: Literal["OK", "BLOCKED"]
    blocked: bool
    blocked_reason: str | None = None
    prompt_text: str | None = None
    prompt_payload_json: dict[str, Any] | None = None
    expected_response_schema: dict[str, Any] | None = None
    db_write_performed: bool = False
    llm_called: bool = False
    mechanism_generated: bool = False
    knowledge_chunks_write_performed: bool = False
    lancedb_write_performed: bool = False


class MechanismDraftValidateDryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    readiness_report: ZoteroMechanismReadinessDryRunResponse
    candidate_response_json: dict[str, Any]


class MechanismDraftValidationReport(BaseModel):
    status: Literal["OK", "BLOCKED"]
    is_valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocked: bool
    parsed_response: dict[str, Any] | None = None
    db_write_performed: bool = False
    llm_called: bool = False
    mechanism_generated: bool = False
    knowledge_chunks_write_performed: bool = False
    lancedb_write_performed: bool = False
