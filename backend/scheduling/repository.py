"""Load a complete, detached domain snapshot in batches; no rule evaluation in SQL."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import (BlockRow, EdgeRow, GroupRow, MemberRow, NodeRow, ScheduleRow,
                 ServiceRow, StepRow, VehicleRow)
from .domain import Service, Step
from .topology import Graph


def load(db: Session, schedule_id: int = 1) -> tuple[Graph, dict[str, int], set[str], tuple[Service, ...]]:
    nodes = {r.id: r.kind for r in db.scalars(select(NodeRow)).all()}
    stations = {r.id: r.station_id for r in db.scalars(select(NodeRow).where(NodeRow.station_id.is_not(None))).all()}
    edges = frozenset((r.from_node_id, r.to_node_id) for r in db.scalars(select(EdgeRow)).all())
    groups: dict[str, set[str]] = {r.id: set() for r in db.scalars(select(GroupRow)).all()}
    for row in db.scalars(select(MemberRow)).all():
        groups[row.group_id].add(row.block_id)
    graph = Graph(nodes, edges, stations, {g: frozenset(m) for g, m in groups.items()})
    durations = {r.block_id: r.traversal_seconds for r in db.scalars(select(BlockRow)).all()}
    vehicles = {r.id for r in db.scalars(select(VehicleRow)).all()}
    steps: dict[int, list[Step]] = {}
    for row in db.scalars(select(StepRow).join(ServiceRow, StepRow.service_id == ServiceRow.id)
                          .where(ServiceRow.schedule_id == schedule_id)
                          .order_by(StepRow.service_id, StepRow.sequence_index)).all():
        steps.setdefault(row.service_id, []).append(Step(row.node_id, row.dwell_seconds))
    services = tuple(Service(r.id, r.vehicle_id, r.start_at, tuple(steps.get(r.id, ())),
                             r.is_passenger_service)
                     for r in db.scalars(select(ServiceRow).where(ServiceRow.schedule_id == schedule_id)
                                         .order_by(ServiceRow.id)).all())
    return graph, durations, vehicles, services


def lock_schedule(db: Session, schedule_id: int = 1) -> ScheduleRow:
    return db.execute(select(ScheduleRow).where(ScheduleRow.id == schedule_id).with_for_update()).scalar_one()


def lock_schedules(db: Session) -> tuple[ScheduleRow, ScheduleRow]:
    """Every shared-data writer locks manual then auto to avoid deadlocks."""
    return lock_schedule(db, 1), lock_schedule(db, 2)
