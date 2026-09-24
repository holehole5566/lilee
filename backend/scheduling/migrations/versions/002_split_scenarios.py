"""Separate manual and auto alternative schedules without moving/deleting existing runs.

Revision ID: 002
Revises: 001
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("schedule_id_check", "schedule", type_="check")
    op.create_check_constraint("schedule_id_check", "schedule", "id IN (1, 2)")
    # Fresh DB has no schedule row until the seed job runs. Existing DB keeps its
    # original row (including start/revision/status); auto starts as an empty alternative.
    op.execute("""
        INSERT INTO schedule (id, scenario_start_at, revision, validation_status)
        SELECT 2, scenario_start_at, 0, 'validated' FROM schedule WHERE id = 1
        ON CONFLICT (id) DO NOTHING
    """)
    op.add_column("services", sa.Column("schedule_id", sa.Integer(), nullable=False,
                                        server_default="1"))
    op.create_foreign_key("services_schedule_id_fkey", "services", "schedule", ["schedule_id"],
                          ["id"], ondelete="RESTRICT")
    op.create_index("ix_services_schedule_id", "services", ["schedule_id"])
    op.alter_column("services", "schedule_id", server_default=None)


def downgrade():
    raise RuntimeError("Refusing downgrade: it could destroy the auto schedule; back up and migrate manually")
