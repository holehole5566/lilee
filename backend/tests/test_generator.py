import unittest
from datetime import datetime, timedelta, timezone

from scheduling.domain import evaluate
from scheduling.generator import generate, yard_routes
from scheduling.topology import assignment_graph

T0 = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
GRAPH = assignment_graph()
DURATIONS = {f"B{i}": 12 for i in range(1, 15)}


class GeneratorTests(unittest.TestCase):
    def test_routes_are_directed_yard_loops_covering_all_stations(self):
        routes = yard_routes(GRAPH)
        self.assertTrue(routes)
        for route in routes:
            self.assertEqual((route[0], route[-1]), ("Y", "Y"))
            self.assertEqual({GRAPH.stations[n] for n in route if n in GRAPH.stations},
                             {"S1", "S2", "S3"})
            self.assertTrue(all((a, b) in GRAPH.edges for a, b in zip(route, route[1:])))

    def test_short_range_finds_complete_valid_candidate(self):
        vehicles = {"V1", "V2"}
        candidate = generate(GRAPH, DURATIONS, vehicles, T0,
                             T0 + timedelta(seconds=12), T0 + timedelta(seconds=42), 240)
        self.assertTrue(candidate.complete, candidate.gaps)
        self.assertTrue(candidate.services)
        self.assertTrue(all(s.is_passenger_service and s.vehicle_id in vehicles
                            and all(step.dwell_seconds == (60 if GRAPH.nodes[step.node] == 'PLATFORM' else 0)
                                    for step in s.steps)
                            for s in candidate.services))
        self.assertEqual(evaluate(GRAPH, DURATIONS, vehicles, candidate.services, T0).status,
                         "validated")

    def test_longer_range_reuses_vehicle_only_after_yard_recharge(self):
        # A loop uses 10 battery units; 60s dwell at each platform makes it
        # 480s long. The same vehicle needs at least 120s in Y to recharge.
        result = generate(GRAPH, DURATIONS, {"V1"}, T0,
                          T0 + timedelta(seconds=12), T0 + timedelta(seconds=360), 600)
        self.assertTrue(result.complete, result.gaps)
        self.assertGreaterEqual(len(result.services), 2)
        self.assertEqual({s.vehicle_id for s in result.services}, {"V1"})
        timelines = result.evaluation.timelines
        for previous, following in zip(timelines, timelines[1:]):
            self.assertEqual(previous.visits[-1].node, "Y")
            self.assertEqual(following.visits[0].node, "Y")
            self.assertGreaterEqual((following.service.start_at - previous.end_at).total_seconds(), 120)
            self.assertGreaterEqual(following.visits[0].battery, 80)
        self.assertEqual(result.evaluation.status, "validated")
        self.assertEqual(result.gaps, ())

    def test_two_hour_window_with_two_vehicles_and_ten_minute_wait(self):
        # Previously the greedy search exhausted 2,000 candidates and left
        # S2/S3 gaps. A regular headway is just a candidate; coverage, battery
        # and resource intervals still must pass the authoritative validators.
        from scheduling.coverage import uncovered
        durations = {block: 60 for block in DURATIONS}
        vehicles = {"V1", "V2"}
        result = generate(GRAPH, durations, vehicles, T0,
                          T0 + timedelta(hours=6), T0 + timedelta(hours=8), 600,
                          max_candidates=32)
        self.assertTrue(result.complete, result.gaps)
        self.assertLessEqual(result.attempts, 32)
        self.assertEqual(result.evaluation.status, "validated")
        self.assertEqual(uncovered(GRAPH, result.evaluation,
                                   T0 + timedelta(hours=6), T0 + timedelta(hours=8), 600), ())
        self.assertEqual({s.vehicle_id for s in result.services}, vehicles)
        self.assertTrue(all(s.is_passenger_service for s in result.services))

    def test_headway_adjusts_to_yard_interlocking_with_longer_blocks(self):
        # 60s platform dwell lengthens a loop. With longer B1/B2/B3 and
        # three vehicles, 540s spacing avoids G1 and covers a 600s wait.
        durations = {**{block: 60 for block in DURATIONS},
                     "B1": 120, "B2": 120, "B3": 120}
        result = generate(GRAPH, durations, {"V1", "V2", "V3"}, T0,
                          T0 + timedelta(hours=6), T0 + timedelta(hours=8), 600,
                          max_candidates=60)
        self.assertTrue(result.complete, result.gaps)
        self.assertEqual(result.evaluation.status, "validated")
        self.assertEqual(result.gaps, ())
        self.assertEqual((result.services[1].start_at - result.services[0].start_at).total_seconds(), 540)

    def test_default_service_limit_allows_more_than_32_runs(self):
        durations = {block: 60 for block in DURATIONS}
        result = generate(GRAPH, durations, {'V1', 'V2'}, T0,
                          T0 + timedelta(hours=6), T0 + timedelta(hours=12), 600,
                          max_candidates=128)
        self.assertTrue(result.complete, result.gaps)
        self.assertGreater(len(result.services), 32)
        self.assertLessEqual(len(result.services), 128)

    def test_limited_search_never_reports_partial_as_success(self):
        candidate = generate(GRAPH, DURATIONS, {"V1"}, T0,
                             T0 + timedelta(seconds=12), T0 + timedelta(seconds=42), 24,
                             max_candidates=1)
        self.assertFalse(candidate.complete)
        self.assertTrue(candidate.exhausted)
        self.assertTrue(candidate.gaps)
        self.assertLessEqual(candidate.attempts, 1)

    def test_rejects_invalid_range_and_empty_fleet(self):
        with self.assertRaises(ValueError):
            generate(GRAPH, DURATIONS, {"V1"}, T0, T0 - timedelta(seconds=1), T0, 48)
        with self.assertRaises(ValueError):
            generate(GRAPH, DURATIONS, set(), T0, T0, T0, 48)


if __name__ == "__main__":
    unittest.main()
