"""Screening engine tests (MOLECULAB 1.0, module 4).

Offline: the ChEMBL client is replaced by a mocked transport, so these tests pin behaviour rather
than network state. The adversarial cases are the point - a screening run must refuse to proceed
rather than produce a ranking it cannot justify, and must never present similarity or any computed
value as a measurement.
"""
import json
import os

import httpx
import pytest
from sqlalchemy import func, select

from app.clients.chembl import ChEMBLClient
from app.db import make_engine, init_db
from app.models import (ActivityMeasurement, BiologicalTarget, MolecularStructure, ScreeningResult,
                        ScreeningRun)
from app.screening import (DEFAULT_TOP_N, RANKABLE, ScreeningConfig, ScreeningError, run_screening,
                           software_versions, upsert_structure)
from sqlalchemy.orm import sessionmaker

live = pytest.mark.skipif(os.getenv("MOLECULAB_LIVE") != "1", reason="set MOLECULAB_LIVE=1 for live API tests")

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
# Three real-shaped JAK1-like compounds (structures are real molecules; values are fixtures).
C1 = "CC(C)c1nc(Nc2ccncc2)ncc1"
C2 = "CCc1nc(Nc2ccncc2)ncc1"
C3 = "CCCc1nc(Nc2ccncc2)ncc1"

TARGET = {"target_chembl_id": "CHEMBL2835", "pref_name": "Tyrosine-protein kinase JAK1",
          "target_type": "SINGLE PROTEIN", "organism": "Homo sapiens", "tax_id": 9606,
          "target_components": [{"accession": "P23458", "target_component_synonyms": [
              {"syn_type": "GENE_SYMBOL", "component_synonym": "JAK1"}]}]}
FAMILY_TARGET = {**TARGET, "target_chembl_id": "CHEMBL2363062", "target_type": "PROTEIN FAMILY",
                 "pref_name": "Janus Kinase (JAK)"}


def activity(molecule, smiles, value, relation="=", **kw):
    base = {"activity_id": abs(hash(molecule + str(value))) % 10**6, "molecule_chembl_id": molecule,
            "canonical_smiles": smiles, "standard_type": "IC50", "standard_relation": relation,
            "standard_value": str(value), "standard_units": "nM", "assay_chembl_id": "CHEMBL_A1",
            "assay_type": "B", "target_chembl_id": "CHEMBL2835", "target_organism": "Homo sapiens",
            "document_chembl_id": "CHEMBL_D1", "pchembl_value": None,
            "data_validity_comment": None, "potential_duplicate": False}
    return {**base, **kw}


DEFAULT_ROWS = [activity("CHEMBL_M1", C1, 1.0), activity("CHEMBL_M2", C2, 100.0),
                activity("CHEMBL_M3", C3, 10000.0)]


def chembl_stub(rows=None, target=None):
    """A ChEMBL client whose transport answers status/target/activity from fixtures."""
    rows = DEFAULT_ROWS if rows is None else rows
    target = target or TARGET

    def handler(request):
        path = request.url.path
        if path.endswith("/status.json"):
            return httpx.Response(200, json={"chembl_db_version": "ChEMBL_TEST"})
        if "/target/" in path:
            return httpx.Response(200, json=target)
        if "/activity.json" in path:
            return httpx.Response(200, json={"page_meta": {"next": None}, "activities": rows})
        return httpx.Response(404, json={})
    return ChEMBLClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0)


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'screen.db'}")
    init_db(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


# ------------------------------------------------------------------ configuration

def test_config_validation_collects_every_problem():
    problems = ScreeningConfig("T", endpoint="Inhibition", rank_by="vibes",
                               property_filters={"max_vibes": 1}, top_n=0).validate()
    assert len(problems) >= 4
    assert any("not a supported concentration endpoint" in p for p in problems)
    assert any("rank_by" in p for p in problems)
    assert any("unknown property filter" in p for p in problems)


def test_similarity_ranking_requires_a_reference():
    assert any("requires reference_smiles" in p
               for p in ScreeningConfig("T", rank_by="similarity").validate())


def test_invalid_reference_structure_is_refused():
    assert any("reference_smiles invalid" in p
               for p in ScreeningConfig("T", reference_smiles="C1CC").validate())


def test_rankable_dimensions_are_documented():
    assert set(RANKABLE) == {"measured_p_activity", "similarity"}
    assert all(isinstance(v, str) and v for v in RANKABLE.values())


# -------------------------------------------------------------------- refusals

def test_non_single_protein_target_is_refused(session):
    """Measurements against a PROTEIN FAMILY aggregate different proteins."""
    with pytest.raises(ScreeningError) as exc:
        run_screening(session, ScreeningConfig("CHEMBL2363062"),
                      client=chembl_stub(target=FAMILY_TARGET), persist=False)
    assert "PROTEIN FAMILY" in str(exc.value) and "SINGLE PROTEIN" in str(exc.value)


def test_no_comparable_measurements_is_refused_not_emptied(session):
    """An empty ranking must never be returned as a successful result."""
    rows = [activity("M1", C1, 1.0, standard_type="Ki")]  # wrong endpoint only
    with pytest.raises(ScreeningError) as exc:
        run_screening(session, ScreeningConfig("CHEMBL2835"), client=chembl_stub(rows), persist=False)
    assert "no comparable IC50 measurements" in str(exc.value)


def test_filters_removing_everything_are_refused_with_the_reason(session):
    with pytest.raises(ScreeningError) as exc:
        run_screening(session, ScreeningConfig("CHEMBL2835", min_p_activity=99.0),
                      client=chembl_stub(), persist=False)
    assert "removed by the configured filters" in str(exc.value)
    assert "below_min_p_activity" in str(exc.value)


def test_target_fetch_failure_is_refused(session):
    def handler(request):
        if request.url.path.endswith("/status.json"):
            return httpx.Response(200, json={"chembl_db_version": "X"})
        return httpx.Response(500)
    client = ChEMBLClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0,
                          max_retries=0)
    with pytest.raises(ScreeningError) as exc:
        run_screening(session, ScreeningConfig("CHEMBL2835"), client=client, persist=False)
    assert "could not be retrieved" in str(exc.value)


# ------------------------------------------------------------------- the ranking

def test_ranking_is_by_measured_activity_and_reports_dimensions(session):
    out = run_screening(session, ScreeningConfig("CHEMBL2835", top_n=10),
                        client=chembl_stub(), persist=False)
    assert [r["molecule_chembl_id"] for r in out["results"]] == ["CHEMBL_M1", "CHEMBL_M2", "CHEMBL_M3"]
    top = out["results"][0]["dimensions"]
    assert top["measured_p_activity"]["value"] == 9.0          # 1 nM
    assert "experimental measurement" in top["measured_p_activity"]["kind"]
    assert top["measured_p_activity"]["endpoint"] == "IC50"
    assert top["measurement_count"]["value"] == 1


def test_no_single_composite_score_is_produced(session):
    out = run_screening(session, ScreeningConfig("CHEMBL2835"), client=chembl_stub(), persist=False)
    result = out["results"][0]
    assert "score" not in result
    for forbidden in ("total_score", "composite_score", "success_probability", "confidence"):
        assert forbidden not in result and forbidden not in result["dimensions"]
    # Each dimension says what kind of quantity it is.
    assert all("kind" in d for d in result["dimensions"].values())


def test_similarity_is_a_separate_dimension_with_a_caveat(session):
    out = run_screening(session, ScreeningConfig("CHEMBL2835", reference_smiles=C1),
                        client=chembl_stub(), persist=False)
    similarity = out["results"][0]["dimensions"]["similarity"]
    assert similarity["value"] == 1.0
    assert "not evidence of biological activity" in similarity["caveat"]
    # Similarity must not have been folded into the activity dimension.
    assert out["results"][0]["dimensions"]["measured_p_activity"]["value"] == 9.0


def test_similarity_ranking_does_not_reorder_activity_values(session):
    """Ranking by similarity must not change what the activity dimension reports."""
    out = run_screening(session, ScreeningConfig("CHEMBL2835", reference_smiles=C3,
                                                 rank_by="similarity"),
                        client=chembl_stub(), persist=False)
    assert out["results"][0]["molecule_chembl_id"] == "CHEMBL_M3"   # most similar to C3
    assert out["results"][0]["dimensions"]["measured_p_activity"]["value"] == 5.0  # but weakest
    assert "similarity" in out["ranking_method"].lower()


def test_censored_measurements_do_not_enter_the_ranking(session):
    rows = [activity("M1", C1, 1.0), activity("M2", C2, 5.0, relation=">")]
    out = run_screening(session, ScreeningConfig("CHEMBL2835"), client=chembl_stub(rows),
                        persist=False)
    assert [r["molecule_chembl_id"] for r in out["results"]] == ["M1"]
    summary = out["dataset_snapshot"]["curation_summary"]
    assert summary["censored_measurements_excluded_from_numeric_use"] == 1


def test_property_filters_are_applied_and_counted(session):
    """C2 is MW 200.2; C1 and C3 are MW 214.3, so a 205 cutoff keeps exactly one."""
    out = run_screening(session, ScreeningConfig("CHEMBL2835",
                                                 property_filters={"max_molecular_weight": 205}),
                        client=chembl_stub(), persist=False)
    assert out["dataset_snapshot"]["candidates_after_filters"] == 1
    assert [r["molecule_chembl_id"] for r in out["results"]] == ["CHEMBL_M2"]
    assert out["exclusion_reasons"]["screening_filters"] == {"property_filter:MW > 205": 2}


def test_no_docking_score_anywhere(session):
    """MOLECULAB has no docking implementation, so no docking number may appear."""
    out = run_screening(session, ScreeningConfig("CHEMBL2835"), client=chembl_stub(), persist=False)
    body = json.dumps(out, default=str).lower()
    assert "docking" in body  # it is mentioned, in the limitations
    for forbidden in ("docking_score", "binding_energy", "kcal/mol", "glide", "vina"):
        assert forbidden not in body
    assert any("no docking" in limitation.lower() for limitation in out["limitations"])


def test_predictions_never_appear_in_a_screening_result(session):
    out = run_screening(session, ScreeningConfig("CHEMBL2835"), client=chembl_stub(), persist=False)
    for result in out["results"]:
        assert "predicted_p_activity" not in result and "prediction" not in result
        assert result["measured_p_activity"] is not None


# ------------------------------------------------------- provenance / reproducibility

def test_run_records_dataset_snapshot_and_software_versions(session):
    out = run_screening(session, ScreeningConfig("CHEMBL2835"), client=chembl_stub(), persist=False)
    snapshot = out["dataset_snapshot"]
    assert snapshot["source"] == "chembl" and snapshot["source_release"] == "ChEMBL_TEST"
    assert snapshot["target_type"] == "SINGLE PROTEIN"
    assert snapshot["raw_profile"]["raw_rows"] == 3
    assert snapshot["curation_summary"]["included_measurements"] == 3
    versions = out["software_versions"]
    assert versions["moleculab"] and versions["rdkit"] and versions["chemistry_version"]
    assert versions["fingerprint"]["bits"] == 2048


def test_identical_configuration_reproduces_the_fingerprint(session):
    first = run_screening(session, ScreeningConfig("CHEMBL2835"), client=chembl_stub(), persist=False)
    second = run_screening(session, ScreeningConfig("CHEMBL2835"), client=chembl_stub(), persist=False)
    assert first["content_fingerprint"] == second["content_fingerprint"]


def test_changed_parameters_change_the_fingerprint(session):
    base = run_screening(session, ScreeningConfig("CHEMBL2835"), client=chembl_stub(), persist=False)
    changed = run_screening(session, ScreeningConfig("CHEMBL2835", min_p_activity=6.0),
                            client=chembl_stub(), persist=False)
    assert base["content_fingerprint"] != changed["content_fingerprint"]


def test_fingerprint_depends_on_the_provider_release(session):
    """The same parameters against a different data release are a different run."""
    first = run_screening(session, ScreeningConfig("CHEMBL2835"), client=chembl_stub(), persist=False)

    def handler(request):
        path = request.url.path
        if path.endswith("/status.json"):
            return httpx.Response(200, json={"chembl_db_version": "ChEMBL_OTHER"})
        if "/target/" in path:
            return httpx.Response(200, json=TARGET)
        return httpx.Response(200, json={"page_meta": {"next": None}, "activities": DEFAULT_ROWS})
    other = ChEMBLClient(client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0)
    second = run_screening(session, ScreeningConfig("CHEMBL2835"), client=other, persist=False)
    assert first["content_fingerprint"] != second["content_fingerprint"]


def test_limitations_are_always_present(session):
    out = run_screening(session, ScreeningConfig("CHEMBL2835"), client=chembl_stub(), persist=False)
    assert len(out["limitations"]) >= 4
    joined = " ".join(out["limitations"]).lower()
    assert "not evidence of inactivity" in joined
    assert "not prediction" in joined or "not a prediction" in joined


def test_every_excluded_row_is_accounted_for(session):
    rows = DEFAULT_ROWS + [
        activity("M4", C1, 1.0, standard_type="Ki"),
        activity("M5", C2, 1.0, target_organism="Mus musculus"),
        activity("M6", C3, 1.0, potential_duplicate=True),
    ]
    out = run_screening(session, ScreeningConfig("CHEMBL2835"), client=chembl_stub(rows),
                        persist=False)
    exclusions = out["exclusion_reasons"]["curation"]
    assert exclusions == {"endpoint_mismatch": 1, "organism_mismatch": 1, "potential_duplicate": 1}


# -------------------------------------------------------------------- persistence

def test_persisted_run_stores_results_structures_and_provenance(session):
    out = run_screening(session, ScreeningConfig("CHEMBL2835", top_n=2), client=chembl_stub(),
                        executed_by="tester")
    session.commit()
    assert session.scalar(select(func.count()).select_from(ScreeningRun)) == 1
    assert session.scalar(select(func.count()).select_from(ScreeningResult)) == 2
    run = session.scalar(select(ScreeningRun))
    assert run.executed_by == "tester" and run.result_count == 2
    assert run.content_fingerprint == out["content_fingerprint"]
    assert run.software_versions["rdkit"] and run.ranking_method and run.limitations
    assert run.dataset_snapshot["source_release"] == "ChEMBL_TEST"
    assert run.target_id is not None

    result = session.scalars(select(ScreeningResult).order_by(ScreeningResult.rank)).first()
    assert result.rank == 1 and result.measured_p_activity == 9.0
    assert "Experimental IC50 measurement" in result.basis
    assert result.evidence_refs["assay_chembl_ids"] == ["CHEMBL_A1"]
    structure = session.get(MolecularStructure, result.structure_id)
    assert structure.inchikey and structure.chembl_molecule_id == "CHEMBL_M1"
    assert structure.provider == "chembl" and structure.source_release == "ChEMBL_TEST"
    assert structure.descriptors["molecular_weight"] > 0
    assert structure.chemistry_version


def test_screening_result_table_has_no_prediction_column():
    """Structural guarantee: a prediction cannot be written where a measurement is read."""
    columns = set(ScreeningResult.__table__.columns.keys())
    assert "measured_p_activity" in columns
    assert not any("predict" in c for c in columns)


def test_activity_measurement_table_has_no_prediction_column():
    columns = set(ActivityMeasurement.__table__.columns.keys())
    assert not any("predict" in c for c in columns)
    assert "measurement_type" in columns


def test_upsert_structure_is_idempotent_and_rejects_invalid(session):
    first = upsert_structure(session, ASPIRIN, chembl_molecule_id="CHEMBL25")
    second = upsert_structure(session, ASPIRIN)
    assert first is not None and first.id == second.id
    assert session.scalar(select(func.count()).select_from(MolecularStructure)) == 1
    assert upsert_structure(session, "C1CC") is None


def test_upsert_structure_stores_both_submitted_and_standardized_forms(session):
    structure = upsert_structure(session, "CC(=O)Oc1ccccc1C(=O)[O-].[Na+]")
    assert structure.standardized_smiles == ASPIRIN
    assert "[Na+]" in structure.original_smiles
    assert structure.standardization_changed is True
    assert any(t["step"] == "fragment_parent" for t in structure.transformations)


def test_software_versions_are_concrete():
    versions = software_versions()
    assert versions["rdkit"][0].isdigit() and versions["moleculab"][0].isdigit()


def test_default_top_n_is_bounded():
    assert DEFAULT_TOP_N == 100


# -------------------------------------------------------------------------- live

@live
def test_live_jak1_screening_reproduces_and_refuses_family_target(session):
    config = ScreeningConfig("CHEMBL2835", "IC50", top_n=5)
    first = run_screening(session, config, persist=False)
    assert first["dataset_snapshot"]["source_release"].startswith("ChEMBL")
    assert first["dataset_snapshot"]["curation_summary"]["included_measurements"] > 1000
    assert len(first["results"]) == 5
    assert all(r["measured_p_activity"] is not None for r in first["results"])
    second = run_screening(session, config, persist=False)
    assert first["content_fingerprint"] == second["content_fingerprint"]

    with pytest.raises(ScreeningError):
        run_screening(session, ScreeningConfig("CHEMBL2363062", "IC50"), persist=False)
