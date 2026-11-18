"""Query logic shared by the API and the assistant."""
from collections import defaultdict

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.textnorm import fold
from app.models import (EVIDENCE_TYPE_LABELS, Compound, EvidenceRecord, EvidenceType, InterpretationStatus, Plant,
                        PlantAlias, Publication, ReviewStatus)


def _like(term: str) -> str:
    """LIKE pattern with the user's % and _ escaped, so they match literally (use with escape='\\')."""
    t = term.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{t}%"


def _latest(e: EvidenceRecord, scope: str):
    rs = [r for r in e.reviews if (r.review_scope or "expert_review") == scope]
    return max(rs, key=lambda r: (r.created_at, r.id)) if rs else None


def effective_review_status(e: EvidenceRecord) -> str:
    """Latest EXPERT review by an authenticated reviewer; 'pending_expert_review' when there is none."""
    r = _latest(e, "expert_review")
    return r.review_status if r else ReviewStatus.PENDING_EXPERT_REVIEW.value


def interpretation_review_status(e: EvidenceRecord) -> str:
    r = _latest(e, "interpretation_review")
    return r.review_status if r else "interpretation_not_reviewed"


def provenance(e: EvidenceRecord) -> dict:
    """What has actually been checked, step by step. Excerpt matching is NOT expert verification."""
    review = effective_review_status(e)
    interp_review = interpretation_review_status(e)
    meta_ok = "crossref DOI verified" in (e.publication.metadata_source or "")
    return {
        "publication_metadata": {"status": "metadata_verified" if meta_ok else "metadata_unverified",
                                 "meaning": "PubMed metadata retrieved and the DOI resolved in Crossref."
                                 if meta_ok else "Publication metadata has not been cross-checked."},
        "source_excerpt": {"status": e.verification_status,
                           "meaning": {"excerpt_verified": "Excerpt found verbatim (whitespace-normalised) in the "
                                                           "PubMed title/abstract. This checks the quotation only.",
                                       "excerpt_mismatch": "Excerpt NOT found in the stored source text.",
                                       "unverified": "Excerpt has not been checked against the source."}
                           .get(e.verification_status, "")},
        "interpretation": {"status": e.interpretation_status, "by": e.interpreted_by,
                           "meaning": "Evidence type, plant part and method were assigned by "
                                      + ("AI-assisted curation from the abstract." if e.interpretation_status ==
                                         InterpretationStatus.AI_INTERPRETED else "a human curator."
                                         if e.interpretation_status == InterpretationStatus.CURATOR_INTERPRETED
                                         else "nobody yet."),
                           "review_status": interp_review},
        "basis": "Assessment based on the PubMed title/abstract; the full text was not read.",
        "disputed_or_rejected": review in ("expert_disputed", "rejected") or interp_review in ("expert_disputed", "rejected"),
        "expert_review": {"status": review, "review_count": len([r for r in e.reviews
                                                                 if (r.review_scope or "expert_review") == "expert_review"]),
                          "meaning": {"pending_expert_review": "No domain expert has reviewed this record.",
                                      "expert_confirmed": "A domain expert confirmed this record.",
                                      "expert_disputed": "A domain expert disputed this record.",
                                      "rejected": "A domain expert rejected this record.",
                                      "needs_full_text": "A reviewer asked for a full-text check."}.get(review, "")},
    }


def evidence_dict(e: EvidenceRecord) -> dict:
    return {
        "id": e.id,
        "plant": {"id": e.plant.id, "accepted_name": e.plant.accepted_name},
        "compound": {"id": e.compound.id, "name": e.compound.name} if e.compound else None,
        "publication": {"id": e.publication.id, "title": e.publication.title, "year": e.publication.year,
                        "pmid": e.publication.pmid, "doi": e.publication.doi, "url": e.publication.url},
        "plant_part": e.plant_part,
        "geographic_origin": e.geographic_origin,
        "observation": e.observation,
        "identification_method": e.identification_method,
        "evidence_type": e.evidence_type,
        "evidence_type_label": EVIDENCE_TYPE_LABELS.get(EvidenceType(e.evidence_type), e.evidence_type),
        "source_location": e.source_location,
        "original_excerpt": e.original_excerpt,
        "verification_status": e.verification_status,
        "curator_notes": e.curator_notes,
        "interpretation_status": e.interpretation_status,
        "interpreted_by": e.interpreted_by,
        "review_status": effective_review_status(e),
        "provenance": provenance(e),
        "reviews": [{"id": r.id, "review_status": r.review_status, "review_scope": r.review_scope, "reviewer": r.reviewer,
                     "review_notes": r.review_notes, "created_at": r.created_at.isoformat()} for r in e.reviews],
    }


def _evidence_query():
    return select(EvidenceRecord).options(
        selectinload(EvidenceRecord.plant), selectinload(EvidenceRecord.compound),
        selectinload(EvidenceRecord.publication), selectinload(EvidenceRecord.reviews))


def list_evidence(s: Session, *, plant_id=None, compound_id=None, publication_id=None, evidence_type=None,
                  verification_status=None, year_from=None, year_to=None, language=None, q=None,
                  limit=200) -> list[EvidenceRecord]:
    stmt = _evidence_query().join(EvidenceRecord.publication)
    if plant_id:
        stmt = stmt.where(EvidenceRecord.plant_id == plant_id)
    if compound_id:
        stmt = stmt.where(EvidenceRecord.compound_id == compound_id)
    if publication_id:
        stmt = stmt.where(EvidenceRecord.publication_id == publication_id)
    if evidence_type:
        stmt = stmt.where(EvidenceRecord.evidence_type == evidence_type)
    if verification_status:
        stmt = stmt.where(EvidenceRecord.verification_status == verification_status)
    if year_from:
        stmt = stmt.where(Publication.year >= year_from)
    if year_to:
        stmt = stmt.where(Publication.year <= year_to)
    if language:
        stmt = stmt.where(func.lower(Publication.language) == language.lower())
    if q:
        stmt = stmt.where(or_(func.lower(EvidenceRecord.observation).like(_like(q), escape="\\"),
                              func.lower(EvidenceRecord.original_excerpt).like(_like(q), escape="\\")))
    return list(s.scalars(stmt.order_by(EvidenceRecord.id).limit(limit)))


def search(s: Session, q: str, limit: int = 20) -> dict:
    """Search plants (accepted names and aliases in any script), compounds (name, synonyms,
    identifiers), publications (title, DOI, PMID) and evidence observations."""
    term = q.strip()
    like = _like(term)
    # Matched in Python: SQLite's lower() is ASCII-only, which would miss Cyrillic and Uzbek names.
    key = fold(term)
    plants = [p for p in s.scalars(select(Plant).options(selectinload(Plant.aliases)).order_by(Plant.accepted_name))
              if key in fold(p.accepted_name) or key in fold(p.family or "")
              or any(key in fold(a.name) for a in p.aliases)][:limit]

    compound_filters = [func.lower(Compound.name).like(like, escape="\\"), func.lower(Compound.inchikey).like(like, escape="\\"),
                        func.lower(Compound.molecular_formula).like(like, escape="\\")]
    if term.isdigit():
        compound_filters.append(Compound.pubchem_cid == int(term))
    compounds = list(s.scalars(select(Compound).where(or_(*compound_filters)).limit(limit)))
    seen = {c.id for c in compounds}
    for c in s.scalars(select(Compound).where(Compound.synonyms.is_not(None))):
        if c.id not in seen and any(term.lower() in str(syn).lower() for syn in c.synonyms or []):
            compounds.append(c)

    pubs = s.scalars(select(Publication).where(or_(
        func.lower(Publication.title).like(like, escape="\\"), func.lower(Publication.doi).like(like, escape="\\"),
        Publication.pmid == term)).limit(limit)).all()
    ev = list_evidence(s, q=term, limit=limit)
    return {
        "query": q,
        "plants": [plant_summary(p) for p in plants],
        "compounds": [compound_summary(c) for c in compounds[:limit]],
        "publications": [publication_summary(p) for p in pubs],
        "evidence": [evidence_dict(e) for e in ev],
    }


def plant_summary(p: Plant) -> dict:
    return {"id": p.id, "accepted_name": p.accepted_name, "authority": p.authority, "family": p.family,
            "aliases": [a.name for a in p.aliases], "evidence_count": len(p.evidence)}


def compound_summary(c: Compound) -> dict:
    return {"id": c.id, "name": c.name, "pubchem_cid": c.pubchem_cid, "inchikey": c.inchikey,
            "molecular_formula": c.molecular_formula, "evidence_count": len(c.evidence)}


def publication_summary(p: Publication) -> dict:
    return {"id": p.id, "title": p.title, "year": p.year, "journal": p.journal, "pmid": p.pmid, "doi": p.doi,
            "url": p.url, "language": p.language, "evidence_count": len(p.evidence)}


STRONG = {EvidenceType.DIRECT_EXPERIMENTAL}


def plant_gaps(p: Plant) -> list[dict]:
    """Rule-based gaps. Each item names its rule and the records behind it."""
    gaps = []
    by_compound = defaultdict(list)
    for e in p.evidence:
        if e.compound:
            by_compound[e.compound].append(e)
    for comp, recs in sorted(by_compound.items(), key=lambda kv: kv[0].name):
        types = {r.evidence_type for r in recs}
        ids = [r.id for r in recs]
        if not types & STRONG:
            gaps.append({"rule": "no_direct_identification", "compound": comp.name, "evidence_ids": ids,
                         "detail": "Only tentative, secondary or computational evidence for this compound in this plant."})
        if len({r.publication_id for r in recs}) == 1:
            gaps.append({"rule": "single_source", "compound": comp.name, "evidence_ids": ids,
                         "detail": "Reported by one publication only in this corpus."})
        if not comp.inchikey:
            gaps.append({"rule": "unresolved_structure", "compound": comp.name, "evidence_ids": ids,
                         "detail": "No PubChem identifier/InChIKey resolved for this name."})
        origins = {r.geographic_origin for r in recs if r.geographic_origin}
        if len(origins) > 1 or any(r.curator_notes and "variation" in r.curator_notes.lower() for r in recs):
            gaps.append({"rule": "possible_variation", "compound": comp.name, "evidence_ids": ids,
                         "detail": "Reports differ by origin or quantity; compare conditions before drawing conclusions."})
    trad = [e for e in p.evidence if e.evidence_type == EvidenceType.TRADITIONAL_USE]
    if trad:
        gaps.append({"rule": "traditional_use_unvalidated", "compound": None, "evidence_ids": [e.id for e in trad],
                     "detail": "Traditional use is recorded; it is not evidence of efficacy."})
    if not any(a.language in ("uz", "ru") for a in p.aliases):
        gaps.append({"rule": "no_regional_names", "compound": None, "evidence_ids": [],
                     "detail": "No Uzbek or Russian name recorded."})
    unverified_aliases = [a.name for a in p.aliases if a.verification_status != "excerpt_verified"]
    if unverified_aliases:
        gaps.append({"rule": "uncited_names", "compound": None, "evidence_ids": [],
                     "detail": f"Names without a cited source: {', '.join(unverified_aliases)}."})
    pending = [e.id for e in p.evidence if effective_review_status(e) != ReviewStatus.EXPERT_CONFIRMED]
    if pending:
        gaps.append({"rule": "awaiting_expert_review", "compound": None, "evidence_ids": pending,
                     "detail": f"{len(pending)} of {len(p.evidence)} records have no expert confirmation."})
    return gaps


def dashboard(s: Session) -> dict:
    """Every number here is computed from the database at request time."""
    evidence = list(s.scalars(_evidence_query()))
    plants = list(s.scalars(select(Plant).options(selectinload(Plant.aliases), selectinload(Plant.evidence))
                            .order_by(Plant.accepted_name)))
    review_counts: dict[str, int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)
    by_interp: dict[str, int] = defaultdict(int)
    for e in evidence:
        review_counts[effective_review_status(e)] += 1
        by_type[e.evidence_type] += 1
        by_source[e.verification_status] += 1
        by_interp[e.interpretation_status] += 1
    coverage = []
    for p in plants:
        types: dict[str, int] = defaultdict(int)
        for e in p.evidence:
            types[e.evidence_type] += 1
        coverage.append({
            "id": p.id, "accepted_name": p.accepted_name, "family": p.family,
            "evidence_count": len(p.evidence), "evidence_by_type": dict(types),
            "compound_count": len({e.compound_id for e in p.evidence if e.compound_id}),
            "publication_count": len({e.publication_id for e in p.evidence}),
            "has_regional_names": any(a.language in ("uz", "ru") for a in p.aliases),
            "gap_count": len(plant_gaps(p)),
        })
    from app.models import ReviewRecord
    activity = [{"kind": "evidence_added", "at": e.created_at.isoformat(), "evidence_id": e.id,
                 "label": f"{e.plant.accepted_name}" + (f" · {e.compound.name}" if e.compound else "")}
                for e in sorted(evidence, key=lambda e: (e.created_at, e.id), reverse=True)[:6]]
    activity += [{"kind": "review", "at": r.created_at.isoformat(), "evidence_id": r.evidence_record_id,
                  "label": f"{r.review_status} by {r.reviewer}"}
                 for r in s.scalars(select(ReviewRecord).order_by(ReviewRecord.created_at.desc()).limit(6))]
    activity.sort(key=lambda a: a["at"], reverse=True)
    return {
        "totals": {"plants": len(plants), "compounds": s.scalar(select(func.count()).select_from(Compound)),
                   "publications": s.scalar(select(func.count()).select_from(Publication)),
                   "evidence_records": len(evidence),
                   "compounds_without_identifier": s.scalar(select(func.count()).select_from(Compound)
                                                            .where(Compound.inchikey.is_(None)))},
        "evidence_by_type": dict(by_type),
        "verification": {"source_excerpt": dict(by_source), "interpretation": dict(by_interp),
                         "expert_review": dict(review_counts)},
        "coverage": coverage,
        "recent_activity": activity[:8],
    }


def graph(s: Session, plant_id: int | None = None, compound_id: int | None = None) -> dict:
    """Plant -> Evidence -> Compound and Evidence -> Publication, built from stored relationships.
    Node ids are typed ('plant:1') so a compound shared by two plants appears once."""
    stmt = _evidence_query()
    if plant_id:
        stmt = stmt.where(EvidenceRecord.plant_id == plant_id)
    if compound_id:
        stmt = stmt.where(EvidenceRecord.compound_id == compound_id)
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(nid, kind, label, **extra):
        nodes.setdefault(nid, {"id": nid, "type": kind, "label": label, **extra})

    for e in s.scalars(stmt):
        ev = f"evidence:{e.id}"
        node(f"plant:{e.plant.id}", "plant", e.plant.accepted_name, ref_id=e.plant.id)
        node(ev, "evidence", f"#{e.id}", ref_id=e.id, evidence_type=e.evidence_type,
             review_status=effective_review_status(e))
        node(f"publication:{e.publication.id}", "publication", f"PMID {e.publication.pmid}",
             ref_id=e.publication.id, title=e.publication.title, year=e.publication.year)
        edges.append({"source": f"plant:{e.plant.id}", "target": ev, "relation": "has_evidence"})
        edges.append({"source": ev, "target": f"publication:{e.publication.id}", "relation": "reported_in"})
        if e.compound:
            node(f"compound:{e.compound.id}", "compound", e.compound.name, ref_id=e.compound.id)
            edges.append({"source": ev, "target": f"compound:{e.compound.id}", "relation": "concerns_compound"})
    if not plant_id and not compound_id:  # include entities with no evidence so they are visible, not hidden
        for p in s.scalars(select(Plant)):
            node(f"plant:{p.id}", "plant", p.accepted_name, ref_id=p.id)
        for c in s.scalars(select(Compound)):
            node(f"compound:{c.id}", "compound", c.name, ref_id=c.id)
    linked = {x for e in edges for x in (e["source"], e["target"])}
    for n in nodes.values():
        n["disconnected"] = n["id"] not in linked
    return {"nodes": list(nodes.values()), "edges": edges,
            "counts": {k: sum(1 for n in nodes.values() if n["type"] == k)
                       for k in ("plant", "compound", "publication", "evidence")}}
