"""v0.5: research workspace tables

Workspace children cascade with their workspace. No table here has a foreign key into
plant/compound/publication/evidence_record, so workspace deletion cannot touch scientific records.

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLES = ("research_report", "workspace_activity", "research_task", "workspace_hypothesis", "research_note",
          "workspace_resource", "research_workspace")


def _ts():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def _ws():
    return sa.Column("workspace_id", sa.Integer, sa.ForeignKey("research_workspace.id", ondelete="CASCADE"),
                     nullable=False)


def upgrade() -> None:
    op.create_table(
        "research_workspace", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("owner", sa.String(120), nullable=False), sa.Column("title", sa.String(200), nullable=False),
        sa.Column("research_question", sa.Text, nullable=False), sa.Column("description", sa.Text),
        sa.Column("research_domain", sa.String(120)), sa.Column("scope", sa.Text),
        sa.Column("status", sa.String(30), nullable=False), sa.Column("status_before_archive", sa.String(30)),
        sa.Column("archived_at", sa.DateTime(timezone=True)), *_ts())
    op.create_index("ix_research_workspace_owner", "research_workspace", ["owner"])
    op.create_index("ix_research_workspace_status", "research_workspace", ["status"])
    op.create_table(
        "workspace_resource", sa.Column("id", sa.Integer, primary_key=True), _ws(),
        sa.Column("resource_type", sa.String(30), nullable=False), sa.Column("resource_ref", sa.String(200), nullable=False),
        sa.Column("label", sa.String(300), nullable=False), sa.Column("researcher_note", sa.Text),
        sa.Column("snapshot", sa.JSON), *_ts(), sa.UniqueConstraint("workspace_id", "resource_type", "resource_ref"))
    op.create_table(
        "research_note", sa.Column("id", sa.Integer, primary_key=True), _ws(),
        sa.Column("category", sa.String(30), nullable=False), sa.Column("body", sa.Text, nullable=False),
        sa.Column("resource_type", sa.String(30)), sa.Column("resource_ref", sa.String(200)),
        sa.Column("author", sa.String(120), nullable=False), *_ts())
    op.create_table(
        "workspace_hypothesis", sa.Column("id", sa.Integer, primary_key=True), _ws(),
        sa.Column("origin", sa.String(20), nullable=False), sa.Column("statement", sa.Text, nullable=False),
        sa.Column("category", sa.String(60)), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("causal_attribution", sa.String(50), nullable=False),
        sa.Column("supporting_evidence_ids", sa.JSON, nullable=False),
        sa.Column("contradicting_evidence_ids", sa.JSON, nullable=False),
        sa.Column("alternative_explanations", sa.JSON, nullable=False),
        sa.Column("validation_methods", sa.JSON, nullable=False), sa.Column("generator_snapshot", sa.JSON),
        sa.Column("author", sa.String(120), nullable=False), *_ts())
    op.create_table(
        "research_task", sa.Column("id", sa.Integer, primary_key=True), _ws(),
        sa.Column("title", sa.String(200), nullable=False), sa.Column("description", sa.Text),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("due_date", sa.String(10)), sa.Column("related_resources", sa.JSON, nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)), *_ts())
    op.create_index("ix_research_task_status", "research_task", ["status"])
    op.create_table(
        "workspace_activity", sa.Column("id", sa.Integer, primary_key=True), _ws(),
        sa.Column("action", sa.String(60), nullable=False), sa.Column("detail", sa.Text, nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_workspace_activity_created_at", "workspace_activity", ["created_at"])
    op.create_table(
        "research_report", sa.Column("id", sa.Integer, primary_key=True), _ws(),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_by", sa.String(120), nullable=False), sa.Column("markdown", sa.Text, nullable=False),
        sa.Column("content", sa.JSON, nullable=False), sa.Column("dataset_fingerprint", sa.JSON, nullable=False))
    for t in TABLES[:-1]:
        op.create_index(f"ix_{t}_workspace_id", t, ["workspace_id"])


def downgrade() -> None:
    for t in TABLES:
        op.drop_table(t)
