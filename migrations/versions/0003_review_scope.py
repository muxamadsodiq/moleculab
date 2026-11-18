"""v0.3: review scope (interpretation review vs expert review)

Existing reviews keep their meaning: they were submitted as expert reviews.

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("review_record") as b:
        b.add_column(sa.Column("review_scope", sa.String(40), nullable=False, server_default="expert_review"))


def downgrade() -> None:
    with op.batch_alter_table("review_record") as b:
        b.drop_column("review_scope")
