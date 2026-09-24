"""Relational model and first-run seed; Alembic owns schema upgrades."""
import os
from datetime import datetime
from sqlalchemy import (CheckConstraint, ForeignKey, Integer, String, Boolean,
                        DateTime, Index, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .topology import assignment_graph


class Base(DeclarativeBase):
    pass


class ScheduleRow(Base):
    __tablename__ = "schedule"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validation_status: Mapped[str] = mapped_column(String(12), nullable=False, default="validated")
    __table_args__ = (CheckConstraint("id IN (1, 2)", name="schedule_id_check"), CheckConstraint("revision >= 0"),
                      CheckConstraint("validation_status IN ('draft','validated')"))


class VehicleRow(Base):
    __tablename__ = "vehicles"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)


class NodeRow(Base):
    __tablename__ = "track_nodes"
    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    station_id: Mapped[str | None] = mapped_column(String(10))
    __table_args__ = (CheckConstraint("kind IN ('YARD','PLATFORM','BLOCK')"),)


class EdgeRow(Base):
    __tablename__ = "track_edges"
    from_node_id: Mapped[str] = mapped_column(ForeignKey("track_nodes.id"), primary_key=True)
    to_node_id: Mapped[str] = mapped_column(ForeignKey("track_nodes.id"), primary_key=True)


class GroupRow(Base):
    __tablename__ = "interlocking_groups"
    id: Mapped[str] = mapped_column(String(10), primary_key=True)


class MemberRow(Base):
    __tablename__ = "interlocking_members"
    group_id: Mapped[str] = mapped_column(ForeignKey("interlocking_groups.id"), primary_key=True)
    block_id: Mapped[str] = mapped_column(ForeignKey("track_nodes.id"), primary_key=True)


class BlockRow(Base):
    __tablename__ = "block_configs"
    block_id: Mapped[str] = mapped_column(ForeignKey("track_nodes.id"), primary_key=True)
    traversal_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (CheckConstraint("traversal_seconds > 0"),)


class ServiceRow(Base):
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedule.id", ondelete="RESTRICT"), nullable=False)
    __table_args__ = (Index("ix_services_schedule_id", "schedule_id"),)
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_passenger_service: Mapped[bool] = mapped_column(Boolean, nullable=False)


class StepRow(Base):
    __tablename__ = "service_steps"
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), primary_key=True)
    sequence_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("track_nodes.id"), nullable=False)
    dwell_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (CheckConstraint("sequence_index >= 0"), CheckConstraint("dwell_seconds >= 0"))


DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://schedule:schedule@localhost:5432/schedule")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)


def bootstrap() -> None:
    # Compose's migrate job has applied the schema before API startup.
    with Session.begin() as db:
        if db.get(ScheduleRow, 1) is not None:
            # Upgrading an existing DB preserves the manual row; migration 002
            # has inserted the auto row based on its original scenario start.
            if db.get(ScheduleRow, 2) is None:
                raise RuntimeError("auto schedule missing: apply Alembic migration 002")
            return
        start = datetime.fromisoformat(os.environ.get("SCENARIO_START_AT", "2026-01-01T00:00:00+00:00"))
        if start.utcoffset() is None or start.utcoffset().total_seconds() != 0 or start.microsecond:
            raise ValueError("SCENARIO_START_AT must be a UTC-aware whole second")
        graph = assignment_graph()
        db.add_all((ScheduleRow(id=1, scenario_start_at=start, revision=0, validation_status="validated"),
                    ScheduleRow(id=2, scenario_start_at=start, revision=0, validation_status="validated")))
        db.add_all(NodeRow(id=node, kind=kind, station_id=graph.stations.get(node))
                   for node, kind in graph.nodes.items())
        db.flush()
        db.add_all(EdgeRow(from_node_id=a, to_node_id=b) for a, b in graph.edges)
        db.add_all(GroupRow(id=g) for g in graph.interlockings)
        db.flush()
        db.add_all(MemberRow(group_id=g, block_id=b)
                   for g, blocks in graph.interlockings.items() for b in blocks)
        db.add_all(BlockRow(block_id=b, traversal_seconds=60)
                   for b, kind in graph.nodes.items() if kind == "BLOCK")
