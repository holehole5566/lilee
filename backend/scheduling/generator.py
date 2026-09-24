"""Bounded, deterministic Bonus 3 search; no persistence or HTTP dependencies.

Try a regular headway with existing vehicles first, then fall back to arrivals
aligned to uncovered windows. Both strategies use passenger-only Y-to-Y routes
and a 60-second dwell at each platform visit. Search failure is not proof of infeasibility.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import reduce
from math import gcd

from .coverage import Gap, uncovered
from .domain import Result, Service, Step, _utc_second, evaluate
from .topology import Graph

DEFAULT_DWELL_SECONDS = 60
MAX_ROUTE_NODES = 21
MAX_CANDIDATES = 2000
MAX_SERVICES = 128


@dataclass(frozen=True)
class SearchResult:
    services: tuple[Service, ...]
    evaluation: Result
    gaps: tuple[Gap, ...]
    attempts: int
    exhausted: bool

    @property
    def complete(self) -> bool:
        return self.evaluation.status == "validated" and not self.gaps


def yard_routes(graph: Graph) -> tuple[tuple[str, ...], ...]:
    """Enumerate simple directed Y loops visiting every station; never invent edges."""
    stations = set(graph.stations.values())
    next_nodes: dict[str, list[str]] = {}
    for a, b in graph.edges:
        next_nodes.setdefault(a, []).append(b)
    routes: set[tuple[str, ...]] = set()

    def walk(path: tuple[str, ...], visited_stations: frozenset[str]) -> None:
        if len(path) >= MAX_ROUTE_NODES:
            return
        for node in sorted(next_nodes.get(path[-1], ())):
            if node == "Y":
                if visited_stations == stations and len(path) > 1:
                    routes.add(path + (node,))
            elif node not in path:
                station = graph.stations.get(node)
                walk(path + (node,), visited_stations | ({station} if station else set()))

    walk(("Y",), frozenset())
    return tuple(sorted(routes))


def generate(graph: Graph, durations: dict[str, int], vehicles: set[str],
             scenario_start_at: datetime, start: datetime, end: datetime,
             interval_seconds: int, *, max_candidates: int = MAX_CANDIDATES,
             max_services: int = MAX_SERVICES) -> SearchResult:
    """Greedily reduce uncovered windows using bounded whole-second candidates.

    Returns a partial candidate and gaps on failure, never a success flag for it.
    No vehicle is created; each accepted service must pass the full validator.
    """
    if (not all(_utc_second(t) for t in (scenario_start_at, start, end))
            or start < scenario_start_at or end < start):
        raise ValueError("range must be ordered UTC seconds after scenario start")
    if type(interval_seconds) is not int or interval_seconds <= 0:
        raise ValueError("interval must be positive integer seconds")
    if type(max_candidates) is not int or max_candidates <= 0 or type(max_services) is not int or max_services <= 0:
        raise ValueError("search limits must be positive integers")
    routes = yard_routes(graph)
    if not routes or not vehicles:
        raise ValueError("at least one vehicle and a Y-to-Y route visiting every station are required")
    selected: tuple[Service, ...] = ()
    result = evaluate(graph, durations, vehicles, selected, scenario_start_at)
    gaps = uncovered(graph, result, start, end, interval_seconds)
    regular, attempts = _regular_headways(graph, durations, vehicles, scenario_start_at,
                                          start, end, interval_seconds, routes,
                                          max_candidates, max_services)
    if regular is not None:
        return regular
    # Candidate departures align station arrivals with the start or end of
    # an uncovered window. This is a heuristic, not an exhaustive proof.
    while gaps and len(selected) < max_services and attempts < max_candidates:
        options: set[tuple[datetime, tuple[str, ...]]] = set()
        for route in routes:
            offset = 0
            for node in route:
                if graph.nodes[node] == "BLOCK":
                    offset += durations[node]
                station = graph.stations.get(node)
                if station:
                    for gap in gaps:
                        if gap.station_id == station:
                            for target in (gap.start, gap.end, gap.start + timedelta(seconds=interval_seconds)):
                                departure = target - timedelta(seconds=offset)
                                if scenario_start_at <= departure <= end + timedelta(seconds=interval_seconds):
                                    options.add((departure, route))
        best = None
        best_score = _gap_score(gaps)
        for departure, route in sorted(options):
            for vehicle in sorted(vehicles):
                if attempts >= max_candidates:
                    break
                attempts += 1
                candidate = Service(len(selected) + 1, vehicle, departure,
                                    _route_steps(graph, route), True)
                tested = evaluate(graph, durations, vehicles, selected + (candidate,), scenario_start_at)
                if tested.status != "validated":
                    continue
                remaining = uncovered(graph, tested, start, end, interval_seconds)
                score = _gap_score(remaining)
                if score < best_score:
                    best, best_score = (candidate, tested, remaining), score
            if attempts >= max_candidates:
                break
        if best is None:
            break
        candidate, result, gaps = best
        selected += (candidate,)
    return SearchResult(selected, result, gaps, attempts, bool(gaps))


def _regular_headways(graph: Graph, durations: dict[str, int], vehicles: set[str],
                      scenario_start_at: datetime, start: datetime, end: datetime,
                      interval_seconds: int, routes: tuple[tuple[str, ...], ...],
                      max_candidates: int, max_services: int) -> tuple[SearchResult | None, int]:
    """Try periodic Y departures, accepting only domain- and coverage-valid plans.

    This is a search template, not a claim that Yard headway is the requirement.
    Each service in a periodic candidate consumes one unit of the shared budget.
    """
    attempts = 0
    fleet = sorted(vehicles)
    # Offsetting headway by one block-duration quantum can avoid an outgoing
    # Yard interlocking colliding with the next incoming service. Bound the
    # number of templates so the fallback still gets a search budget.
    quantum = reduce(gcd, durations.values(), interval_seconds)
    headways = tuple(dict.fromkeys(
        [interval_seconds - i * quantum for i in range(13) if interval_seconds - i * quantum > 0]
        + [max(1, interval_seconds // 2)]))
    for route in routes:
        # Starting at passenger range start covers every station if its first
        # arrival offset fits the wait interval. Earlier starts may help slower
        # routes; try the offset needed for their latest station as well.
        elapsed = 0
        latest_station = 0
        for node in route:
            if graph.nodes[node] == "BLOCK":
                elapsed += durations[node]
            if node in graph.stations:
                latest_station = max(latest_station, elapsed)
                elapsed += DEFAULT_DWELL_SECONDS
        phases = (start, start - timedelta(seconds=max(0, latest_station - interval_seconds)))
        for phase in dict.fromkeys(phases):
            if phase < scenario_start_at:
                continue
            for headway in headways:
                # One departure at or before end + interval may cover the
                # window end; avoid generating a partial plan as success.
                count = (end + timedelta(seconds=interval_seconds) - phase) // timedelta(seconds=headway) + 1
                if count < 1 or count > max_services or attempts + count > max_candidates:
                    continue
                steps = _route_steps(graph, route)
                services = tuple(Service(i + 1, fleet[i % len(fleet)],
                                         phase + timedelta(seconds=i * headway), steps, True)
                                 for i in range(count))
                # Evaluate the whole trajectory to catch interlocking, vehicle
                # continuity and Yard charging, not just departure spacing.
                attempts += count
                result = evaluate(graph, durations, vehicles, services, scenario_start_at)
                if result.status != "validated":
                    continue
                gaps = uncovered(graph, result, start, end, interval_seconds)
                if not gaps:
                    # Do not keep a tail run that adds no passenger coverage;
                    # validate after each trim rather than infer from Yard times.
                    while len(services) > 1:
                        shorter = services[:-1]
                        trimmed = evaluate(graph, durations, vehicles, shorter, scenario_start_at)
                        if (trimmed.status != "validated" or
                                uncovered(graph, trimmed, start, end, interval_seconds)):
                            break
                        services, result = shorter, trimmed
                    return SearchResult(services, result, (), attempts, False), attempts
    return None, attempts


def _route_steps(graph: Graph, route: tuple[str, ...]) -> tuple[Step, ...]:
    return tuple(Step(node, DEFAULT_DWELL_SECONDS if graph.nodes[node] == "PLATFORM" else 0)
                 for node in route)


def _gap_score(gaps: tuple[Gap, ...]) -> tuple[float, int]:
    """Compare uncovered duration, then remaining isolated boundary points."""
    return (sum((g.end - g.start).total_seconds() for g in gaps),
            sum(g.start_inclusive + g.end_inclusive for g in gaps))
