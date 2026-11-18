"""Virtual screening: reproducible, evidence-backed candidate ranking.

What a screening run here is, and is not:

* It ranks compounds for which **real experimental measurements exist** against one target and one
  endpoint, optionally narrowed by structural similarity to a reference and by molecular property
  constraints.
* It is **not** docking, and it produces no simulated binding score. There is no docking
  implementation, no prepared protein structure and no ligand preparation in MOLECULAB, so no
  docking number is offered at all rather than offering a fake one.
* Structural similarity is reported as a **separate dimension** and never converted into, or
  presented as, activity.
* There is **no single opaque score**. Every ranking dimension is stored and explained, and
  `ranking_method` states in words how the order was produced.

Reproducibility: a run records its parameters, the dataset snapshot (counts and the provider's
release), every software version involved, the full curation inclusion/exclusion accounting, and a
content fingerprint over the ranked results. Re-running with the same parameters against the same
provider release reproduces the same fingerprint.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import rdkit
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activity import CONCENTRATION_ENDPOINTS, aggregate_by_compound, curate, dataset_profile
from app.clients.chembl import ChEMBLClient
from app.config import APP_VERSION
from app.models import (ActivityMeasurement, Assay, BiologicalTarget, Disease, MolecularStructure,
                        ScreeningResult, ScreeningRun)
from app.molecules import (CHEMISTRY_VERSION, FINGERPRINT_SPEC, bulk_tanimoto, murcko_scaffold,
                           parse_smiles, resolve_structure)

log = logging.getLogger("moleculab")

#: Default upper bound on raw rows fetched. A screening run is an interactive operation; the cap keeps
#: it bounded, and the run record always states whether the cap was reached.
DEFAULT_MAX_RECORDS = 20_000
#: Default number of ranked candidates persisted. The dataset snapshot records the full counts.
DEFAULT_TOP_N = 100

PROPERTY_FILTERS = ("max_molecular_weight", "min_molecular_weight", "max_clogp", "min_clogp",
                    "max_tpsa", "max_h_bond_donors", "max_h_bond_acceptors", "max_rotatable_bonds")

RANKABLE = {
    "measured_p_activity": "descending p-activity (-log10 molar) of the median experimental measurement",
    "similarity": "descending Tanimoto similarity to the reference structure",
}


def software_versions() -> dict:
    return {"moleculab": APP_VERSION, "rdkit": rdkit.__version__,
            "chemistry_version": CHEMISTRY_VERSION, "fingerprint": dict(FINGERPRINT_SPEC)}


@dataclass
class ScreeningConfig:
    """Explicit screening inputs. Nothing is inferred."""
    chembl_target_id: str
    endpoint: str = "IC50"
    organism: str | None = "Homo sapiens"
    assay_type: str | None = "B"
    reference_smiles: str | None = None
    min_similarity: float | None = None
    min_p_activity: float | None = None
    property_filters: dict = field(default_factory=dict)
    rank_by: str = "measured_p_activity"
    top_n: int = DEFAULT_TOP_N
    max_records: int = DEFAULT_MAX_RECORDS
    exclude_censored: bool = True
    disease_ontology_id: str | None = None

    def validate(self) -> list[str]:
        problems = []
        if self.endpoint not in CONCENTRATION_ENDPOINTS:
            problems.append(f"endpoint '{self.endpoint}' is not a supported concentration endpoint; "
                            f"supported: {sorted(CONCENTRATION_ENDPOINTS)}")
        if self.rank_by not in RANKABLE:
            problems.append(f"rank_by '{self.rank_by}' unknown; supported: {sorted(RANKABLE)}")
        if self.rank_by == "similarity" and not self.reference_smiles:
            problems.append("rank_by='similarity' requires reference_smiles")
        if self.min_similarity is not None and not self.reference_smiles:
            problems.append("min_similarity requires reference_smiles")
        if self.reference_smiles:
            _, error = parse_smiles(self.reference_smiles)
            if error:
                problems.append(f"reference_smiles invalid: {error}")
        for key in self.property_filters:
            if key not in PROPERTY_FILTERS:
                problems.append(f"unknown property filter '{key}'; supported: {list(PROPERTY_FILTERS)}")
        if self.top_n < 1:
            problems.append("top_n must be >= 1")
        return problems

    def as_dict(self) -> dict:
        return {"chembl_target_id": self.chembl_target_id, "endpoint": self.endpoint,
                "organism": self.organism, "assay_type": self.assay_type,
                "reference_smiles": self.reference_smiles, "min_similarity": self.min_similarity,
                "min_p_activity": self.min_p_activity, "property_filters": dict(self.property_filters),
                "rank_by": self.rank_by, "top_n": self.top_n, "max_records": self.max_records,
                "exclude_censored": self.exclude_censored,
                "disease_ontology_id": self.disease_ontology_id}


class ScreeningError(Exception):
    """Raised when a run cannot be performed honestly (bad config, unusable target, no data)."""


# --------------------------------------------------------------------------- filters

def _passes_properties(descriptors: dict, filters: dict) -> tuple[bool, str | None]:
    checks = (
        ("max_molecular_weight", "molecular_weight", lambda v, t: v <= t, "MW > {t}"),
        ("min_molecular_weight", "molecular_weight", lambda v, t: v >= t, "MW < {t}"),
        ("max_clogp", "clogp", lambda v, t: v <= t, "cLogP > {t}"),
        ("min_clogp", "clogp", lambda v, t: v >= t, "cLogP < {t}"),
        ("max_tpsa", "tpsa", lambda v, t: v <= t, "TPSA > {t}"),
        ("max_h_bond_donors", "h_bond_donors", lambda v, t: v <= t, "HBD > {t}"),
        ("max_h_bond_acceptors", "h_bond_acceptors", lambda v, t: v <= t, "HBA > {t}"),
        ("max_rotatable_bonds", "rotatable_bonds", lambda v, t: v <= t, "rotatable bonds > {t}"),
    )
    for key, descriptor, ok, message in checks:
        if key in filters:
            value = descriptors.get(descriptor)
            if value is None:
                return False, f"{descriptor} unavailable"
            if not ok(value, filters[key]):
                return False, message.format(t=filters[key])
    return True, None


# ------------------------------------------------------------------------ persistence

def upsert_structure(session: Session, smiles: str, *, chembl_molecule_id: str | None = None,
                     provider: str | None = None, provenance: dict | None = None,
                     provider_properties: dict | None = None) -> MolecularStructure | None:
    """Store a structure by standardized InChIKey. Returns None for an invalid structure."""
    record = resolve_structure(smiles)
    if not record.valid or not record.inchikey:
        return None
    existing = session.scalar(select(MolecularStructure)
                              .where(MolecularStructure.inchikey == record.inchikey))
    if existing:
        if chembl_molecule_id and not existing.chembl_molecule_id:
            existing.chembl_molecule_id = chembl_molecule_id
        return existing
    prov = provenance or {}
    structure = MolecularStructure(
        inchikey=record.inchikey, skeleton_key=record.skeleton_key, inchi=record.inchi,
        standardized_smiles=record.standardized_smiles, original_smiles=record.original_smiles,
        input_format=record.input_format, input_value=record.input_value,
        standardization_changed=record.standardization_changed, transformations=record.transformations,
        scaffold_smiles=record.scaffold_smiles, descriptors=record.descriptors,
        stereo_centers=record.stereo_centers,
        unspecified_stereo_centers=record.unspecified_stereo_centers,
        has_defined_stereo=record.has_defined_stereo, chemistry_version=record.chemistry_version,
        chembl_molecule_id=chembl_molecule_id, provider_properties=provider_properties,
        provider=provider or prov.get("source"), provider_record_id=prov.get("provider_record_id"),
        source_url=prov.get("request_url"), retrieved_at=prov.get("retrieved_at"),
        source_release=prov.get("source_release"), raw_payload_hash=prov.get("raw_payload_hash"),
        transformation_version=prov.get("transformation_version"))
    session.add(structure)
    session.flush()
    return structure


def upsert_target(session: Session, chembl_data: dict, provenance: dict | None = None,
                  ensembl_id: str | None = None) -> BiologicalTarget:
    prov = provenance or {}
    existing = session.scalar(select(BiologicalTarget)
                              .where(BiologicalTarget.chembl_target_id == chembl_data["chembl_id"]))
    if existing:
        return existing
    target = BiologicalTarget(
        ensembl_id=ensembl_id, chembl_target_id=chembl_data["chembl_id"],
        gene_symbol=(chembl_data.get("gene_symbols") or [None])[0],
        target_name=chembl_data.get("pref_name"), organism=chembl_data.get("organism"),
        chembl_target_type=chembl_data.get("target_type"),
        uniprot_accessions=chembl_data.get("uniprot_accessions"),
        provider=prov.get("source", "chembl"), provider_record_id=chembl_data["chembl_id"],
        source_url=prov.get("request_url"), retrieved_at=prov.get("retrieved_at"),
        source_release=prov.get("source_release"), raw_payload_hash=prov.get("raw_payload_hash"),
        transformation_version=prov.get("transformation_version"))
    session.add(target)
    session.flush()
    return target


def upsert_assay(session: Session, chembl_assay_id: str, assay_type: str | None,
                 chembl_target_id: str | None, document_chembl_id: str | None,
                 source_release: str | None = None) -> Assay | None:
    if not chembl_assay_id:
        return None
    existing = session.scalar(select(Assay).where(Assay.chembl_assay_id == chembl_assay_id))
    if existing:
        return existing
    assay = Assay(chembl_assay_id=chembl_assay_id, assay_type=assay_type,
                  chembl_target_id=chembl_target_id, document_chembl_id=document_chembl_id,
                  provider="chembl", provider_record_id=chembl_assay_id,
                  source_release=source_release, transformation_version="1.0")
    session.add(assay)
    session.flush()
    return assay


# -------------------------------------------------------------------------- the run

def _fingerprint(parameters: dict, snapshot: dict, rows: list[dict]) -> str:
    """Content fingerprint over inputs and ranked output, excluding wall-clock time.

    Deliberately includes the provider release: the same parameters against a different ChEMBL
    release are a different run, and must not claim the same fingerprint.
    """
    body = json.dumps({
        "parameters": parameters,
        "source_release": snapshot.get("source_release"),
        "software": software_versions(),
        "results": [{"inchikey": r["inchikey"], "rank": r["rank"],
                     "dimensions": r["dimensions"]} for r in rows],
    }, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def run_screening(session: Session, config: ScreeningConfig, executed_by: str = "local",
                  client: ChEMBLClient | None = None, persist: bool = True) -> dict:
    """Execute one screening run and (optionally) persist it.

    Raises ScreeningError when the run cannot be performed honestly: an invalid configuration, a
    target that is not a single protein, or no usable measurements. It never returns an empty or
    fabricated ranking in those cases.
    """
    problems = config.validate()
    if problems:
        raise ScreeningError("; ".join(problems))

    chembl = client or ChEMBLClient()
    release = chembl.release()
    source_release = release.source_release if release.ok else None

    target_result = chembl.target(config.chembl_target_id)
    if not target_result.ok:
        raise ScreeningError(f"target {config.chembl_target_id} could not be retrieved: "
                             f"{target_result.error}")
    target_data = target_result.data
    target_type = target_data.get("target_type")

    # Measurements against a complex or family are not measurements against one protein.
    if target_type not in ("SINGLE PROTEIN",):
        raise ScreeningError(
            f"target {config.chembl_target_id} has target_type '{target_type}'. Screening requires a "
            "SINGLE PROTEIN target, because measurements against a complex or family aggregate "
            "different biological entities and are not comparable.")

    activities = chembl.activities(config.chembl_target_id, config.endpoint, relation=None,
                                  max_records=config.max_records)
    if not activities.ok and not activities.data:
        raise ScreeningError(f"activity download failed: {activities.error}")
    raw_rows = activities.data or []
    download_truncated = len(raw_rows) >= config.max_records
    partial_download_error = activities.error if not activities.ok else None

    profile = dataset_profile(raw_rows)
    curation = curate(raw_rows, config.endpoint, config.chembl_target_id,
                      organism=config.organism, assay_type=config.assay_type,
                      require_single_protein_target_type=target_type,
                      exclude_censored=config.exclude_censored)
    if not curation.included:
        raise ScreeningError(
            f"no comparable {config.endpoint} measurements remain after curation for "
            f"{config.chembl_target_id} (organism={config.organism}, assay_type={config.assay_type}). "
            f"Exclusions: {curation.exclusion_counts}")

    aggregated, unresolved = aggregate_by_compound(curation.included)

    # --- dimensions: activity, optional similarity, property compliance ---
    reference_mol = None
    if config.reference_smiles:
        reference_mol, _ = parse_smiles(config.reference_smiles)

    candidates, excluded_by_filter = [], {}
    mols, keep = [], []
    for row in aggregated:
        mol, _ = parse_smiles(row["smiles"] or "")
        if mol is None:
            excluded_by_filter["unparseable_structure"] = \
                excluded_by_filter.get("unparseable_structure", 0) + 1
            continue
        mols.append(mol)
        keep.append(row)

    similarities = bulk_tanimoto(reference_mol, mols) if reference_mol is not None else [None] * len(mols)

    from app.molecules import descriptors as compute_descriptors
    for row, mol, similarity in zip(keep, mols, similarities):
        if config.min_p_activity is not None and row["p_activity_median"] < config.min_p_activity:
            excluded_by_filter["below_min_p_activity"] = \
                excluded_by_filter.get("below_min_p_activity", 0) + 1
            continue
        if config.min_similarity is not None and (similarity or 0) < config.min_similarity:
            excluded_by_filter["below_min_similarity"] = \
                excluded_by_filter.get("below_min_similarity", 0) + 1
            continue
        desc = compute_descriptors(mol)
        if config.property_filters:
            ok, reason = _passes_properties(desc, config.property_filters)
            if not ok:
                key = f"property_filter:{reason}"
                excluded_by_filter[key] = excluded_by_filter.get(key, 0) + 1
                continue
        candidates.append({
            "molecule_chembl_id": row["molecule_chembl_id"],
            "smiles": row["smiles"],
            "scaffold": murcko_scaffold(mol),
            "dimensions": {
                # Each dimension is independent and labelled with what it is.
                "measured_p_activity": {
                    "value": row["p_activity_median"],
                    "kind": "experimental measurement (median of replicates)",
                    "endpoint": config.endpoint, "units": "p-activity (-log10 molar)"},
                "measurement_count": {"value": row["replicate_count"], "kind": "evidence volume"},
                "replicate_fold_spread": {"value": row["fold_spread"],
                                          "kind": "disagreement between replicate measurements"},
                **({"similarity": {
                    "value": similarity,
                    "kind": "structural similarity (Morgan/Tanimoto) to the reference",
                    "caveat": "structural similarity is not evidence of biological activity"}}
                   if similarity is not None else {}),
            },
            "descriptors": desc,
            "evidence_refs": {"assay_chembl_ids": row["assay_ids"],
                              "document_chembl_ids": row["document_ids"]},
            "measured_p_activity": row["p_activity_median"],
            "measurement_count": row["replicate_count"],
        })

    if not candidates:
        raise ScreeningError(
            f"all {len(aggregated)} compounds with comparable measurements were removed by the "
            f"configured filters: {excluded_by_filter}")

    key = (lambda c: c["dimensions"]["similarity"]["value"]) if config.rank_by == "similarity" \
        else (lambda c: c["measured_p_activity"])
    candidates.sort(key=lambda c: (key(c), c["measured_p_activity"]), reverse=True)
    ranked = candidates[:config.top_n]
    for position, candidate in enumerate(ranked, start=1):
        candidate["rank"] = position
        candidate["basis"] = (
            f"Ranked on {config.rank_by}. Experimental {config.endpoint} measurement(s) exist for this "
            f"compound against {config.chembl_target_id} ({target_data.get('organism')}, assay type "
            f"{config.assay_type}). " + ("Structural similarity to the reference is reported as a "
                                         "separate dimension and is not evidence of activity."
                                         if config.reference_smiles else ""))

    ranking_method = (
        f"Candidates are compounds with at least one exact, unit-convertible {config.endpoint} "
        f"measurement against {config.chembl_target_id} in {config.organism}, assay type "
        f"{config.assay_type}. Replicates are reduced by median p-activity; compounds whose replicates "
        f"disagree by more than 100-fold are excluded as unresolved rather than averaged. Ordering is by "
        f"{RANKABLE[config.rank_by]}. No composite score is computed and no prediction is involved.")

    limitations = [
        "Candidates are limited to compounds already measured against this target in ChEMBL; absence "
        "from this ranking is not evidence of inactivity.",
        f"Only {config.endpoint} measurements are used. Other endpoints are not pooled, so a compound "
        f"measured only by another endpoint does not appear.",
        "No docking or binding simulation is performed; MOLECULAB has no docking implementation, so no "
        "docking score is reported.",
        "Reported activity is experimental measurement, not prediction. No predicted value appears in "
        "this ranking.",
        f"{len(unresolved)} compound(s) were excluded because replicate measurements disagreed by more "
        f"than 100-fold; replicate disagreement of this size is a data-quality problem.",
    ]
    if config.reference_smiles:
        limitations.append("Structural similarity is a Morgan/Tanimoto comparison only and does not "
                           "imply shared biological activity or a shared mechanism.")
    if download_truncated:
        limitations.append(f"The activity download hit the configured cap of {config.max_records} rows, "
                           f"so the dataset may be incomplete.")
    if partial_download_error:
        limitations.append(f"The activity download was incomplete: {partial_download_error}")

    snapshot = {
        "source": "chembl", "source_release": source_release,
        "target_chembl_id": config.chembl_target_id, "target_type": target_type,
        "raw_profile": profile,
        "curation_summary": curation.summary(),
        "aggregated_compounds": len(aggregated),
        "unresolved_replicate_disagreement": len(unresolved),
        "candidates_after_filters": len(candidates),
        "download_truncated": download_truncated,
        "retrieved_at": activities.retrieved_at,
    }
    fingerprint_rows = [{"inchikey": None, "rank": c["rank"], "dimensions": c["dimensions"]}
                        for c in ranked]

    # Structures are resolved (and persisted) only for the ranked rows: standardizing thousands of
    # molecules per run is unnecessary when the snapshot already records the full counts.
    run_record = None
    stored = []
    if persist:
        disease = None
        if config.disease_ontology_id:
            disease = session.scalar(select(Disease)
                                     .where(Disease.ontology_id == config.disease_ontology_id))
        target_row = upsert_target(session, target_data, target_result.provenance())
        run_record = ScreeningRun(
            executed_by=executed_by,
            run_type="similarity_and_activity" if config.reference_smiles else "activity_filter",
            disease_id=disease.id if disease else None, target_id=target_row.id,
            parameters=config.as_dict(), dataset_snapshot=snapshot,
            software_versions=software_versions(), ranking_method=ranking_method,
            inclusion_summary=curation.summary(), exclusion_reasons={
                "curation": curation.exclusion_counts, "screening_filters": excluded_by_filter},
            limitations=limitations, result_count=len(ranked))
        session.add(run_record)
        session.flush()

        for candidate in ranked:
            structure = upsert_structure(
                session, candidate["smiles"], chembl_molecule_id=candidate["molecule_chembl_id"],
                provider="chembl",
                provenance={**activities.provenance(), "source_release": source_release})
            if structure is None:
                continue
            session.add(ScreeningResult(
                run_id=run_record.id, structure_id=structure.id, rank=candidate["rank"],
                dimensions=candidate["dimensions"], basis=candidate["basis"],
                evidence_refs=candidate["evidence_refs"],
                measured_p_activity=candidate["measured_p_activity"],
                measurement_count=candidate["measurement_count"]))
            candidate["inchikey"] = structure.inchikey
            stored.append(candidate)
        session.flush()
        fingerprint_rows = [{"inchikey": c.get("inchikey"), "rank": c["rank"],
                             "dimensions": c["dimensions"]} for c in stored]

    content_fingerprint = _fingerprint(config.as_dict(), snapshot, fingerprint_rows)
    if run_record is not None:
        run_record.content_fingerprint = content_fingerprint
        session.flush()

    return {
        "run_id": run_record.id if run_record else None,
        "executed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": {"chembl_target_id": config.chembl_target_id,
                   "name": target_data.get("pref_name"), "organism": target_data.get("organism"),
                   "target_type": target_type,
                   "uniprot_accessions": target_data.get("uniprot_accessions")},
        "parameters": config.as_dict(),
        "dataset_snapshot": snapshot,
        "software_versions": software_versions(),
        "ranking_method": ranking_method,
        "exclusion_reasons": {"curation": curation.exclusion_counts,
                              "screening_filters": excluded_by_filter},
        "limitations": limitations,
        "results": ranked,
        "content_fingerprint": content_fingerprint,
    }
