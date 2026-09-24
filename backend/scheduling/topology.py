"""Versioned seed data for the assignment's directed track graph.

The persistence layer will seed these rows into PostgreSQL; the domain engine
accepts a loaded Graph rather than reading this module implicitly.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Graph:
    nodes: dict[str, str]  # node -> YARD, PLATFORM or BLOCK
    edges: frozenset[tuple[str, str]]
    stations: dict[str, str]  # platform -> station
    interlockings: dict[str, frozenset[str]]


def assignment_graph() -> Graph:
    stations = {f"P{s}{p}": f"S{s}" for s in range(1, 4) for p in "AB"}
    nodes = {"Y": "YARD", **dict.fromkeys(stations, "PLATFORM"),
             **{f"B{i}": "BLOCK" for i in range(1, 15)}}
    routes = (
        ("Y", "B1", "P1A"), ("Y", "B2", "P1B"),
        ("P1A", "B3", "B5", "P2A"), ("P1B", "B4", "B5", "P2A"),
        ("P2A", "B6", "B7", "P3A"), ("P2A", "B6", "B8", "P3B"),
        ("P3A", "B10", "B11", "P2B"), ("P3B", "B9", "B11", "P2B"),
        ("P2B", "B12", "B14", "P1B"), ("P2B", "B12", "B13", "P1A"),
    )
    edges = {(a, b) for route in routes for a, b in zip(route, route[1:])}
    edges.update({(b, a) for a, b in tuple(edges) if a == "Y" or b == "Y" or
                  (a == "B1" and b == "P1A") or (a == "B2" and b == "P1B")})
    return Graph(nodes, frozenset(edges), stations, {
        "G1": frozenset(("B1", "B2")),
        "G2": frozenset(("B3", "B4", "B13", "B14")),
        "G3": frozenset(("B7", "B8", "B9", "B10")),
    })
