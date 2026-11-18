"""Scientific entities.

The unit of knowledge is the EvidenceRecord: one publication's statement about
a plant (and optionally a compound), with its excerpt, method and evidence
type. Evidence types are kept as distinct categories on purpose; nothing here
collapses them into a single confidence score.
"""
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceType(StrEnum):
    DIRECT_EXPERIMENTAL = "direct_experimental_identification"
    TENTATIVE = "tentative_identification"
    SECONDARY_SOURCE = "secondary_source_report"
    TRADITIONAL_USE = "traditional_use"
    COMPUTATIONAL = "computational_prediction"
    UNVERIFIED = "unverified_claim"
    # v0.3: categories for stronger evidence. No seed record uses them yet.
    EXPERIMENTAL_BIOACTIVITY = "experimental_bioactivity"  # measured activity (in vitro / in vivo)
    CLINICAL_STUDY = "clinical_study"  # human clinical data


EVIDENCE_TYPE_LABELS = {
    EvidenceType.DIRECT_EXPERIMENTAL: "Direct experimental identification",
    EvidenceType.TENTATIVE: "Tentative identification",
    EvidenceType.SECONDARY_SOURCE: "Secondary-source report",
    EvidenceType.TRADITIONAL_USE: "Traditional use (not proof of efficacy)",
    EvidenceType.COMPUTATIONAL: "Computational prediction (not an experimental result)",
    EvidenceType.UNVERIFIED: "Unverified claim",
    EvidenceType.EXPERIMENTAL_BIOACTIVITY: "Experimental bioactivity (model stated in record)",
    EvidenceType.CLINICAL_STUDY: "Clinical study in humans",
}


class ReviewScope(StrEnum):
    INTERPRETATION = "interpretation_review"  # checked the curator's reading of the source
    EXPERT = "expert_review"  # domain expert assessed the scientific claim


class VerificationStatus(StrEnum):
    # The excerpt was matched verbatim against the source text fetched from PubMed.
    EXCERPT_VERIFIED = "excerpt_verified"
    # The record exists but its excerpt has not been checked against the source.
    UNVERIFIED = "unverified"
    # The excerpt could not be found in the fetched source text.
    EXCERPT_MISMATCH = "excerpt_mismatch"


class ReviewStatus(StrEnum):
    PENDING_EXPERT_REVIEW = "pending_expert_review"
    EXPERT_CONFIRMED = "expert_confirmed"
    EXPERT_DISPUTED = "expert_disputed"
    REJECTED = "rejected"
    NEEDS_FULL_TEXT = "needs_full_text"


class InterpretationStatus(StrEnum):
    # Who read the source and decided the evidence type, plant part, method etc.
    NOT_INTERPRETED = "not_interpreted"
    AI_INTERPRETED = "ai_assisted_interpretation"
    CURATOR_INTERPRETED = "curator_interpretation"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Plant(TimestampMixin, Base):
    __tablename__ = "plant"
    id: Mapped[int] = mapped_column(primary_key=True)
    accepted_name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    authority: Mapped[str | None] = mapped_column(String(120))
    family: Mapped[str | None] = mapped_column(String(120))
    gbif_key: Mapped[int | None] = mapped_column(Integer)
    taxonomy_source: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)

    aliases: Mapped[list["PlantAlias"]] = relationship(back_populates="plant", cascade="all, delete-orphan")
    evidence: Mapped[list["EvidenceRecord"]] = relationship(back_populates="plant")


class PlantAlias(TimestampMixin, Base):
    __tablename__ = "plant_alias"
    __table_args__ = (UniqueConstraint("plant_id", "name", "language"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    language: Mapped[str] = mapped_column(String(8))  # la, en, ru, uz
    script: Mapped[str] = mapped_column(String(8), default="latn")  # latn, cyrl
    alias_type: Mapped[str] = mapped_column(String(30))  # synonym, common, vernacular
    source: Mapped[str | None] = mapped_column(String(300))
    verification_status: Mapped[str] = mapped_column(String(30), default=VerificationStatus.UNVERIFIED)

    plant: Mapped[Plant] = relationship(back_populates="aliases")


class Compound(TimestampMixin, Base):
    __tablename__ = "compound"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    pubchem_cid: Mapped[int | None] = mapped_column(Integer, index=True)
    inchikey: Mapped[str | None] = mapped_column(String(27), index=True)
    molecular_formula: Mapped[str | None] = mapped_column(String(80))
    molecular_weight: Mapped[float | None] = mapped_column(Float)
    smiles: Mapped[str | None] = mapped_column(Text)
    iupac_name: Mapped[str | None] = mapped_column(Text)
    synonyms: Mapped[list | None] = mapped_column(JSON)
    identifier_source: Mapped[str | None] = mapped_column(String(500))  # provenance of identifiers
    identifier_retrieved_at: Mapped[str | None] = mapped_column(String(40))

    evidence: Mapped[list["EvidenceRecord"]] = relationship(back_populates="compound")


class Publication(TimestampMixin, Base):
    __tablename__ = "publication"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[list] = mapped_column(JSON, default=list)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    journal: Mapped[str | None] = mapped_column(String(300))
    doi: Mapped[str | None] = mapped_column(String(200), unique=True, index=True)
    pmid: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    url: Mapped[str | None] = mapped_column(String(500))
    language: Mapped[str | None] = mapped_column(String(20))
    publication_types: Mapped[list | None] = mapped_column(JSON)
    abstract: Mapped[str | None] = mapped_column(Text)
    metadata_source: Mapped[str | None] = mapped_column(String(500))
    metadata_retrieved_at: Mapped[str | None] = mapped_column(String(40))

    evidence: Mapped[list["EvidenceRecord"]] = relationship(back_populates="publication")


class EvidenceRecord(TimestampMixin, Base):
    __tablename__ = "evidence_record"
    id: Mapped[int] = mapped_column(primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id"), index=True)
    compound_id: Mapped[int | None] = mapped_column(ForeignKey("compound.id"), index=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publication.id"), index=True)
    plant_part: Mapped[str | None] = mapped_column(String(120))
    geographic_origin: Mapped[str | None] = mapped_column(String(200))
    observation: Mapped[str] = mapped_column(Text)
    identification_method: Mapped[str | None] = mapped_column(Text)
    evidence_type: Mapped[str] = mapped_column(String(50), index=True)
    source_location: Mapped[str] = mapped_column(String(60))  # e.g. "abstract", "title"
    original_excerpt: Mapped[str] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(String(30), default=VerificationStatus.UNVERIFIED, index=True)
    interpretation_status: Mapped[str] = mapped_column(String(40), default=InterpretationStatus.NOT_INTERPRETED)
    interpreted_by: Mapped[str | None] = mapped_column(String(120))
    curator_notes: Mapped[str | None] = mapped_column(Text)

    plant: Mapped[Plant] = relationship(back_populates="evidence")
    compound: Mapped[Compound | None] = relationship(back_populates="evidence")
    publication: Mapped[Publication] = relationship(back_populates="evidence")
    reviews: Mapped[list["ReviewRecord"]] = relationship(back_populates="evidence_record",
                                                         cascade="all, delete-orphan")


class ReviewRecord(TimestampMixin, Base):
    __tablename__ = "review_record"
    id: Mapped[int] = mapped_column(primary_key=True)
    evidence_record_id: Mapped[int] = mapped_column(ForeignKey("evidence_record.id", ondelete="CASCADE"), index=True)
    review_status: Mapped[str] = mapped_column(String(40), default=ReviewStatus.PENDING_EXPERT_REVIEW)
    review_scope: Mapped[str] = mapped_column(String(40), default=ReviewScope.EXPERT)
    reviewer: Mapped[str] = mapped_column(String(120))  # identity from the authenticated reviewer token
    review_notes: Mapped[str | None] = mapped_column(Text)

    evidence_record: Mapped[EvidenceRecord] = relationship(back_populates="reviews")


# ---------------------------------------------------------------- v0.5 research workspace
# Workspace tables reference scientific entities by (resource_type, resource_ref) only, never by foreign
# key, so deleting a workspace can never delete or alter shared scientific records.

class WorkspaceStatus(StrEnum):
    PLANNING = "planning"
    ACTIVE = "active"
    UNDER_REVIEW = "under_review"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ResourceType(StrEnum):
    PLANT = "plant"
    COMPOUND = "compound"
    PUBLICATION = "publication"
    EVIDENCE = "evidence"
    CONTRADICTION = "contradiction"
    GAP = "gap"
    # Frozen snapshot of a repurposing evidence inventory (v1.0). Created only by the server running the
    # analysis; `resource_ref` is the analysis content fingerprint.
    REPURPOSING_ANALYSIS = "repurposing_analysis"


class NoteCategory(StrEnum):
    OBSERVATION = "observation"
    QUESTION = "question"
    METHODOLOGY = "methodology"
    INTERPRETATION = "interpretation"
    LIMITATION = "limitation"
    DECISION = "decision"


class HypothesisStatus(StrEnum):
    # investigation progress, never scientific proof
    PROPOSED = "proposed"
    UNDER_INVESTIGATION = "under_investigation"
    PRELIMINARY_SUPPORT = "supported_by_preliminary_evidence"
    CONTRADICTED = "contradicted"
    NEEDS_EXPERIMENT = "requires_experimental_validation"
    ABANDONED = "abandoned"


class CausalAttribution(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    UNTESTED = "untested"  # occurrence + material activity do not establish that the compound caused it
    SUPPORTED_BY_COMPOUND_EXPERIMENT = "supported_by_compound_level_experiment"
    NOT_SUPPORTED_BY_EXPERIMENT = "not_supported_by_compound_level_experiment"


class TaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ResearchWorkspace(TimestampMixin, Base):
    __tablename__ = "research_workspace"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(200))
    research_question: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    research_domain: Mapped[str | None] = mapped_column(String(120))
    scope: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default=WorkspaceStatus.PLANNING, index=True)
    status_before_archive: Mapped[str | None] = mapped_column(String(30))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class _WorkspaceChild:
    @staticmethod
    def ws_fk():
        return mapped_column(ForeignKey("research_workspace.id", ondelete="CASCADE"), index=True)


class WorkspaceResource(TimestampMixin, Base):
    __tablename__ = "workspace_resource"
    __table_args__ = (UniqueConstraint("workspace_id", "resource_type", "resource_ref"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = _WorkspaceChild.ws_fk()
    resource_type: Mapped[str] = mapped_column(String(30))
    resource_ref: Mapped[str] = mapped_column(String(200))
    label: Mapped[str] = mapped_column(String(300))
    researcher_note: Mapped[str | None] = mapped_column(Text)
    # computed intelligence items (gaps, contradictions) are recomputed from data; keep what was seen
    snapshot: Mapped[dict | None] = mapped_column(JSON)


class ResearchNote(TimestampMixin, Base):
    __tablename__ = "research_note"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = _WorkspaceChild.ws_fk()
    category: Mapped[str] = mapped_column(String(30))
    body: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str | None] = mapped_column(String(30))
    resource_ref: Mapped[str | None] = mapped_column(String(200))
    author: Mapped[str] = mapped_column(String(120))


class WorkspaceHypothesis(TimestampMixin, Base):
    __tablename__ = "workspace_hypothesis"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = _WorkspaceChild.ws_fk()
    origin: Mapped[str] = mapped_column(String(20))  # generated | researcher
    statement: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(40), default=HypothesisStatus.PROPOSED)
    causal_attribution: Mapped[str] = mapped_column(String(50), default=CausalAttribution.NOT_APPLICABLE)
    supporting_evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    contradicting_evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    alternative_explanations: Mapped[list] = mapped_column(JSON, default=list)
    validation_methods: Mapped[list] = mapped_column(JSON, default=list)
    generator_snapshot: Mapped[dict | None] = mapped_column(JSON)
    author: Mapped[str] = mapped_column(String(120))


class ResearchTask(TimestampMixin, Base):
    __tablename__ = "research_task"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = _WorkspaceChild.ws_fk()
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.OPEN, index=True)
    priority: Mapped[str] = mapped_column(String(10), default=TaskPriority.MEDIUM)
    due_date: Mapped[str | None] = mapped_column(String(10))  # ISO date
    related_resources: Mapped[list] = mapped_column(JSON, default=list)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkspaceActivity(Base):
    __tablename__ = "workspace_activity"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = _WorkspaceChild.ws_fk()
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ResearchReport(Base):
    __tablename__ = "research_report"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = _WorkspaceChild.ws_fk()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    generated_by: Mapped[str] = mapped_column(String(120))
    markdown: Mapped[str] = mapped_column(Text)
    content: Mapped[dict] = mapped_column(JSON)
    dataset_fingerprint: Mapped[dict] = mapped_column(JSON)


# ===========================================================================
# MOLECULAB 1.0 - drug discovery intelligence entities
#
# Design rules for everything below:
#
# * **Provenance is mandatory.** Every imported record carries which provider it came from, that
#   provider's own record id, the request URL, when it was retrieved, the provider's release/version
#   and a hash of the raw payload. A stored value can always be traced to the response it came from.
# * **Provider data stays distinguishable from MOLECULAB data.** Provider-computed properties keep
#   provider-prefixed names; MOLECULAB's locally computed RDKit descriptors live in their own column.
# * **Experimental, computational and researcher-authored records never share a column.**
#   ActivityMeasurement holds measurements only. Predictions live in their own tables, so nothing can
#   be promoted from a prediction to a measurement by an UPDATE.
# * **No new foreign key deletes a curated scientific record.** MolecularStructure may reference an
#   existing Compound, but with SET NULL in both directions of effect: deleting discovery data can
#   never remove a plant/compound/publication/evidence record.
# ===========================================================================


class ProvenanceMixin:
    """Where a record came from. Required on every externally sourced entity."""
    provider: Mapped[str | None] = mapped_column(String(40), index=True)
    provider_record_id: Mapped[str | None] = mapped_column(String(120), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[str | None] = mapped_column(String(40))
    source_release: Mapped[str | None] = mapped_column(String(80))
    raw_payload_hash: Mapped[str | None] = mapped_column(String(64))
    transformation_version: Mapped[str | None] = mapped_column(String(20))


class Disease(ProvenanceMixin, TimestampMixin, Base):
    """A disease or phenotype identified by an ontology id (EFO/MONDO/...).

    Ontology ids change between platform releases, so `ontology_id` records the id actually resolved
    and `source_release` the release it was resolved against.
    """
    __tablename__ = "disease"
    id: Mapped[int] = mapped_column(primary_key=True)
    ontology_id: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    synonyms: Mapped[list | None] = mapped_column(JSON)
    therapeutic_areas: Mapped[list | None] = mapped_column(JSON)
    db_xrefs: Mapped[list | None] = mapped_column(JSON)
    associated_target_count: Mapped[int | None] = mapped_column(Integer)


class BiologicalTarget(ProvenanceMixin, TimestampMixin, Base):
    """A biological target. Identifiers are resolved through providers, never guessed.

    `chembl_target_type` is retained because measurements against a PROTEIN COMPLEX or PROTEIN FAMILY
    are not measurements against a single protein, and the screening layer enforces that.
    """
    __tablename__ = "biological_target"
    __table_args__ = (UniqueConstraint("ensembl_id", name="uq_target_ensembl"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ensembl_id: Mapped[str | None] = mapped_column(String(40), index=True)
    chembl_target_id: Mapped[str | None] = mapped_column(String(40), index=True)
    gene_symbol: Mapped[str | None] = mapped_column(String(60), index=True)
    target_name: Mapped[str | None] = mapped_column(String(300))
    organism: Mapped[str | None] = mapped_column(String(120), index=True)
    chembl_target_type: Mapped[str | None] = mapped_column(String(40))
    uniprot_accessions: Mapped[list | None] = mapped_column(JSON)
    target_classes: Mapped[list | None] = mapped_column(JSON)
    function_descriptions: Mapped[list | None] = mapped_column(JSON)
    synonyms: Mapped[list | None] = mapped_column(JSON)
    # Curated safety findings. An EMPTY list means none is recorded, NOT that the target is safe.
    safety_liabilities: Mapped[list | None] = mapped_column(JSON)
    tractability: Mapped[list | None] = mapped_column(JSON)


class TargetDiseaseEvidence(ProvenanceMixin, TimestampMixin, Base):
    """One target-disease association with its per-datatype breakdown preserved.

    `overall_score` and `datatype_scores` are the provider's evidence-aggregation scores: a
    prioritisation aid, NOT a probability of drug success. `score_meaning` keeps that caveat inside
    the row so an exported table cannot lose it.
    """
    __tablename__ = "target_disease_evidence"
    __table_args__ = (UniqueConstraint("disease_id", "target_id", "provider", name="uq_tde_pair"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    disease_id: Mapped[int] = mapped_column(ForeignKey("disease.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("biological_target.id", ondelete="CASCADE"),
                                           index=True)
    overall_score: Mapped[float | None] = mapped_column(Float)
    datatype_scores: Mapped[dict | None] = mapped_column(JSON)
    score_meaning: Mapped[str | None] = mapped_column(Text, default=(
        "Provider evidence-aggregation score for prioritisation. Not a probability of drug success "
        "and not a measure of treatment efficacy."))
    evidence_rows: Mapped[list | None] = mapped_column(JSON)
    evidence_row_count: Mapped[int | None] = mapped_column(Integer)

    disease: Mapped["Disease"] = relationship()
    target: Mapped["BiologicalTarget"] = relationship()


class MolecularStructure(ProvenanceMixin, TimestampMixin, Base):
    """A canonical chemical structure, identified by its standardized InChIKey.

    Submitted and standardized forms are both stored, with the full transformation log, so any
    difference between what was supplied and what is used for comparison can be audited.
    `descriptors` holds MOLECULAB's own RDKit values; provider-computed properties stay in
    `provider_properties` under provider-prefixed names and are never merged with them.
    """
    __tablename__ = "molecular_structure"
    __table_args__ = (UniqueConstraint("inchikey", name="uq_structure_inchikey"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    inchikey: Mapped[str] = mapped_column(String(27), index=True)
    skeleton_key: Mapped[str | None] = mapped_column(String(14), index=True)
    inchi: Mapped[str | None] = mapped_column(Text)
    standardized_smiles: Mapped[str] = mapped_column(Text)
    original_smiles: Mapped[str | None] = mapped_column(Text)
    input_format: Mapped[str | None] = mapped_column(String(20))
    input_value: Mapped[str | None] = mapped_column(Text)
    standardization_changed: Mapped[bool] = mapped_column(default=False)
    transformations: Mapped[list | None] = mapped_column(JSON)
    scaffold_smiles: Mapped[str | None] = mapped_column(Text, index=True)
    descriptors: Mapped[dict | None] = mapped_column(JSON)
    stereo_centers: Mapped[int | None] = mapped_column(Integer)
    unspecified_stereo_centers: Mapped[int | None] = mapped_column(Integer)
    has_defined_stereo: Mapped[bool | None] = mapped_column()
    chemistry_version: Mapped[str | None] = mapped_column(String(20))
    chembl_molecule_id: Mapped[str | None] = mapped_column(String(40), index=True)
    pubchem_cid: Mapped[int | None] = mapped_column(Integer, index=True)
    provider_properties: Mapped[dict | None] = mapped_column(JSON)
    # SET NULL on purpose: deleting discovery data must never remove a curated Compound.
    compound_id: Mapped[int | None] = mapped_column(
        ForeignKey("compound.id", ondelete="SET NULL"), index=True)


class Assay(ProvenanceMixin, TimestampMixin, Base):
    """Assay metadata, kept as its own entity so measurements can be grouped by the experiment that
    produced them and so assay type and confidence stay attached to every measurement."""
    __tablename__ = "assay"
    __table_args__ = (UniqueConstraint("chembl_assay_id", name="uq_assay_chembl"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    chembl_assay_id: Mapped[str] = mapped_column(String(40), index=True)
    assay_type: Mapped[str | None] = mapped_column(String(10), index=True)
    assay_type_description: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    assay_organism: Mapped[str | None] = mapped_column(String(120))
    confidence_score: Mapped[int | None] = mapped_column(Integer)
    confidence_description: Mapped[str | None] = mapped_column(String(300))
    relationship_type: Mapped[str | None] = mapped_column(String(20))
    bao_format: Mapped[str | None] = mapped_column(String(40))
    chembl_target_id: Mapped[str | None] = mapped_column(String(40), index=True)
    document_chembl_id: Mapped[str | None] = mapped_column(String(40))


class ActivityMeasurement(ProvenanceMixin, TimestampMixin, Base):
    """One EXPERIMENTAL activity measurement with its full experimental context.

    This table holds measurements only. There is deliberately no column for a predicted value, so a
    prediction cannot be written here and later read as if it had been measured.

    `standard_relation` is retained verbatim: `=` is an exact measurement, while `>`/`<`/`>=`/`<=`
    are censored observations. `is_exact` marks which, and only exact rows carry `p_activity`.
    """
    __tablename__ = "activity_measurement"
    id: Mapped[int] = mapped_column(primary_key=True)
    structure_id: Mapped[int] = mapped_column(
        ForeignKey("molecular_structure.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[int | None] = mapped_column(
        ForeignKey("biological_target.id", ondelete="SET NULL"), index=True)
    assay_id: Mapped[int | None] = mapped_column(ForeignKey("assay.id", ondelete="SET NULL"), index=True)
    chembl_activity_id: Mapped[int | None] = mapped_column(Integer, index=True)
    chembl_molecule_id: Mapped[str | None] = mapped_column(String(40), index=True)
    chembl_target_id: Mapped[str | None] = mapped_column(String(40), index=True)
    standard_type: Mapped[str] = mapped_column(String(40), index=True)
    standard_relation: Mapped[str | None] = mapped_column(String(8))
    standard_value: Mapped[float | None] = mapped_column(Float)
    standard_units: Mapped[str | None] = mapped_column(String(20))
    is_exact: Mapped[bool] = mapped_column(default=False, index=True)
    value_nm: Mapped[float | None] = mapped_column(Float)
    p_activity: Mapped[float | None] = mapped_column(Float, index=True)
    assay_type: Mapped[str | None] = mapped_column(String(10), index=True)
    target_organism: Mapped[str | None] = mapped_column(String(120), index=True)
    document_chembl_id: Mapped[str | None] = mapped_column(String(40))
    pchembl_value: Mapped[float | None] = mapped_column(Float)
    data_validity_comment: Mapped[str | None] = mapped_column(String(200))
    potential_duplicate: Mapped[bool] = mapped_column(default=False)
    measurement_type: Mapped[str] = mapped_column(String(20), default="experimental")

    structure: Mapped["MolecularStructure"] = relationship()
    target: Mapped["BiologicalTarget | None"] = relationship()
    assay: Mapped["Assay | None"] = relationship()


class ScreeningRun(TimestampMixin, Base):
    """One screening execution, recorded so it can be reproduced and audited.

    Stores the full parameter set, dataset snapshot counts, every software version involved, the
    inclusion/exclusion accounting from curation, the ranking method and a content fingerprint.
    Re-running with the same parameters on the same dataset reproduces the same fingerprint.
    """
    __tablename__ = "screening_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    executed_by: Mapped[str] = mapped_column(String(120))
    run_type: Mapped[str] = mapped_column(String(40), index=True)
    disease_id: Mapped[int | None] = mapped_column(ForeignKey("disease.id", ondelete="SET NULL"))
    target_id: Mapped[int | None] = mapped_column(
        ForeignKey("biological_target.id", ondelete="SET NULL"), index=True)
    parameters: Mapped[dict] = mapped_column(JSON)
    dataset_snapshot: Mapped[dict] = mapped_column(JSON)
    software_versions: Mapped[dict] = mapped_column(JSON)
    ranking_method: Mapped[str] = mapped_column(Text)
    inclusion_summary: Mapped[dict | None] = mapped_column(JSON)
    exclusion_reasons: Mapped[dict | None] = mapped_column(JSON)
    limitations: Mapped[list | None] = mapped_column(JSON)
    result_count: Mapped[int | None] = mapped_column(Integer)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)

    disease: Mapped["Disease | None"] = relationship()
    target: Mapped["BiologicalTarget | None"] = relationship()
    results: Mapped[list["ScreeningResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan")


class ScreeningResult(Base):
    """One ranked candidate from a screening run.

    Ranking dimensions are stored separately in `dimensions`, never collapsed into one opaque score.
    `evidence_refs` points at the measurement/assay/document records behind the row, and `basis`
    states in words what kind of evidence the row rests on, so a ranked table cannot be read without
    the distinction between a measurement and a structural similarity.
    """
    __tablename__ = "screening_result"
    __table_args__ = (UniqueConstraint("run_id", "structure_id", name="uq_result_run_structure"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("screening_run.id", ondelete="CASCADE"), index=True)
    structure_id: Mapped[int] = mapped_column(
        ForeignKey("molecular_structure.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer, index=True)
    dimensions: Mapped[dict] = mapped_column(JSON)
    basis: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[dict | None] = mapped_column(JSON)
    # Measured activity only. A screening result never carries a predicted value in this column.
    measured_p_activity: Mapped[float | None] = mapped_column(Float)
    measurement_count: Mapped[int | None] = mapped_column(Integer)

    run: Mapped["ScreeningRun"] = relationship(back_populates="results")
    structure: Mapped["MolecularStructure"] = relationship()


# ===========================================================================
# MOLECULAB — formulation research, constraints and experiment planning
#
# This layer is a RESEARCH RECORD SYSTEM, not a formulation predictor. MOLECULAB does not know
# whether a tablet will be hard enough or dissolve fast enough. What it does is:
#
#   * hold the researcher's own constraints, targets and acceptance ranges;
#   * record each measurement WITH its source, method, conditions and uncertainty;
#   * compute constraint status honestly, where a missing measurement is NOT a pass;
#   * surface trade-offs between constraints as general pharmaceutical principles or recorded
#     observations, never as compound-specific predictions;
#   * rank proposed experiments by how much unresolved constraint uncertainty they would remove.
#
# Nothing in these tables is generated by a model. Every number is either entered by the
# researcher, carried from a cited source, or computed by a named function from those inputs.
# ===========================================================================


class ConstraintCategory(StrEnum):
    """First-class formulation requirement categories (see the product brief, section 4.3)."""
    DOSAGE_FORM = "dosage_form"              # must be a tablet, defined dimensions, manufacturable
    MECHANICAL = "mechanical"                # hardness, friability, tensile strength
    DISINTEGRATION = "disintegration"        # break-up of the dosage form
    DISSOLUTION = "dissolution"              # the drug going into solution — NOT disintegration
    RELEASE_PROFILE = "release_profile"      # immediate / modified / extended
    SOLUBILITY = "solubility"
    PERMEABILITY = "permeability"
    BIOAVAILABILITY = "bioavailability"      # systemic availability; never inferred from a descriptor
    ACTIVE_INTEGRITY = "active_integrity"    # chemical identity of the active preserved
    STABILITY = "stability"                  # temperature, humidity, light, oxidation, pH
    COMPATIBILITY = "compatibility"          # active-excipient and excipient-excipient
    TOXICITY = "toxicity"
    PROCESSING = "processing"                # granulation, compression, drying, coating


class ConstraintPriority(StrEnum):
    MANDATORY = "mandatory"
    PREFERRED = "preferred"
    EXPLORATORY = "exploratory"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class ConstraintStatus(StrEnum):
    """Status of one constraint against its evidence.

    NOT_MEASURED is deliberately distinct from SATISFIED and from VIOLATED. A requirement with no
    measurement is an open question, never a pass.
    """
    SATISFIED = "satisfied"            # a measurement exists and falls in the acceptance range
    VIOLATED = "violated"              # a measurement exists and falls outside it
    UNCERTAIN = "uncertain"            # measured, but the uncertainty spans the acceptance boundary
    NOT_MEASURED = "not_measured"      # no measurement exists
    CONFLICTING = "conflicting"        # measurements disagree about whether it is met
    NOT_APPLICABLE = "not_applicable"


class MeasurementKind(StrEnum):
    """Where a value came from. Never collapsed into a single 'value' notion."""
    EXPERIMENTAL = "experimental"          # measured in a laboratory, by someone, somewhere
    LITERATURE = "literature"              # reported in a publication
    SUPPLIER_SPECIFICATION = "supplier_specification"
    COMPUTED = "computed"                  # calculated here from a structure (e.g. RDKit)
    PREDICTED = "predicted"                # output of a statistical model
    RESEARCHER_ESTIMATE = "researcher_estimate"   # a judgement, explicitly labelled as such


class MaterialRole(StrEnum):
    ACTIVE = "active_ingredient"
    FILLER = "filler_diluent"
    BINDER = "binder"
    DISINTEGRANT = "disintegrant"
    LUBRICANT = "lubricant"
    GLIDANT = "glidant"
    COATING = "coating"
    SOLVENT = "solvent"
    BUFFER = "buffer"
    PRESERVATIVE = "preservative"
    SURFACTANT = "surfactant"
    OTHER = "other"


class DosageForm(StrEnum):
    TABLET = "tablet"
    CAPSULE = "capsule"
    POWDER = "powder"
    GRANULES = "granules"
    ORAL_SOLUTION = "oral_solution"
    SUSPENSION = "suspension"
    EMULSION = "emulsion"
    CREAM = "cream"
    GEL = "gel"
    OINTMENT = "ointment"
    INJECTABLE = "injectable"
    MODIFIED_RELEASE = "modified_release"
    OTHER = "other"


class CompatibilityVerdict(StrEnum):
    """Absence of a negative finding is NOT compatibility; it is NOT_EVALUATED."""
    DEMONSTRATED_COMPATIBLE = "demonstrated_compatible"
    DEMONSTRATED_INCOMPATIBLE = "demonstrated_incompatible"
    PREDICTED_INTERACTION = "predicted_interaction"
    LITERATURE_CONCERN = "literature_concern"
    HYPOTHESIS = "hypothesis"
    NOT_EVALUATED = "not_evaluated"


class ExperimentStatus(StrEnum):
    PROPOSED = "proposed"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class Material(ProvenanceMixin, TimestampMixin, Base):
    """An actual or candidate formulation material: an active, an excipient, a solvent, a buffer.

    Materials are reusable across formulations. A material may optionally reference a
    MolecularStructure, but many excipients (e.g. microcrystalline cellulose) are polymeric or
    poorly defined as a single structure, so the link is optional by design.
    """
    __tablename__ = "material"
    __table_args__ = (UniqueConstraint("name", name="uq_material_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    role: Mapped[str] = mapped_column(String(40), index=True, default=MaterialRole.OTHER)
    description: Mapped[str | None] = mapped_column(Text)
    cas_number: Mapped[str | None] = mapped_column(String(20), index=True)
    function_notes: Mapped[str | None] = mapped_column(Text)
    # Optional links: a material may or may not be one defined small molecule.
    structure_id: Mapped[int | None] = mapped_column(
        ForeignKey("molecular_structure.id", ondelete="SET NULL"), index=True)
    compound_id: Mapped[int | None] = mapped_column(
        ForeignKey("compound.id", ondelete="SET NULL"), index=True)
    # Free-form physical properties, each entry carrying its own source. Not a schema of truths.
    properties: Mapped[list | None] = mapped_column(JSON)


class Formulation(TimestampMixin, Base):
    """One formulation candidate inside a research investigation.

    Several candidates may exist per workspace so they can be compared dimension by dimension.
    """
    __tablename__ = "formulation"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_workspace.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    research_question: Mapped[str | None] = mapped_column(Text)
    dosage_form: Mapped[str] = mapped_column(String(40), default=DosageForm.TABLET, index=True)
    intended_release: Mapped[str | None] = mapped_column(String(80))
    route_of_administration: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(120), default="local")

    components: Mapped[list["FormulationComponent"]] = relationship(
        back_populates="formulation", cascade="all, delete-orphan")
    constraints: Mapped[list["ResearchConstraint"]] = relationship(
        back_populates="formulation", cascade="all, delete-orphan")
    conditions: Mapped[list["ProcessingCondition"]] = relationship(
        back_populates="formulation", cascade="all, delete-orphan")


class FormulationComponent(TimestampMixin, Base):
    """A material in a formulation, with its amount. Amounts are what the researcher specified;
    MOLECULAB does not compute or suggest them."""
    __tablename__ = "formulation_component"
    __table_args__ = (UniqueConstraint("formulation_id", "material_id", "role",
                                       name="uq_component_once"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    formulation_id: Mapped[int] = mapped_column(
        ForeignKey("formulation.id", ondelete="CASCADE"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("material.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(40), default=MaterialRole.OTHER)
    amount: Mapped[float | None] = mapped_column(Float)
    amount_unit: Mapped[str | None] = mapped_column(String(20))   # mg, %w/w, mg/mL …
    amount_basis: Mapped[str | None] = mapped_column(String(40))  # per unit dose, per batch …
    rationale: Mapped[str | None] = mapped_column(Text)

    formulation: Mapped[Formulation] = relationship(back_populates="components")
    material: Mapped[Material] = relationship()


class ResearchConstraint(TimestampMixin, Base):
    """A requirement the formulation must meet, with its acceptance range and rationale.

    `status` is never stored as a judgement: it is recomputed from the attached measurements by
    app.formulation.evaluate_constraint, so it cannot drift away from the evidence.
    """
    __tablename__ = "research_constraint"
    id: Mapped[int] = mapped_column(primary_key=True)
    formulation_id: Mapped[int] = mapped_column(
        ForeignKey("formulation.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(200))
    priority: Mapped[str] = mapped_column(String(20), default=ConstraintPriority.MANDATORY, index=True)
    # What is measured, and what counts as acceptable.
    property_name: Mapped[str | None] = mapped_column(String(120))
    unit: Mapped[str | None] = mapped_column(String(30))
    min_value: Mapped[float | None] = mapped_column(Float)
    max_value: Mapped[float | None] = mapped_column(Float)
    target_value: Mapped[float | None] = mapped_column(Float)
    qualitative_target: Mapped[str | None] = mapped_column(Text)  # when no number applies
    rationale: Mapped[str | None] = mapped_column(Text)
    validation_method: Mapped[str | None] = mapped_column(Text)   # e.g. USP <701> disintegration
    notes: Mapped[str | None] = mapped_column(Text)

    formulation: Mapped[Formulation] = relationship(back_populates="constraints")
    measurements: Mapped[list["ConstraintMeasurement"]] = relationship(
        back_populates="constraint", cascade="all, delete-orphan")


class ConstraintMeasurement(TimestampMixin, Base):
    """One value bearing on one constraint, with the context that makes it meaningful.

    `kind` separates an experimental result from a literature value, a supplier specification, a
    computed descriptor, a model prediction and a researcher's estimate. These are never pooled.
    """
    __tablename__ = "constraint_measurement"
    id: Mapped[int] = mapped_column(primary_key=True)
    constraint_id: Mapped[int] = mapped_column(
        ForeignKey("research_constraint.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    value: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(Text)      # for qualitative observations
    unit: Mapped[str | None] = mapped_column(String(30))
    uncertainty: Mapped[float | None] = mapped_column(Float)  # +/- in the same unit
    n_replicates: Mapped[int | None] = mapped_column(Integer)
    method: Mapped[str | None] = mapped_column(Text)
    conditions: Mapped[dict | None] = mapped_column(JSON)     # temperature, humidity, medium, pH …
    # Provenance: where this came from. A publication, an internal run, a supplier sheet.
    source: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    evidence_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence_record.id", ondelete="SET NULL"), index=True)
    publication_id: Mapped[int | None] = mapped_column(
        ForeignKey("publication.id", ondelete="SET NULL"), index=True)
    recorded_by: Mapped[str] = mapped_column(String(120), default="local")
    limitations: Mapped[str | None] = mapped_column(Text)

    constraint: Mapped[ResearchConstraint] = relationship(back_populates="measurements")


class ProcessingCondition(TimestampMixin, Base):
    """A processing or environmental variable for a formulation: temperature, humidity, pressure,
    solvent, duration. Each carries its unit, whether it is a target or a measurement, and a source."""
    __tablename__ = "processing_condition"
    id: Mapped[int] = mapped_column(primary_key=True)
    formulation_id: Mapped[int] = mapped_column(
        ForeignKey("formulation.id", ondelete="CASCADE"), index=True)
    process_step: Mapped[str | None] = mapped_column(String(120))  # granulation, compression, drying
    parameter: Mapped[str] = mapped_column(String(120))            # temperature, pressure, pH …
    value: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(String(200))
    unit: Mapped[str | None] = mapped_column(String(30))
    is_target: Mapped[bool] = mapped_column(default=True)  # target/intended vs measured
    uncertainty: Mapped[float | None] = mapped_column(Float)
    physical_state: Mapped[str | None] = mapped_column(String(40))
    method: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)

    formulation: Mapped[Formulation] = relationship(back_populates="conditions")


class CompatibilityAssessment(TimestampMixin, Base):
    """An assessment of one material pair under stated conditions.

    The verdict vocabulary has no 'compatible by default': an unevaluated pair is NOT_EVALUATED, and
    the UI shows that as an open question rather than a green tick.
    """
    __tablename__ = "compatibility_assessment"
    __table_args__ = (UniqueConstraint("material_a_id", "material_b_id", "conditions_summary",
                                       name="uq_compat_pair_conditions"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    material_a_id: Mapped[int] = mapped_column(ForeignKey("material.id", ondelete="CASCADE"), index=True)
    material_b_id: Mapped[int] = mapped_column(ForeignKey("material.id", ondelete="CASCADE"), index=True)
    verdict: Mapped[str] = mapped_column(String(40), index=True, default=CompatibilityVerdict.NOT_EVALUATED)
    interaction_type: Mapped[str | None] = mapped_column(String(80))  # oxidation, Maillard, eutectic
    conditions_summary: Mapped[str] = mapped_column(String(200), default="unspecified")
    observation: Mapped[str | None] = mapped_column(Text)
    method: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    evidence_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence_record.id", ondelete="SET NULL"), index=True)
    recorded_by: Mapped[str] = mapped_column(String(120), default="local")

    material_a: Mapped[Material] = relationship(foreign_keys=[material_a_id])
    material_b: Mapped[Material] = relationship(foreign_keys=[material_b_id])


class ExperimentPlan(TimestampMixin, Base):
    """A PROPOSED experiment. `status` separates proposed from performed; results live in
    ConstraintMeasurement rows created after the fact, never invented here.

    Cost and duration are researcher estimates. MOLECULAB does not know laboratory prices and does
    not supply them.
    """
    __tablename__ = "experiment_plan"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_workspace.id", ondelete="CASCADE"), index=True)
    formulation_id: Mapped[int | None] = mapped_column(
        ForeignKey("formulation.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    research_question: Mapped[str | None] = mapped_column(Text)
    hypothesis: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default=ExperimentStatus.PROPOSED, index=True)
    independent_variables: Mapped[list | None] = mapped_column(JSON)
    dependent_variables: Mapped[list | None] = mapped_column(JSON)
    controlled_conditions: Mapped[list | None] = mapped_column(JSON)
    materials_required: Mapped[list | None] = mapped_column(JSON)
    equipment_required: Mapped[list | None] = mapped_column(JSON)
    measurement_methods: Mapped[list | None] = mapped_column(JSON)
    acceptance_criteria: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    limitations: Mapped[str | None] = mapped_column(Text)
    # Researcher-supplied estimates only.
    estimated_cost: Mapped[float | None] = mapped_column(Float)
    cost_currency: Mapped[str | None] = mapped_column(String(10))
    estimated_duration_hours: Mapped[float | None] = mapped_column(Float)
    estimate_basis: Mapped[str | None] = mapped_column(Text)
    # Which constraints this experiment would produce measurements for.
    addresses_constraint_ids: Mapped[list | None] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(120), default="local")


# ===========================================================================
# Research board — the researcher's visual workspace.
#
# A board object is a POINTER to a real record plus its placement, never a copy of the science.
# `ref_type` + `ref_id` identify the underlying row (compound, structure, evidence, formulation,
# constraint, experiment, disease, target, publication); `content` holds only board-local material
# such as a note's text or an equation. Opening an object navigates to its real record, so the
# board can never drift out of step with the data.
# ===========================================================================


class BoardObjectType(StrEnum):
    NOTE = "note"                       # researcher-authored text
    HYPOTHESIS = "hypothesis"           # unvalidated by definition
    EQUATION = "equation"               # a formula the researcher wrote
    COMPOUND = "compound"
    STRUCTURE = "molecular_structure"
    MATERIAL = "material"
    FORMULATION = "formulation"
    CONSTRAINT = "research_constraint"
    EXPERIMENT = "experiment_plan"
    EVIDENCE = "evidence_record"
    PUBLICATION = "publication"
    DISEASE = "disease"
    TARGET = "biological_target"
    SIMULATION = "simulation_result"
    IMAGE = "image"
    GROUP = "group"


class ResearchBoard(TimestampMixin, Base):
    """A board belongs to an investigation. Deleting a board never touches the records it points at."""
    __tablename__ = "research_board"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_workspace.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(120), default="local")
    #: Incremented on every save, so a board has a history even without full versioning.
    revision: Mapped[int] = mapped_column(Integer, default=1)

    objects: Mapped[list["ResearchBoardObject"]] = relationship(
        back_populates="board", cascade="all, delete-orphan")
    edges: Mapped[list["ResearchBoardEdge"]] = relationship(
        back_populates="board", cascade="all, delete-orphan")


class ResearchBoardObject(TimestampMixin, Base):
    """One object on a board: a pointer to a record, plus where it sits."""
    __tablename__ = "research_board_object"
    id: Mapped[int] = mapped_column(primary_key=True)
    board_id: Mapped[int] = mapped_column(
        ForeignKey("research_board.id", ondelete="CASCADE"), index=True)
    object_type: Mapped[str] = mapped_column(String(40), index=True)
    label: Mapped[str] = mapped_column(String(300))
    # Reference to the underlying record. Deliberately a loose (type, id) pair rather than a dozen
    # nullable foreign keys: the board must be able to point at any record type without a schema
    # change, and it must never cascade a delete into scientific data.
    ref_type: Mapped[str | None] = mapped_column(String(40), index=True)
    ref_id: Mapped[str | None] = mapped_column(String(80), index=True)
    #: Board-local content only (note text, equation source, image data URI, colour).
    content: Mapped[dict | None] = mapped_column(JSON)
    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[float] = mapped_column(Float, default=240.0)
    height: Mapped[float] = mapped_column(Float, default=140.0)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("research_board_object.id", ondelete="SET NULL"), index=True)

    board: Mapped[ResearchBoard] = relationship(back_populates="objects")


class ResearchBoardEdge(TimestampMixin, Base):
    """A researcher-drawn connection between two board objects.

    This is the researcher's own reasoning, not a provider-asserted relationship, and the API
    labels it that way so it is never confused with evidence from a database.
    """
    __tablename__ = "research_board_edge"
    id: Mapped[int] = mapped_column(primary_key=True)
    board_id: Mapped[int] = mapped_column(
        ForeignKey("research_board.id", ondelete="CASCADE"), index=True)
    from_object_id: Mapped[int] = mapped_column(
        ForeignKey("research_board_object.id", ondelete="CASCADE"), index=True)
    to_object_id: Mapped[int] = mapped_column(
        ForeignKey("research_board_object.id", ondelete="CASCADE"), index=True)
    label: Mapped[str | None] = mapped_column(String(200))
    relationship_kind: Mapped[str] = mapped_column(String(40), default="researcher_asserted")

    # ResearchBoard.edges declares back_populates="board"; without this side the mapper fails to
    # configure and every request that touches the ORM errors at startup.
    board: Mapped["ResearchBoard"] = relationship(back_populates="edges")
