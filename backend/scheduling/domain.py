"""Deterministic scheduling rules. No HTTP, database, or wall-clock dependencies."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import combinations
from .topology import Graph


@dataclass(frozen=True)
class Step:
    node: str
    dwell_seconds: int = 0


@dataclass(frozen=True)
class Service:
    id: int
    vehicle_id: str
    start_at: datetime
    steps: tuple[Step, ...]
    is_passenger_service: bool = True


@dataclass(frozen=True)
class Interval:
    resource: str
    service_id: int
    vehicle_id: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Visit:
    node: str
    arrival: datetime
    departure: datetime
    battery: float | None = None


@dataclass(frozen=True)
class Conflict:
    code: str
    message: str
    service_ids: tuple[int, ...]
    vehicle_ids: tuple[str, ...]
    resource: str | None = None
    start: datetime | None = None
    end: datetime | None = None


@dataclass(frozen=True)
class Timeline:
    service: Service
    visits: tuple[Visit, ...]
    blocks: tuple[Interval, ...]

    @property
    def end_at(self) -> datetime:
        return self.visits[-1].departure


@dataclass(frozen=True)
class Result:
    status: str
    timelines: tuple[Timeline, ...]
    conflicts: tuple[Conflict, ...]
    # Vehicles whose trajectories cannot be simulated after an invalid handoff.
    unknown_from: dict[str, datetime]


class StructuralError(ValueError):
    def __init__(self, code: str, message: str, service_id: int | None = None,
                 step_index: int | None = None):
        super().__init__(message)
        self.code, self.service_id, self.step_index = code, service_id, step_index


def _utc_second(value: datetime) -> bool:
    return (isinstance(value, datetime) and value.tzinfo is not None
            and value.utcoffset() == timedelta(0) and value.microsecond == 0)


def _check(graph: Graph, durations: dict[str, int], vehicles: set[str],
           services: tuple[Service, ...], scenario_start_at: datetime) -> None:
    if not _utc_second(scenario_start_at):
        raise StructuralError("INVALID_TIME", "scenario_start_at must be UTC with second precision")
    blocks = {node for node, kind in graph.nodes.items() if kind == "BLOCK"}
    if set(durations) != blocks or any(type(v) is not int or v <= 0 for v in durations.values()):
        raise StructuralError("INVALID_CONFIG", "every block requires a positive integer duration")
    ids: set[int] = set()
    for service in services:
        if type(service.id) is not int or service.id <= 0 or service.id in ids:
            raise StructuralError("INVALID_ID", "service IDs must be unique positive integers", service.id)
        ids.add(service.id)
        if service.vehicle_id not in vehicles:
            raise StructuralError("UNKNOWN_VEHICLE", "vehicle does not exist", service.id)
        if not _utc_second(service.start_at) or service.start_at < scenario_start_at:
            raise StructuralError("INVALID_TIME", "start must be a UTC second >= scenario start", service.id)
        if len(service.steps) < 2:
            raise StructuralError("INVALID_PATH", "path needs at least two nodes", service.id)
        for index, step in enumerate(service.steps):
            kind = graph.nodes.get(step.node)
            if kind is None:
                raise StructuralError("UNKNOWN_NODE", f"unknown node {step.node}", service.id, index)
            if type(step.dwell_seconds) is not int or step.dwell_seconds < 0 or kind == "BLOCK" and step.dwell_seconds != 0:
                raise StructuralError("INVALID_DWELL", "only platforms and Y can have nonnegative dwell", service.id, index)
            if index and (service.steps[index - 1].node, step.node) not in graph.edges:
                raise StructuralError("INVALID_EDGE", "path contains a disconnected or reversed edge", service.id, index)
        if graph.nodes[service.steps[0].node] == "BLOCK" or graph.nodes[service.steps[-1].node] == "BLOCK":
            raise StructuralError("INVALID_ENDPOINT", "service must start/end at a platform or Y", service.id)


def _interval_conflicts(intervals: list[Interval], code: str) -> list[Conflict]:
    """Compare different vehicles; half-open intervals allow touching endpoints."""
    result = []
    for a, b in combinations(sorted(intervals, key=lambda x: x.start), 2):
        if a.vehicle_id == b.vehicle_id or a.service_id == b.service_id:
            continue
        start, end = max(a.start, b.start), min(a.end, b.end)
        if start < end:
            result.append(Conflict(code, f"{a.resource} occupied by multiple vehicles",
                                   (a.service_id, b.service_id), (a.vehicle_id, b.vehicle_id),
                                   a.resource, start, end))
    return result


def _build_timeline(graph: Graph, durations: dict[str, int], service: Service) -> Timeline:
    now = service.start_at
    visits: list[Visit] = []
    blocks: list[Interval] = []
    for step in service.steps:
        arrival = now
        if graph.nodes[step.node] == "BLOCK":
            now += timedelta(seconds=durations[step.node])
            blocks.append(Interval(step.node, service.id, service.vehicle_id, arrival, now))
        else:
            now += timedelta(seconds=step.dwell_seconds)
        visits.append(Visit(step.node, arrival, now))
    return Timeline(service, tuple(visits), tuple(blocks))


def _resource_conflicts(graph: Graph, durations: dict[str, int],
                        timelines: list[Timeline]) -> list[Conflict]:
    conflicts: list[Conflict] = []
    all_blocks = [b for t in timelines for b in t.blocks]
    for node in durations:
        conflicts.extend(_interval_conflicts([b for b in all_blocks if b.resource == node], "BLOCK_OVERLAP"))
    for group, members in graph.interlockings.items():
        intervals = [Interval(group, b.service_id, b.vehicle_id, b.start, b.end)
                     for b in all_blocks if b.resource in members]
        conflicts.extend(_interval_conflicts(intervals, "INTERLOCKING_OVERLAP"))
    return conflicts


def _vehicle_conflicts(vehicles: set[str], timelines: list[Timeline]
                       ) -> tuple[list[Conflict], dict[str, datetime]]:
    conflicts: list[Conflict] = []
    unknown_from: dict[str, datetime] = {}
    for vehicle in sorted(vehicles):
        runs = [t for t in timelines if t.service.vehicle_id == vehicle]
        previous: Timeline | None = None
        for run in runs:
            origin = run.visits[0].node
            expected = previous.visits[-1].node if previous else "Y"
            bad = False
            if previous and run.service.start_at < previous.end_at:
                conflicts.append(Conflict("VEHICLE_OVERLAP", "vehicle services overlap",
                                          (previous.service.id, run.service.id), (vehicle,),
                                          vehicle, run.service.start_at, min(previous.end_at, run.end_at)))
                bad = True
            if vehicle not in unknown_from and origin != expected:
                conflicts.append(Conflict("LOCATION_DISCONTINUITY", f"expected {expected}, got {origin}",
                                          (previous.service.id, run.service.id) if previous else (run.service.id,),
                                          (vehicle,), vehicle, run.service.start_at, run.service.start_at))
                bad = True
            if bad and vehicle not in unknown_from:
                unknown_from[vehicle] = run.service.start_at
            previous = run
    return conflicts, unknown_from


def _simulate_battery(graph: Graph, vehicles: set[str], timelines: list[Timeline],
                      scenario_start_at: datetime, unknown_from: dict[str, datetime]
                      ) -> tuple[list[Timeline], list[Conflict]]:
    # Position and battery must not be invented for a broken trajectory.
    updated: list[Timeline] = []
    conflicts: list[Conflict] = []
    for vehicle in sorted(vehicles):
        battery = 80.0
        last_time = scenario_start_at
        last_node = "Y"
        for run in (t for t in timelines if t.service.vehicle_id == vehicle):
            if vehicle in unknown_from and run.service.start_at >= unknown_from[vehicle]:
                updated.append(run)
                continue
            if last_node == "Y":
                battery = min(100.0, battery + (run.service.start_at - last_time).total_seconds() / 12)
            visits: list[Visit] = []
            for index, visit in enumerate(run.visits):
                if index and last_node == "Y" and visit.node != "Y" and battery < 80:
                    conflicts.append(Conflict("INSUFFICIENT_CHARGE", "departure from Y below 80",
                                              (run.service.id,), (vehicle,), "Y", visit.arrival, visit.arrival))
                if visit.node == "Y":
                    # A Y visit charges throughout its dwell, including the first
                    # or last step of a service; idle between services also charges.
                    battery = min(100.0, battery + (visit.departure - visit.arrival).total_seconds() / 12)
                if graph.nodes[visit.node] == "BLOCK":
                    battery -= 1
                    if battery < 30:
                        conflicts.append(Conflict("LOW_BATTERY", "battery below 30 outside Y",
                                                  (run.service.id,), (vehicle,), visit.node,
                                                  visit.arrival, visit.departure))
                visits.append(Visit(visit.node, visit.arrival, visit.departure, battery))
                last_node = visit.node
            last_time = run.end_at
            updated.append(Timeline(run.service, tuple(visits), run.blocks))
    updated.sort(key=lambda t: (t.service.start_at, t.service.id))
    return updated, conflicts


def evaluate(graph: Graph, durations: dict[str, int], vehicles: set[str],
             services: tuple[Service, ...], scenario_start_at: datetime) -> Result:
    """Reject malformed inputs; report schedule conflicts as a storable draft."""
    _check(graph, durations, vehicles, services, scenario_start_at)
    timelines = sorted((_build_timeline(graph, durations, service) for service in services),
                       key=lambda t: (t.service.start_at, t.service.id))
    conflicts = _resource_conflicts(graph, durations, timelines)
    vehicle_conflicts, unknown_from = _vehicle_conflicts(vehicles, timelines)
    conflicts.extend(vehicle_conflicts)
    updated, battery_conflicts = _simulate_battery(graph, vehicles, timelines, scenario_start_at, unknown_from)
    conflicts.extend(battery_conflicts)
    return Result("draft" if conflicts else "validated", tuple(updated), tuple(conflicts), unknown_from)
