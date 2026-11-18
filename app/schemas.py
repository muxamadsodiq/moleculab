"""Pydantic response/request models for the API."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.claims import ClaimCategory, Verdict

from app.models import ReviewScope, ReviewStatus
from app.reasoning import AnswerStatus, QuestionType


class ClaimVerification(BaseModel):
    """One atomic claim and the result of verifying it against its cited evidence."""
    model_config = ConfigDict(extra="allow")
    statement: str
    category: ClaimCategory
    status: Verdict
    evidence_ids: list[int] = []
    source_excerpts: list[dict] = Field([], description="Exact excerpts of cited records with the matched span")
    structured_sources: list[dict] = Field([], description="PubChem/GBIF records for property/taxonomy claims")
    explanation: str
    limitations: list[str] = []
    expert_review_required: bool
    checks: list[dict] = Field([], description="Every deterministic and semantic check with pass/fail and detail")
    verifiers: list[str] = []


class Claim(ClaimVerification):
    support: str = Field(description="'full' (supported) or 'qualified' (partially supported)")


class RejectedClaim(ClaimVerification):
    reason: str


class VerifyClaimIn(BaseModel):
    claim: str = Field(min_length=3, max_length=600)
    evidence_ids: list[int] = Field([], max_length=50)


class Contradiction(BaseModel):
    plant: str
    compound: str
    evidence_ids: list[int]
    findings: list[dict]
    explanation: str


class Answerability(BaseModel):
    answerable: bool
    status: AnswerStatus
    reason: str
    question_type: QuestionType
    evidence_ids: list[int] = Field(description="Retrieved records able to support this type of answer")
    retrieved_evidence_ids: list[int]
    limitations: list[str]
    missing_evidence: list[str]
    contradictions: list[Contradiction]
    entities: dict


class AssistantAnswer(BaseModel):
    model_config = ConfigDict(extra="allow")
    question: str
    mode: str = Field(description="no_evidence | abstained | structured_record | retrieval_only | llm_grounded")
    question_type: QuestionType
    claim_verifications: list[ClaimVerification]
    hypothesis: ClaimVerification | None = None
    established_findings: list[Claim] = []
    verification_summary: dict[str, int]
    supporting_evidence: list[dict]
    missing_evidence: list[str]
    contradictions: list[Contradiction]
    llm_used: bool
    llm_provider: str | None = None
    llm_model: str | None = None
    answer: str = Field(description="Prose answer assembled only from validated claims, or the abstention")
    answerability: Answerability
    claims: list[Claim]
    rejected_claims: list[RejectedClaim]
    references: list[dict]
    limitations: list[str]
    message: str
    # v0.1 compatibility fields
    answerable: bool
    retrieved_evidence_ids: list[int]


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class ReviewIn(BaseModel):
    review_status: ReviewStatus
    review_scope: ReviewScope = ReviewScope.EXPERT
    review_notes: str | None = Field(None, max_length=4000)
