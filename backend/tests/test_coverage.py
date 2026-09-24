import unittest
from datetime import datetime, timedelta, timezone
from scheduling.coverage import uncovered
from scheduling.domain import Service, Step, evaluate
from scheduling.topology import assignment_graph

T0 = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
DURATIONS = {f"B{i}": 12 for i in range(1, 15)}
GRAPH = assignment_graph()
LOOP = ("Y", "B1", "P1A", "B3", "B5", "P2A", "B6", "B7", "P3A",
        "B10", "B11", "P2B", "B12", "B14", "P1B", "B2", "Y")


def run(starts, passenger=True):
    services = tuple(Service(i, f"V{i}", T0 + timedelta(seconds=t),
                             tuple(Step(n) for n in LOOP), passenger)
                     for i, t in enumerate(starts, 1))
    return evaluate(GRAPH, DURATIONS, {f"V{i}" for i in range(1, len(starts) + 1)}, services, T0)


class CoverageTests(unittest.TestCase):
    def test_feasible_tiny_scenario_and_end_extension(self):
        result = run((0, 30))
        self.assertEqual(result.status, "validated")
        # At t=12, S3's first passenger arrival is t=60, exactly 48s later.
        self.assertEqual(uncovered(GRAPH, result, T0 + timedelta(seconds=12),
                                   T0 + timedelta(seconds=42), 48), ())
        # With only the first service, the arrival at t=60 still covers range end t=12.
        self.assertEqual(uncovered(GRAPH, run((0,)), T0 + timedelta(seconds=12),
                                   T0 + timedelta(seconds=12), 48), ())

    def test_missing_coverage_and_legacy_flag_does_not_hide_platforms(self):
        result = run((0,))
        gaps = uncovered(GRAPH, result, T0 + timedelta(seconds=12),
                         T0 + timedelta(seconds=42), 24)
        self.assertTrue(any(g.station_id == "S3" and g.start == T0 + timedelta(seconds=12)
                            for g in gaps))
        # Older stored services may still carry the obsolete false flag;
        # stops at platforms remain boardable regardless of that metadata.
        legacy = run((0, 30), passenger=False)
        self.assertEqual(uncovered(GRAPH, legacy, T0 + timedelta(seconds=12),
                                   T0 + timedelta(seconds=42), 48), ())

    def test_passenger_can_board_during_dwell_including_window_start_and_departure(self):
        stop = Service(1, "V1", T0, (Step("Y"), Step("B1"), Step("P1A", 60)))
        result = evaluate(GRAPH, DURATIONS, {"V1"}, (stop,), T0)
        self.assertEqual(result.timelines[0].visits[-1].arrival, T0 + timedelta(seconds=12))
        self.assertEqual(result.timelines[0].visits[-1].departure, T0 + timedelta(seconds=72))
        gaps = uncovered(GRAPH, result, T0 + timedelta(seconds=30),
                         T0 + timedelta(seconds=72), 1)
        self.assertFalse(any(g.station_id == "S1" for g in gaps))
        after = uncovered(GRAPH, result, T0 + timedelta(seconds=73),
                          T0 + timedelta(seconds=73), 1)
        self.assertTrue(any(g.station_id == "S1" for g in after))
        without_dwell = evaluate(GRAPH, DURATIONS, {"V1"},
                                 (Service(1, "V1", T0, (Step("Y"), Step("B1"), Step("P1A"))),), T0)
        self.assertTrue(any(g.station_id == "S1" for g in uncovered(
            GRAPH, without_dwell, T0 + timedelta(seconds=30), T0 + timedelta(seconds=30), 1)))
        legacy = evaluate(GRAPH, DURATIONS, {"V1"},
                          (Service(1, "V1", T0, stop.steps, False),), T0)
        self.assertFalse(any(g.station_id == "S1" for g in uncovered(
            GRAPH, legacy, T0 + timedelta(seconds=30), T0 + timedelta(seconds=30), 1)))

    def test_one_second_gap_not_smoothed_over(self):
        # S3: 60s and 91s. With interval=30s, t strictly between 60 and 61 is uncovered.
        result = run((0, 31))
        self.assertEqual(result.status, "validated")
        gaps = uncovered(GRAPH, result, T0 + timedelta(seconds=60),
                         T0 + timedelta(seconds=61), 30)
        self.assertTrue(any(g.station_id == "S3" and g.start == T0 + timedelta(seconds=60)
                            and g.end == T0 + timedelta(seconds=61)
                            and not g.start_inclusive and not g.end_inclusive for g in gaps))

    def test_input_boundaries(self):
        with self.assertRaises(ValueError):
            uncovered(GRAPH, run(()), T0, T0, 0)
        with self.assertRaises(ValueError):
            uncovered(GRAPH, run(()), T0 + timedelta(seconds=1), T0, 30)


if __name__ == "__main__":
    unittest.main()
