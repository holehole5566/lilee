"""Initial scheduling schema. Never import current ORM models into a historical migration.

Revision ID: 001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "schedule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scenario_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("validation_status", sa.String(12), nullable=False),
        sa.CheckConstraint("id = 1"),
        sa.CheckConstraint("revision >= 0"),
        sa.CheckConstraint("validation_status IN ('draft','validated')"),
    )
    op.create_table("vehicles",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False))
    op.create_table("track_nodes",
        sa.Column("id", sa.String(10), primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("station_id", sa.String(10)),
        sa.CheckConstraint("kind IN ('YARD','PLATFORM','BLOCK')"))
    op.create_table("track_edges",
        sa.Column("from_node_id", sa.String(10), sa.ForeignKey("track_nodes.id"), primary_key=True),
        sa.Column("to_node_id", sa.String(10), sa.ForeignKey("track_nodes.id"), primary_key=True))
    op.create_table("interlocking_groups",
        sa.Column("id", sa.String(10), primary_key=True))
    op.create_table("interlocking_members",
        sa.Column("group_id", sa.String(10), sa.ForeignKey("interlocking_groups.id"), primary_key=True),
        sa.Column("block_id", sa.String(10), sa.ForeignKey("track_nodes.id"), primary_key=True))
    op.create_table("block_configs",
        sa.Column("block_id", sa.String(10), sa.ForeignKey("track_nodes.id"), primary_key=True),
        sa.Column("traversal_seconds", sa.Integer(), nullable=False),
        sa.CheckConstraint("traversal_seconds > 0"))
    op.create_table("services",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("vehicle_id", sa.String(40), sa.ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_passenger_service", sa.Boolean(), nullable=False))
    op.create_table("service_steps",
        sa.Column("service_id", sa.Integer(), sa.ForeignKey("services.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("sequence_index", sa.Integer(), primary_key=True),
        sa.Column("node_id", sa.String(10), sa.ForeignKey("track_nodes.id"), nullable=False),
        sa.Column("dwell_seconds", sa.Integer(), nullable=False),
        sa.CheckConstraint("sequence_index >= 0"),
        sa.CheckConstraint("dwell_seconds >= 0"))


def downgrade():
    # Explicit downgrade for disposable development DBs only; destroys all schedule data.
    for table in ("service_steps", "services", "block_configs", "interlocking_members",
                  "interlocking_groups", "track_edges", "track_nodes", "vehicles", "schedule"):
        op.drop_table(table)
