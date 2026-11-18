"""Find candidate plant-compound evidence in PubMed abstracts for manual review.

Prints sentences that mention both the plant and a compound. Nothing is written
to the database: a curator reads the output, checks the paper, and adds a
record to data/seed/*.json by hand.

    python -m scripts.curate_candidates "Peganum harmala" harmine harmaline --extra "isolat*"
"""
import argparse
import re
import sys

from app.clients.pubmed import PubMedClient


def sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("plant")
    ap.add_argument("compounds", nargs="+")
    ap.add_argument("--extra", default="", help="extra PubMed terms, e.g. 'isolat*'")
    ap.add_argument("--retmax", type=int, default=15)
    args = ap.parse_args()

    client = PubMedClient()
    genus_species = args.plant.lower()
    for compound in args.compounds:
        term = f'"{args.plant}"[tiab] AND "{compound}"[tiab]'
        if args.extra:
            term += " AND (" + " OR ".join(f"{t.strip()}[tiab]" for t in args.extra.split(" OR ")) + ")"
        found = client.search(term, args.retmax)
        if not found.ok:
            print(f"!! search failed for {compound}: {found.error}")
            continue
        print(f"\n### {compound}: {found.data['count']} hits ({term})")
        records = client.fetch(found.data["pmids"])
        if not records.ok:
            print(f"!! fetch failed: {records.error}")
            continue
        for rec in records.data:
            hits = [s for s in sentences(rec["abstract"])
                    if compound.lower() in s.lower() and genus_species.split()[0] in s.lower()]
            hits = hits or [s for s in sentences(rec["abstract"]) if compound.lower() in s.lower()][:1]
            if not hits:
                continue
            print(f"- PMID {rec['pmid']} ({rec['year']}) [{', '.join(rec['publication_types'][:2])}] "
                  f"doi={rec['doi']}\n  {rec['title']}")
            for s in hits[:2]:
                print(f"    > {s.strip()}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a legacy code page
    main()
