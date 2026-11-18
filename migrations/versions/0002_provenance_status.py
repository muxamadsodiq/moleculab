"""v0.2: separate interpretation from review; remove AI pseudo-reviews

v0.1 seeded one ReviewRecord per evidence record authored by the AI curator,
which made AI curation look like part of the review history. This migration
records that fact as `interpretation_status`/`interpreted_by` on the evidence
record and deletes those pseudo-reviews. Reviews by anyone else are kept.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

AI_REVIEWER_PREFIX = "ai-assisted-curation"


def upgrade() -> None:
    with op.batch_alter_table("evidence_record") as b:
        b.add_column(sa.Column("interpretation_status", sa.String(40), nullable=False,
                               server_default="not_interpreted"))
        b.add_column(sa.Column("interpreted_by", sa.String(120)))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT evidence_record_id, reviewer, review_status FROM review_record "
                                "WHERE reviewer LIKE :p"), {"p": AI_REVIEWER_PREFIX + "%"}).all()
    for evidence_id, reviewer, status in rows:
        conn.execute(sa.text("UPDATE evidence_record SET interpretation_status = 'ai_assisted_interpretation', "
                             "interpreted_by = :who WHERE id = :id"), {"who": reviewer[:120], "id": evidence_id})
        if status == "needs_full_text":
            conn.execute(sa.text("UPDATE evidence_record SET curator_notes = COALESCE(curator_notes || ' ', '') || "
                                 "'[AI curator flagged: needs full-text check]' WHERE id = :id"), {"id": evidence_id})
    conn.execute(sa.text("DELETE FROM review_record WHERE reviewer LIKE :p"), {"p": AI_REVIEWER_PREFIX + "%"})


def downgrade() -> None:
    with op.batch_alter_table("evidence_record") as b:
        b.drop_column("interpreted_by")
        b.drop_column("interpretation_status")
