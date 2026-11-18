"""MOLECULAB API. Run: uvicorn app.main:app --reload  (docs at /docs)."""
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import assistant, services
from app.clients.crossref import CrossrefClient
from app.clients.gbif import GBIFClient
from app.clients.pubchem import PubChemClient
from app.clients.pubmed import PubMedClient
from app.auth import require_reviewer, reviews_enabled
from app.config import APP_VERSION
from app.db import get_session
from app.llm import LLMError, get_provider
from app.migrate import upgrade as run_migrations
from app.claims import classify_claim, extract_atomic_claims, matrix_table
from app.schemas import AssistantAnswer, Answerability, ClaimVerification, Question, ReviewIn, VerifyClaimIn
from app.verification import verify_claim
from app.models import (EVIDENCE_TYPE_LABELS, Compound, EvidenceRecord, EvidenceType, Plant, Publication,
                        ReviewRecord, ReviewStatus, VerificationStatus)

STATIC = Path(__file__).parent / "static"
DB = Annotated[Session, Depends(get_session)]


log = logging.getLogger("moleculab")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("database schema revision %s", run_migrations())
    yield


app = FastAPI(title="MOLECULAB API", version=APP_VERSION, lifespan=lifespan,
              description="Evidence-linked knowledge base for Central Asian medicinal-plant research. "
                          "Every plant-compound link cites a publication; evidence types are never merged.")


@app.middleware("http")
async def request_log(request: Request, call_next):
    rid = uuid.uuid4().hex[:8]
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    if request.url.path.startswith("/api"):
        log.info("%s %s %s -> %s %.0fms", rid, request.method, request.url.path, response.status_code,
                 (time.perf_counter() - start) * 1000)
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error; see server log."})


def _page(response: Response, total: int, offset: int, limit: int) -> None:
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Offset"] = str(offset)
    response.headers["X-Limit"] = str(limit)


def _404(obj, what: str):
    if obj is None:
        raise HTTPException(404, f"{what} not found")
    return obj


@app.get("/api/health")
def health(s: DB):
    s.execute(select(1))
    provider = "none"
    try:
        p = get_provider()
        provider = p.name if p else "none"
    except LLMError as exc:
        provider = f"misconfigured: {exc}"
    return {"status": "ok", "database": "ok", "llm_provider": provider, "reviews_enabled": reviews_enabled(),
            "version": app.version}


@app.get("/api/stats")
def stats(s: DB):
    count = lambda m: s.scalar(select(func.count()).select_from(m))
    by_type = dict(s.execute(select(EvidenceRecord.evidence_type, func.count()).group_by(EvidenceRecord.evidence_type)).all())
    by_status = dict(s.execute(select(EvidenceRecord.verification_status, func.count())
                               .group_by(EvidenceRecord.verification_status)).all())
    unresolved = s.scalar(select(func.count()).select_from(Compound).where(Compound.inchikey.is_(None)))
    return {"plants": count(Plant), "compounds": count(Compound), "publications": count(Publication),
            "evidence_records": count(EvidenceRecord), "evidence_by_type": by_type,
            "evidence_by_verification": by_status, "compounds_without_identifier": unresolved}


@app.get("/api/dashboard")
def dashboard(s: DB):
    """Research overview computed live from the database."""
    return services.dashboard(s)


@app.get("/api/graph")
def graph(s: DB, plant_id: int | None = Query(None, ge=1), compound_id: int | None = Query(None, ge=1)):
    """Knowledge graph: plant -> evidence -> compound, evidence -> publication."""
    if plant_id and s.get(Plant, plant_id) is None:
        raise HTTPException(404, "plant not found")
    if compound_id and s.get(Compound, compound_id) is None:
        raise HTTPException(404, "compound not found")
    return services.graph(s, plant_id, compound_id)


@app.get("/api/meta/evidence-types")
def evidence_types():
    return [{"value": t.value, "label": EVIDENCE_TYPE_LABELS[t]} for t in EvidenceType]


@app.get("/api/search")
def search(s: DB, q: str = Query(..., min_length=2, max_length=200)):
    return services.search(s, q)


@app.get("/api/plants")
def plants(s: DB):
    return [services.plant_summary(p) for p in s.scalars(select(Plant).order_by(Plant.accepted_name))]


@app.get("/api/plants/{plant_id}")
def plant(plant_id: int, s: DB):
    p = _404(s.get(Plant, plant_id), "plant")
    ev = services.list_evidence(s, plant_id=p.id)
    compounds = {}
    for e in ev:
        if e.compound:
            c = compounds.setdefault(e.compound.id, {**services.compound_summary(e.compound), "evidence_types": set()})
            c["evidence_types"].add(e.evidence_type)
    for c in compounds.values():
        c["evidence_types"] = sorted(c["evidence_types"])
    return {
        **services.plant_summary(p), "gbif_key": p.gbif_key, "taxonomy_source": p.taxonomy_source, "notes": p.notes,
        "aliases": [{"id": a.id, "name": a.name, "language": a.language, "script": a.script, "alias_type": a.alias_type,
                     "source": a.source, "verification_status": a.verification_status} for a in p.aliases],
        "compounds": sorted(compounds.values(), key=lambda c: c["name"]),
        "evidence": [services.evidence_dict(e) for e in ev],
        "gaps": services.plant_gaps(p),
    }


@app.get("/api/compounds")
def compounds(s: DB, response: Response, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    _page(response, s.scalar(select(func.count()).select_from(Compound)), offset, limit)
    return [services.compound_summary(c) for c in
            s.scalars(select(Compound).order_by(Compound.name).offset(offset).limit(limit))]


@app.get("/api/compounds/{compound_id}")
def compound(compound_id: int, s: DB):
    c = _404(s.get(Compound, compound_id), "compound")
    return {**services.compound_summary(c), "molecular_weight": c.molecular_weight, "smiles": c.smiles,
            "iupac_name": c.iupac_name, "synonyms": c.synonyms or [],
            "identifier_source": c.identifier_source, "identifier_retrieved_at": c.identifier_retrieved_at,
            "pubchem_url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{c.pubchem_cid}" if c.pubchem_cid else None,
            "structure_image": f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{c.pubchem_cid}/PNG"
            if c.pubchem_cid else None,
            "evidence": [services.evidence_dict(e) for e in services.list_evidence(s, compound_id=c.id)],
            "note": "Identifiers come from PubChem and identify the structure only; plant occurrence comes from the cited evidence."}


@app.get("/api/publications")
def publications(s: DB, response: Response, offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    _page(response, s.scalar(select(func.count()).select_from(Publication)), offset, limit)
    return [services.publication_summary(p) for p in
            s.scalars(select(Publication).order_by(Publication.year.desc(), Publication.id).offset(offset).limit(limit))]


@app.get("/api/publications/{publication_id}")
def publication(publication_id: int, s: DB):
    p = _404(s.get(Publication, publication_id), "publication")
    return {**services.publication_summary(p), "authors": p.authors, "abstract": p.abstract,
            "publication_types": p.publication_types, "metadata_source": p.metadata_source,
            "metadata_retrieved_at": p.metadata_retrieved_at,
            "evidence": [services.evidence_dict(e) for e in services.list_evidence(s, publication_id=p.id)]}


@app.get("/api/evidence")
def evidence(s: DB, plant_id: int | None = None, compound_id: int | None = None,
             evidence_type: EvidenceType | None = None, verification_status: VerificationStatus | None = None,
             year_from: int | None = None, year_to: int | None = None, language: str | None = None,
             q: str | None = None, response: Response = None, offset: int = Query(0, ge=0),
             limit: int = Query(200, ge=1, le=1000)):
    rows = services.list_evidence(
        s, plant_id=plant_id, compound_id=compound_id, evidence_type=evidence_type,
        verification_status=verification_status, year_from=year_from, year_to=year_to,
        language=language, q=q, limit=100000)
    _page(response, len(rows), offset, limit)
    return [services.evidence_dict(e) for e in rows[offset:offset + limit]]


@app.get("/api/evidence/{evidence_id}")
def evidence_one(evidence_id: int, s: DB):
    return services.evidence_dict(_404(s.get(EvidenceRecord, evidence_id), "evidence record"))


@app.get("/api/evidence/{evidence_id}/provenance")
def evidence_provenance(evidence_id: int, s: DB):
    """What has been checked for this record: source excerpt, interpretation, expert review."""
    e = _404(s.get(EvidenceRecord, evidence_id), "evidence record")
    return {"evidence_id": e.id, **services.provenance(e),
            "source": {"pmid": e.publication.pmid, "doi": e.publication.doi, "url": e.publication.url,
                       "location": e.source_location, "metadata_source": e.publication.metadata_source},
            "reviews": services.evidence_dict(e)["reviews"]}


@app.get("/api/evidence/{evidence_id}/reviews")
def list_reviews(evidence_id: int, s: DB):
    e = _404(s.get(EvidenceRecord, evidence_id), "evidence record")
    return {"evidence_id": e.id, "review_status": services.effective_review_status(e),
            "reviews": services.evidence_dict(e)["reviews"], "reviews_enabled": reviews_enabled()}


@app.post("/api/evidence/{evidence_id}/reviews", status_code=201)
def add_review(evidence_id: int, body: ReviewIn, s: DB, reviewer: str = Depends(require_reviewer)):
    """Requires a configured reviewer token. The reviewer name comes from the token, not the body."""
    e = _404(s.get(EvidenceRecord, evidence_id), "evidence record")
    r = ReviewRecord(evidence_record=e, review_status=body.review_status, review_scope=body.review_scope,
                     reviewer=reviewer, review_notes=body.review_notes)
    s.add(r)
    s.commit()
    log.info("review %s on evidence %s by %s", r.review_status, e.id, reviewer)
    return {"id": r.id, "evidence_record_id": e.id, "review_status": r.review_status,
            "review_scope": r.review_scope, "reviewer": r.reviewer}


def _provider():
    try:
        return get_provider()
    except LLMError as exc:
        raise HTTPException(503, f"LLM provider misconfigured: {exc}")


@app.post("/api/assistant", response_model=AssistantAnswer)
def ask(body: Question, s: DB, live_lookup: bool = Query(False, description="Fetch missing chemical properties "
                                                                       "from PubChem live (not stored)")):
    return assistant.answer(s, body.question, _provider(), live_lookup=live_lookup)


@app.post("/api/verify-claim", response_model=ClaimVerification)
def verify_one_claim(body: VerifyClaimIn, s: DB):
    """Verify ONE atomic claim against the evidence records it cites (or against PubChem/GBIF records for
    property/taxonomy claims). Compound sentences are rejected: split them with /api/extract-claims first."""
    atoms = extract_atomic_claims(body.claim)
    if len(atoms) > 1:
        raise HTTPException(422, {"error": "claim is not atomic", "atomic_claims": atoms})
    records = {r.id: r for r in s.scalars(select(EvidenceRecord))}
    return verify_claim(body.claim, body.evidence_ids, records=records, existing_ids=set(records),
                        allowed_ids=None, plants=list(s.scalars(select(Plant))),
                        compounds=list(s.scalars(select(Compound))), verifiers=assistant._verifiers(_provider()))


@app.post("/api/extract-claims")
def extract_claims(body: VerifyClaimIn):
    """Split text into atomic claims and classify each (no verification)."""
    return [{"statement": a, "category": classify_claim(a)} for a in extract_atomic_claims(body.claim)]


@app.get("/api/verification/matrix")
def verification_matrix():
    """The claim-evidence compatibility matrix used by the verifier."""
    return matrix_table()


@app.post("/api/assistant/answerability", response_model=Answerability)
def answerability(body: Question, s: DB):
    """The deterministic answerability decision only (no generation)."""
    return assistant.evaluate(s, body.question)[0]


def _source(res):
    if not res.ok:
        status = 404 if res.error == "not found" or res.error == "no GBIF match" else 502
        raise HTTPException(status, {"source": res.source, "error": res.error, "request_url": res.request_url})
    return {"data": res.data, "provenance": res.provenance()}


@app.get("/api/sources/pubchem")
def live_pubchem(name: str = Query(..., min_length=2, max_length=200)):
    """Live PubChem lookup. Identifies a structure; says nothing about plant occurrence."""
    return _source(PubChemClient().search_name(name))


@app.get("/api/sources/pubmed")
def live_pubmed(q: str = Query(..., min_length=3, max_length=300), retmax: int = Query(5, ge=1, le=20)):
    client = PubMedClient()
    found = client.search(q, retmax)
    if not found.ok:
        return _source(found)
    recs = client.fetch(found.data["pmids"])
    out = _source(recs)
    out["count"] = found.data["count"]
    return out


@app.get("/api/sources/crossref")
def live_crossref(doi: str = Query(..., min_length=7, max_length=200)):
    return _source(CrossrefClient().get_doi(doi))


@app.get("/api/sources/gbif")
def live_gbif(name: str = Query(..., min_length=3, max_length=200)):
    return _source(GBIFClient().match(name))


from app.research_api import router as research_router  # noqa: E402

app.include_router(research_router, prefix="/api/research")
app.include_router(research_router, prefix="/api/v1/research", include_in_schema=False)
from app.workspace_api import router as workspace_router  # noqa: E402

app.include_router(workspace_router, prefix="/api/workspaces")
app.include_router(workspace_router, prefix="/api/v1/workspaces", include_in_schema=False)
from app.repurposing_api import router as repurposing_router  # noqa: E402

app.include_router(repurposing_router, prefix="/api/v1/repurposing")
app.include_router(repurposing_router, prefix="/api/repurposing", include_in_schema=False)
@app.get("/api/v1/copilot/status", tags=["assistant"])
def copilot_status() -> dict:
    """Whether live language-model generation is actually available.

    Reported honestly so the interface can never imply that a model answered when none was called.
    Retrieval, claim extraction and claim-level verification run regardless of this status: they are
    deterministic and need no provider.
    """
    from app.config import settings
    from app.llm import LLMError, get_provider

    configured, model, reason = False, None, None
    try:
        provider = get_provider()
        if provider is None:
            reason = ("No language-model provider is configured (LLM_PROVIDER=none). MOLECULAB answers "
                      "from its own records only: retrieval, claim extraction and claim-level "
                      "verification all still run, but no text is generated by a model.")
        else:
            configured, model = True, getattr(provider, "model", None)
            if getattr(provider, "name", "") == "claude_cli":
                reason = ("Generation runs through the operator's locally installed Claude Code CLI. "
                          "This is real model generation, but it is a LOCAL development bridge: it "
                          "depends on that machine's own Claude Code login and is not available in a "
                          "public deployment. All tools are disabled for these calls, and the output "
                          "is still verified claim by claim against cited records.")
    except LLMError as exc:
        reason = f"A provider is configured but unavailable: {exc}"
    return {
        "model_generation_available": configured,
        "provider": settings.llm_provider if settings.llm_provider not in ("", "none") else None,
        "model": model,
        "reason": reason,
        "retrieval_available": True,
        "semantic_verifier": settings.semantic_verifier,
        "verifier_note": ("An LLM semantic verifier can only LOWER a claim verdict, never raise it."
                          if settings.semantic_verifier == "llm" else
                          "Claim verification is deterministic (rule-based span matching)."),
        "how_to_enable": ("Set LLM_PROVIDER=anthropic and LLM_API_KEY in the server-side .env, "
                          "install the optional 'anthropic' package, then restart the backend. "
                          "Credentials stay server-side and are never sent to the browser."),
    }


from app.molstudio_api import router as molstudio_router  # noqa: E402

app.include_router(molstudio_router, prefix="/api/v1/molecules")
app.include_router(molstudio_router, prefix="/api/molecules", include_in_schema=False)
from app.simulations_api import router as simulations_router  # noqa: E402

app.include_router(simulations_router, prefix="/api/v1/simulations")
from app.formulation_api import router as formulation_router  # noqa: E402

app.include_router(formulation_router, prefix="/api/v1/formulations")
from app.board_api import router as board_router  # noqa: E402

app.include_router(board_router, prefix="/api/v1/boards")

# Workstream routers. Each is optional at import time so a module still under development cannot
# take down the whole application; a missing one is reported by /api/health instead.
from app.bench_api import router as bench_router
from app.screening_api import router as screening_router  # noqa: E402

app.include_router(screening_router, prefix="/api/v1/screening")
# The Research Bench is the product's front door: it orchestrates the engines above
# rather than implementing any science of its own.
app.include_router(bench_router, prefix="/api/v1/bench")

OPTIONAL_ROUTERS = [
    ("app.elements_api", "/api/v1/elements"),
    ("app.discovery_api", "/api/v1/discovery"),
    ("app.drugs_api", "/api/v1/drugs"),
    ("app.bench_ai_api", "/api/v1/bench/ai"),
    ("app.workbench_api", "/api/v1/workbench"),
    ("app.materials_api", "/api/v1/materials-explorer"),
]
MODULE_STATUS: dict[str, str] = {}
for _module_name, _prefix in OPTIONAL_ROUTERS:
    try:
        _module = __import__(_module_name, fromlist=["router"])
        app.include_router(_module.router, prefix=_prefix)
        MODULE_STATUS[_prefix] = "available"
    except Exception as _exc:  # noqa: BLE001 - report, never crash the app
        MODULE_STATUS[_prefix] = f"unavailable: {type(_exc).__name__}: {_exc}"
        logging.getLogger("moleculab").warning("router %s unavailable: %s", _prefix, _exc)

if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")
