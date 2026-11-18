"""v0.1 schema (as created by create_all in v0.1)

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _ts():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "plant",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("accepted_name", sa.String(200), nullable=False),
        sa.Column("authority", sa.String(120)), sa.Column("family", sa.String(120)),
        sa.Column("gbif_key", sa.Integer), sa.Column("taxonomy_source", sa.String(300)),
        sa.Column("notes", sa.Text), *_ts())
    op.create_index("ix_plant_accepted_name", "plant", ["accepted_name"], unique=True)
    op.create_table(
        "compound",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("pubchem_cid", sa.Integer),
        sa.Column("inchikey", sa.String(27)), sa.Column("molecular_formula", sa.String(80)),
        sa.Column("molecular_weight", sa.Float), sa.Column("smiles", sa.Text), sa.Column("iupac_name", sa.Text),
        sa.Column("synonyms", sa.JSON), sa.Column("identifier_source", sa.String(500)),
        sa.Column("identifier_retrieved_at", sa.String(40)), *_ts())
    op.create_index("ix_compound_name", "compound", ["name"], unique=True)
    op.create_index("ix_compound_pubchem_cid", "compound", ["pubchem_cid"])
    op.create_index("ix_compound_inchikey", "compound", ["inchikey"])
    op.create_table(
        "publication",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.Text, nullable=False), sa.Column("authors", sa.JSON, nullable=False),
        sa.Column("year", sa.Integer), sa.Column("journal", sa.String(300)), sa.Column("doi", sa.String(200)),
        sa.Column("pmid", sa.String(20)), sa.Column("url", sa.String(500)), sa.Column("language", sa.String(20)),
        sa.Column("publication_types", sa.JSON), sa.Column("abstract", sa.Text),
        sa.Column("metadata_source", sa.String(500)), sa.Column("metadata_retrieved_at", sa.String(40)), *_ts())
    op.create_index("ix_publication_year", "publication", ["year"])
    op.create_index("ix_publication_doi", "publication", ["doi"], unique=True)
    op.create_index("ix_publication_pmid", "publication", ["pmid"], unique=True)
    op.create_table(
        "plant_alias",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plant_id", sa.Integer, sa.ForeignKey("plant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("language", sa.String(8), nullable=False),
        sa.Column("script", sa.String(8), nullable=False), sa.Column("alias_type", sa.String(30), nullable=False),
        sa.Column("source", sa.String(300)), sa.Column("verification_status", sa.String(30), nullable=False),
        *_ts(), sa.UniqueConstraint("plant_id", "name", "language"))
    op.create_index("ix_plant_alias_plant_id", "plant_alias", ["plant_id"])
    op.create_index("ix_plant_alias_name", "plant_alias", ["name"])
    op.create_table(
        "evidence_record",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plant_id", sa.Integer, sa.ForeignKey("plant.id"), nullable=False),
        sa.Column("compound_id", sa.Integer, sa.ForeignKey("compound.id")),
        sa.Column("publication_id", sa.Integer, sa.ForeignKey("publication.id"), nullable=False),
        sa.Column("plant_part", sa.String(120)), sa.Column("geographic_origin", sa.String(200)),
        sa.Column("observation", sa.Text, nullable=False), sa.Column("identification_method", sa.Text),
        sa.Column("evidence_type", sa.String(50), nullable=False),
        sa.Column("source_location", sa.String(60), nullable=False),
        sa.Column("original_excerpt", sa.Text, nullable=False),
        sa.Column("verification_status", sa.String(30), nullable=False),
        sa.Column("curator_notes", sa.Text), *_ts())
    for col in ("plant_id", "compound_id", "publication_id", "evidence_type", "verification_status"):
        op.create_index(f"ix_evidence_record_{col}", "evidence_record", [col])
    op.create_table(
        "review_record",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("evidence_record_id", sa.Integer, sa.ForeignKey("evidence_record.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("review_status", sa.String(40), nullable=False), sa.Column("reviewer", sa.String(120), nullable=False),
        sa.Column("review_notes", sa.Text), *_ts())
    op.create_index("ix_review_record_evidence_record_id", "review_record", ["evidence_record_id"])


def downgrade() -> None:
    for t in ("review_record", "evidence_record", "plant_alias", "publication", "compound", "plant"):
        op.drop_table(t)
