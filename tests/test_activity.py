"""Activity compatibility tests (MOLECULAB 1.0, module 2).

The adversarial cases here are the point: these tests fail if the code ever starts converting Ki to
IC50, pooling human with non-human data, treating a censored ">" as an exact value, converting ug/mL
to molar without a molecular weight, or averaging wildly disagreeing replicates.
"""
import pytest

from app.activity import (ASSAY_TYPES, CONCENTRATION_ENDPOINTS, aggregate_by_compound,
                          compatibility_report, curate, dataset_profile, endpoint_family,
                          p_activity_from_nm, to_nanomolar, units_comparable, Measurement)


def row(**kw):
    base = {"molecule_chembl_id": "CHEMBL1", "canonical_smiles": "CCO", "standard_type": "IC50",
            "standard_relation": "=", "standard_value": "100", "standard_units": "nM",
            "assay_chembl_id": "CHEMBL_A1", "assay_type": "B", "target_chembl_id": "CHEMBL2835",
            "target_organism": "Homo sapiens", "document_chembl_id": "CHEMBL_D1",
            "pchembl_value": "7.0", "data_validity_comment": None, "potential_duplicate": False}
    return {**base, **kw}


def measurement(**kw):
    base = {"molecule_chembl_id": "C1", "smiles": "CCO", "standard_type": "IC50",
            "standard_relation": "=", "standard_value": 100.0, "standard_units": "nM",
            "assay_type": "B", "target_chembl_id": "CHEMBL2835", "target_organism": "Homo sapiens"}
    return Measurement(**{**base, **kw})


# ------------------------------------------------------------- endpoint identity

def test_ic50_and_ki_are_never_compatible():
    """Cheng-Prusoff needs substrate concentration and Km, which activity rows do not carry."""
    verdict = compatibility_report(measurement(standard_type="IC50"), measurement(standard_type="Ki"))
    assert verdict["compatible"] is False
    assert any("different endpoints" in r for r in verdict["reasons"])


@pytest.mark.parametrize("a,b", [("IC50", "EC50"), ("Ki", "Kd"), ("IC50", "Inhibition"),
                                 ("EC50", "Ki"), ("IC50", "AC50")])
def test_distinct_endpoints_are_never_pooled(a, b):
    assert compatibility_report(measurement(standard_type=a),
                               measurement(standard_type=b))["compatible"] is False


def test_identical_context_is_compatible():
    assert compatibility_report(measurement(), measurement())["compatible"] is True


def test_endpoint_family_classification():
    assert endpoint_family("IC50") == "concentration"
    assert endpoint_family("Inhibition") == "non_concentration"
    assert endpoint_family("WeirdMetric") == "unknown"
    assert endpoint_family(None) == "unknown"


# --------------------------------------------------------------------- organism

def test_human_and_non_human_are_not_interchangeable():
    verdict = compatibility_report(measurement(target_organism="Homo sapiens"),
                                   measurement(target_organism="Mus musculus"))
    assert verdict["compatible"] is False
    assert any("different organisms" in r for r in verdict["reasons"])


# ------------------------------------------------------------------ assay type

def test_binding_and_functional_assays_are_not_equivalent():
    verdict = compatibility_report(measurement(assay_type="B"), measurement(assay_type="F"))
    assert verdict["compatible"] is False
    assert any("different assay types" in r and "binding" in r and "functional" in r
               for r in verdict["reasons"])


def test_assay_type_codes_are_named_in_messages():
    assert ASSAY_TYPES["B"] == "binding" and ASSAY_TYPES["A"] == "ADMET"


def test_different_targets_are_not_compatible():
    verdict = compatibility_report(measurement(target_chembl_id="CHEMBL2835"),
                                   measurement(target_chembl_id="CHEMBL3553"))
    assert verdict["compatible"] is False
    assert any("different targets" in r for r in verdict["reasons"])


def test_all_blocking_differences_are_reported_together():
    verdict = compatibility_report(
        measurement(standard_type="IC50", target_organism="Homo sapiens", assay_type="B"),
        measurement(standard_type="Ki", target_organism="Rattus norvegicus", assay_type="F"))
    assert len(verdict["reasons"]) >= 3


# ------------------------------------------------------------------------ units

def test_molar_family_converts_exactly():
    assert to_nanomolar(1, "uM") == (1000.0, None)
    assert to_nanomolar(1, "mM") == (1e6, None)
    assert to_nanomolar(1, "M") == (1e9, None)
    assert to_nanomolar(1000, "pM") == (1.0, None)
    assert to_nanomolar(5, "nM") == (5.0, None)


def test_mass_per_volume_is_refused_without_a_molecular_weight():
    value, error = to_nanomolar(10, "ug.mL-1")
    assert value is None and "molecular weight" in error


def test_percent_and_unknown_units_are_refused():
    assert to_nanomolar(50, "%")[0] is None
    assert to_nanomolar(50, None)[0] is None
    assert "not a molar concentration" in to_nanomolar(50, "%")[1]


def test_non_numeric_and_non_positive_values_are_refused():
    assert to_nanomolar("abc", "nM") == (None, "non-numeric value")
    assert to_nanomolar(0, "nM")[1] == "non-positive concentration"
    assert to_nanomolar(-5, "nM")[1] == "non-positive concentration"
    assert to_nanomolar(float("inf"), "nM")[1] == "non-numeric value"


def test_units_comparability_matrix():
    assert units_comparable("nM", "uM") is True
    assert units_comparable("nM", "nM") is True
    assert units_comparable("nM", "ug.mL-1") is False
    assert units_comparable("%", "nM") is False


def test_p_activity_is_only_computed_from_molar_values():
    assert p_activity_from_nm(1.0) == 9.0        # 1 nM -> pIC50 9
    assert p_activity_from_nm(1000.0) == 6.0     # 1 uM -> 6
    assert p_activity_from_nm(None) is None
    assert p_activity_from_nm(0) is None


# ------------------------------------------------------- censored measurements

def test_censored_relations_are_kept_but_excluded_from_numeric_use():
    """Treating 'IC50 > 10000 nM' as '10000 nM' would invent precision."""
    rows = [row(standard_relation="=", standard_value="100"),
            row(molecule_chembl_id="C2", standard_relation=">", standard_value="10000"),
            row(molecule_chembl_id="C3", standard_relation="<", standard_value="1")]
    result = curate(rows, "IC50", "CHEMBL2835")
    assert len(result.included) == 1
    assert len(result.censored) == 2
    assert result.summary()["censored_measurements_excluded_from_numeric_use"] == 2
    assert all(not m.is_exact for m in result.censored)


def test_censored_can_be_retained_explicitly():
    rows = [row(standard_relation=">", standard_value="10000")]
    result = curate(rows, "IC50", "CHEMBL2835", exclude_censored=False)
    assert len(result.included) == 1 and result.included[0].is_exact is False
    assert len(result.censored) == 1


def test_unknown_relation_is_excluded_with_a_reason():
    result = curate([row(standard_relation="??")], "IC50", "CHEMBL2835")
    assert result.included == []
    assert result.exclusion_counts == {"unknown_relation": 1}


# ---------------------------------------------------------------------- curation

def test_curation_filters_and_accounts_for_every_row():
    rows = [
        row(),                                                        # keep
        row(molecule_chembl_id="C2", standard_type="Ki"),             # wrong endpoint
        row(molecule_chembl_id="C3", target_organism="Mus musculus"),  # wrong organism
        row(molecule_chembl_id="C4", assay_type="F"),                 # wrong assay type
        row(molecule_chembl_id="C5", target_chembl_id="CHEMBL3553"),  # wrong target
        row(molecule_chembl_id="C6", data_validity_comment="Outside typical range"),
        row(molecule_chembl_id="C7", potential_duplicate=True),
        row(molecule_chembl_id="C8", canonical_smiles=None),          # no structure
        row(molecule_chembl_id="C9", standard_units="ug.mL-1"),       # unconvertible
    ]
    result = curate(rows, "IC50", "CHEMBL2835", organism="Homo sapiens", assay_type="B")
    assert len(result.included) == 1
    summary = result.summary()
    assert summary["raw_rows"] == 9
    assert summary["included_measurements"] == 1
    assert summary["unique_compounds"] == 1
    assert summary["exclusion_reasons"] == {
        "endpoint_mismatch": 1, "organism_mismatch": 1, "assay_type_mismatch": 1,
        "target_mismatch": 1, "data_validity_flag": 1, "potential_duplicate": 1,
        "missing_structure": 1, "unit_not_convertible": 1}
    # Every single row is accounted for: nothing vanishes silently.
    assert summary["included_measurements"] + summary["excluded_rows"] + \
        summary["censored_measurements_excluded_from_numeric_use"] == 9


def test_curation_refuses_non_single_protein_targets():
    """Measurements against a PROTEIN FAMILY are not measurements against one protein."""
    result = curate([row()], "IC50", "CHEMBL2363062",
                    require_single_protein_target_type="PROTEIN FAMILY")
    assert result.included == []
    assert result.exclusion_counts == {"target_not_single_protein": 1}
    assert "several proteins" in result.excluded[0]["detail"]


def test_curation_accepts_single_protein_target():
    result = curate([row()], "IC50", "CHEMBL2835", require_single_protein_target_type="SINGLE PROTEIN")
    assert len(result.included) == 1


def test_curation_refuses_non_concentration_endpoint():
    result = curate([row(standard_type="Inhibition", standard_units="%")], "Inhibition", "CHEMBL2835")
    assert result.included == []
    assert result.exclusion_counts == {"unsupported_endpoint": 1}


def test_curation_computes_p_activity_only_for_included_rows():
    result = curate([row(standard_value="100", standard_units="nM")], "IC50", "CHEMBL2835")
    m = result.included[0]
    assert m.value_nm == 100.0 and m.p_activity == 7.0
    assert m.is_exact is True
    # Full experimental context travels with the measurement.
    assert m.provenance == {
        "assay_chembl_id": "CHEMBL_A1", "target_chembl_id": "CHEMBL2835",
        "document_chembl_id": "CHEMBL_D1", "assay_type": "B",
        "target_organism": "Homo sapiens", "standard_type": "IC50",
        "standard_relation": "=", "standard_value": 100.0, "standard_units": "nM"}


def test_supported_endpoints_are_explicit():
    assert {"IC50", "Ki", "Kd", "EC50"} <= CONCENTRATION_ENDPOINTS


# -------------------------------------------------------------------- aggregation

def test_replicates_are_median_aggregated_and_labelled_experimental():
    ms = [measurement(molecule_chembl_id="C1", standard_value=100.0),
          measurement(molecule_chembl_id="C1", standard_value=200.0),
          measurement(molecule_chembl_id="C1", standard_value=150.0)]
    for m in ms:
        m.value_nm = m.standard_value
        m.p_activity = p_activity_from_nm(m.value_nm)
    resolved, unresolved = aggregate_by_compound(ms)
    assert len(resolved) == 1 and unresolved == []
    assert resolved[0]["replicate_count"] == 3
    assert resolved[0]["measurement_type"] == "experimental"
    assert resolved[0]["p_activity_median"] == pytest.approx(6.8239, abs=0.001)


def test_wildly_disagreeing_replicates_are_not_averaged():
    """A 1000-fold disagreement is a data problem, not a value to smooth over."""
    ms = [measurement(molecule_chembl_id="C1", standard_value=1.0),
          measurement(molecule_chembl_id="C1", standard_value=10000.0)]
    for m in ms:
        m.value_nm = m.standard_value
        m.p_activity = p_activity_from_nm(m.value_nm)
    resolved, unresolved = aggregate_by_compound(ms, max_fold_spread=100.0)
    assert resolved == []
    assert len(unresolved) == 1 and "disagree" in unresolved[0]["reason"]


def test_aggregation_records_assay_and_document_provenance():
    ms = [measurement(molecule_chembl_id="C1", standard_value=100.0)]
    ms[0].value_nm, ms[0].p_activity = 100.0, 7.0
    ms[0].assay_chembl_id, ms[0].document_chembl_id = "A1", "D1"
    resolved, _ = aggregate_by_compound(ms)
    assert resolved[0]["assay_ids"] == ["A1"] and resolved[0]["document_ids"] == ["D1"]


# ----------------------------------------------------------------------- profile

def test_dataset_profile_describes_raw_rows_without_filtering():
    rows = [row(), row(standard_type="Ki", molecule_chembl_id="C2"),
            row(target_organism="Mus musculus", molecule_chembl_id="C3"),
            row(canonical_smiles=None, molecule_chembl_id="C4"),
            row(data_validity_comment="x", molecule_chembl_id="C5")]
    profile = dataset_profile(rows)
    assert profile["raw_rows"] == 5 and profile["unique_compounds"] == 5
    assert profile["by_standard_type"] == {"IC50": 4, "Ki": 1}
    assert profile["by_organism"]["Mus musculus"] == 1
    assert profile["missing_smiles"] == 1
    assert profile["with_data_validity_comment"] == 1
