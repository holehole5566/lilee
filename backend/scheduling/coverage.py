"""Bonus 3 passenger-wait coverage, independent of generator search strategy.

At every t in [start, end], each station needs a passenger service arriving
within interval seconds, or a vehicle still boarding at the platform at t.
Boarding remains possible through the departure instant. Every service that
visits a platform is boardable; travel without a platform visit covers nothing.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from .domain import Result, _utc_second
from .topology import Graph


@dataclass(frozen=True)
class Gap:
    station_id: str
    start: datetime
    end: datetime
    start_inclusive: bool
    end_inclusive: bool


def uncovered(graph: Graph, result: Result, start: datetime, end: datetime,
              interval_seconds: int) -> tuple[Gap, ...]:
    if not _utc_second(start) or not _utc_second(end) or start > end:
        raise ValueError("coverage range must contain ordered UTC whole seconds")
    if type(interval_seconds) is not int or interval_seconds <= 0:
        raise ValueError("interval must be a positive integer of seconds")
    stops: dict[str, list[tuple[datetime, datetime]]] = {station: [] for station in graph.stations.values()}
    for timeline in result.timelines:
        for visit in timeline.visits:
            station = graph.stations.get(visit.node)
            if station:
                stops[station].append((visit.arrival, visit.departure))
    gaps: list[Gap] = []
    for station in sorted(stops):
        covered_until = start
        has_coverage = False
        for arrival, departure in sorted(stops[station]):
            # A passenger before arrival waits at most interval seconds; one
            # who arrives while stopped can board immediately. Do not discard
            # a stop arriving before start if it is still boarding at start.
            if departure < start or arrival > end + timedelta(seconds=interval_seconds):
                continue
            left = max(start, arrival - timedelta(seconds=interval_seconds))
            right = min(end, departure)
            if left > right:
                continue
            if left > covered_until:
                gaps.append(Gap(station, covered_until, left, not has_coverage, False))
            if right >= covered_until:
                covered_until, has_coverage = right, True
        if not has_coverage or covered_until < end:
            gaps.append(Gap(station, covered_until, end, not has_coverage, True))
    return tuple(gaps)
