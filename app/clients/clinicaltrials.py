"""ClinicalTrials.gov API v2 client.

The scientific rule this module exists to protect: **a registry record describes a study that was
registered, not a treatment that works.** Every field here is study *registration* metadata. So:

* `has_posted_results` reports whether a results section exists in the registry. It is not an outcome,
  and a completed study with no posted results establishes nothing either way.
* `overall_status` is recruitment/administrative status. `COMPLETED` means the study finished, not that it
  succeeded; `TERMINATED`/`WITHDRAWN` carry `why_stopped` when the sponsor gave a reason.
* Outcome *measures* are the endpoints the study said it would assess. They are not results.

Nothing in this client infers efficacy, and `search` never fabricates a record: an empty page is reported
as zero studies found for that query, which is not evidence that no such research exists anywhere.
"""
from __future__ import annotations

from app.clients.base import SourceClient, SourceResult

#: Registry fields requested. Keeping this explicit keeps responses small and the mapping auditable.
FIELDS = (
    "protocolSection.identificationModule",
    "protocolSection.statusModule",
    "protocolSection.sponsorCollaboratorsModule",
    "protocolSection.conditionsModule",
    "protocolSection.designModule",
    "protocolSection.armsInterventionsModule",
    "protocolSection.outcomesModule",
    "protocolSection.eligibilityModule",
    "protocolSection.contactsLocationsModule",
    "hasResults",
)

VALID_STATUS = {
    "NOT_YET_RECRUITING", "RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING",
    "SUSPENDED", "TERMINATED", "COMPLETED", "WITHDRAWN", "UNKNOWN", "AVAILABLE",
    "NO_LONGER_AVAILABLE", "TEMPORARILY_NOT_AVAILABLE", "APPROVED_FOR_MARKETING",
}
VALID_PHASE = {"EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"}


def _date(block: dict | None) -> str | None:
    return (block or {}).get("date")


def _study(raw: dict) -> dict:
    p = raw.get("protocolSection") or {}
    ident = p.get("identificationModule") or {}
    status = p.get("statusModule") or {}
    design = p.get("designModule") or {}
    arms = p.get("armsInterventionsModule") or {}
    outcomes = p.get("outcomesModule") or {}
    elig = p.get("eligibilityModule") or {}
    locations = p.get("contactsLocationsModule") or {}
    sponsor = p.get("sponsorCollaboratorsModule") or {}
    enrol = design.get("enrollmentInfo") or {}
    nct = ident.get("nctId")

    return {
        "nct_id": nct,
        "title": ident.get("briefTitle"),
        "official_title": ident.get("officialTitle"),
        "acronym": ident.get("acronym"),
        "lead_sponsor": ((sponsor.get("leadSponsor") or {}).get("name")),
        "collaborators": [c.get("name") for c in (sponsor.get("collaborators") or []) if c.get("name")],
        "conditions": (p.get("conditionsModule") or {}).get("conditions") or [],
        "keywords": (p.get("conditionsModule") or {}).get("keywords") or [],
        "study_type": design.get("studyType"),
        # A study may legitimately list several phases (e.g. PHASE1/PHASE2); the list is kept whole.
        "phases": design.get("phases") or [],
        "allocation": (design.get("designInfo") or {}).get("allocation"),
        "primary_purpose": (design.get("designInfo") or {}).get("primaryPurpose"),
        "masking": ((design.get("designInfo") or {}).get("maskingInfo") or {}).get("masking"),
        # Administrative/recruitment status only. Never an efficacy signal.
        "overall_status": status.get("overallStatus"),
        "why_stopped": status.get("whyStopped"),
        "start_date": _date(status.get("startDateStruct")),
        "primary_completion_date": _date(status.get("primaryCompletionDateStruct")),
        "completion_date": _date(status.get("completionDateStruct")),
        "last_update_post_date": _date(status.get("lastUpdatePostDateStruct")),
        "enrollment_count": enrol.get("count"),
        "enrollment_type": enrol.get("type"),  # ACTUAL vs ESTIMATED matters when reading a number
        "interventions": [{"type": i.get("type"), "name": i.get("name"),
                           "description": i.get("description"),
                           "other_names": i.get("otherNames") or []}
                          for i in (arms.get("interventions") or [])],
        "arm_groups": [{"label": a.get("label"), "type": a.get("type"),
                        "description": a.get("description")} for a in (arms.get("armGroups") or [])],
        # Endpoints the study declared it would measure. These are not results.
        "primary_outcomes": [{"measure": o.get("measure"), "description": o.get("description"),
                              "time_frame": o.get("timeFrame")}
                             for o in (outcomes.get("primaryOutcomes") or [])],
        "secondary_outcomes": [{"measure": o.get("measure"), "description": o.get("description"),
                                "time_frame": o.get("timeFrame")}
                               for o in (outcomes.get("secondaryOutcomes") or [])],
        "eligibility_criteria": elig.get("eligibilityCriteria"),
        "healthy_volunteers": elig.get("healthyVolunteers"),
        "sex": elig.get("sex"),
        "minimum_age": elig.get("minimumAge"),
        "maximum_age": elig.get("maximumAge"),
        "locations": [{"facility": loc.get("facility"), "city": loc.get("city"),
                       "state": loc.get("state"), "country": loc.get("country"),
                       "status": loc.get("status")}
                      for loc in (locations.get("locations") or [])],
        # Whether a results section EXISTS in the registry. Not whether the treatment worked.
        "has_posted_results": bool(raw.get("hasResults")),
        "source_url": f"https://clinicaltrials.gov/study/{nct}" if nct else None,
    }


class ClinicalTrialsClient(SourceClient):
    name = "clinicaltrials"
    base_url = "https://clinicaltrials.gov/api/v2"
    cacheable = True
    min_interval = 0.2
    transformation_version = "1.0"

    def search(self, condition: str | None = None, intervention: str | None = None,
               term: str | None = None, phases: list[str] | None = None,
               statuses: list[str] | None = None, location: str | None = None,
               page_size: int = 25, max_records: int | None = None) -> SourceResult:
        """Search registered studies. At least one of condition/intervention/term is required.

        Phase and status values are validated against the API's own vocabularies, because an unknown
        value is silently ignored by the service, which would otherwise return a broader result set than
        the caller asked for while looking like a successful filter.
        """
        if not any((condition, intervention, term)):
            return SourceResult(self.name, False,
                                error="a condition, intervention or search term is required")
        bad_phase = [p for p in (phases or []) if p not in VALID_PHASE]
        if bad_phase:
            return SourceResult(self.name, False,
                                error=f"unknown phase(s) {bad_phase}; valid: {sorted(VALID_PHASE)}")
        bad_status = [s for s in (statuses or []) if s not in VALID_STATUS]
        if bad_status:
            return SourceResult(self.name, False,
                                error=f"unknown status(es) {bad_status}; valid: {sorted(VALID_STATUS)}")

        params: dict = {"pageSize": min(int(page_size), 100), "countTotal": "true",
                        "fields": "|".join(FIELDS)}
        if condition:
            params["query.cond"] = condition
        if intervention:
            params["query.intr"] = intervention
        if term:
            params["query.term"] = term
        if location:
            params["query.locn"] = location
        if phases:
            params["filter.advanced"] = " AND ".join(f"AREA[Phase]{p}" for p in phases)
        if statuses:
            params["filter.overallStatus"] = ",".join(statuses)

        rows: list[dict] = []
        total = None
        first = self._get("/studies", params)
        res = first
        while True:
            if not res.ok:
                if rows:
                    first.data, first.ok = {"total_count": total, "studies": rows}, False
                    first.error = f"partial download: {res.error}"
                    return first
                return res
            if not isinstance(res.data, dict):
                res.ok, res.data, res.error = False, None, "unexpected ClinicalTrials.gov response shape"
                return res
            total = res.data.get("totalCount", total)
            rows.extend(_study(s) for s in (res.data.get("studies") or []))
            token = res.data.get("nextPageToken")
            if not token or (max_records is not None and len(rows) >= max_records):
                break
            res = self._get("/studies", {**params, "pageToken": token})

        if max_records is not None:
            rows = rows[:max_records]
        first.data = {"total_count": total, "studies": rows, "returned": len(rows)}
        first.ok, first.error = True, None
        return first

    def study(self, nct_id: str) -> SourceResult:
        res = self._get(f"/studies/{nct_id}")
        if res.ok:
            if isinstance(res.data, dict) and (res.data.get("protocolSection") or {}):
                res.provider_record_id = nct_id
                res.data = _study(res.data)
            else:
                res.ok, res.data, res.error = False, None, "unexpected ClinicalTrials.gov response shape"
        return res
