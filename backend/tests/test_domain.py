import unittest
from datetime import datetime, timedelta, timezone

from scheduling.domain import Service, Step, StructuralError, evaluate
from scheduling.topology import assignment_graph

T0 = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
GRAPH = assignment_graph()
DURATIONS = {f"B{i}": 12 for i in range(1, 15)}


def service(id, vehicle, seconds, *nodes, dwell=None):
    dwell = dwell or {}
    return Service(id, vehicle, T0 + timedelta(seconds=seconds),
                   tuple(Step(n, dwell.get(i, 0)) for i, n in enumerate(nodes)))


def run(*services, durations=None, vehicles=None):
    return evaluate(GRAPH, durations or DURATIONS, vehicles or {"V1", "V2"}, services, T0)


class TopologyTests(unittest.TestCase):
    def test_exact_nodes_and_directed_branches(self):
        self.assertEqual(len(GRAPH.nodes), 21)
        for a, b in (("Y", "B1"), ("B1", "Y"), ("P1A", "B1"),
                     ("B3", "B5"), ("B12", "B14")):
            self.assertIn((a, b), GRAPH.edges)
        self.assertNotIn(("B5", "B3"), GRAPH.edges)
        self.assertNotIn(("P2B", "B11"), GRAPH.edges)

    def test_invalid_path_and_inputs(self):
        cases = [
            (service(1, "V1", 0, "Y", "P1A"), "INVALID_EDGE"),
            (service(1, "V1", 0, "P2A", "B5", "B3", "P1A"), "INVALID_EDGE"),
            (service(1, "V1", 0, "Y", "B99"), "UNKNOWN_NODE"),
            (service(1, "V1", 0, "Y", "B1"), "INVALID_ENDPOINT"),
            (service(1, "V1", -1, "Y", "B1", "P1A"), "INVALID_TIME"),
            (service(1, "V1", 0, "Y", "B1", "P1A", dwell={2: -1}), "INVALID_DWELL"),
            (service(1, "V1", 0, "Y", "B1", "P1A", dwell={0: -1}), "INVALID_DWELL"),
            (service(1, "V1", 0, "Y", "B1", "P1A", dwell={1: 1}), "INVALID_DWELL"),
            (service(1, "V9", 0, "Y", "B1", "P1A"), "UNKNOWN_VEHICLE"),
        ]
        for s, code in cases:
            with self.subTest(code=code), self.assertRaises(StructuralError) as error:
                run(s)
            self.assertEqual(error.exception.code, code)
        with self.assertRaises(StructuralError):
            run(service(1, "V1", 0, "Y", "B1", "P1A"), durations={"B1": 0})


class TimelineTests(unittest.TestCase):
    def test_times_dwell_and_repeated_platform(self):
        s = service(1, "V1", 0, "Y", "B1", "P1A", "B3", "B5", "P2A",
                    "B6", "B7", "P3A", "B10", "B11", "P2B", "B12", "B13", "P1A",
                    dwell={2: 5, 14: 7})
        result = run(s)
        self.assertEqual(result.status, "validated")
        visits = result.timelines[0].visits
        self.assertEqual(visits[2].arrival, T0 + timedelta(seconds=12))
        self.assertEqual(visits[2].departure, T0 + timedelta(seconds=17))
        self.assertEqual(visits[-1].arrival, T0 + timedelta(seconds=113))
        self.assertEqual(result.timelines[0].end_at, T0 + timedelta(seconds=120))
        self.assertEqual(result.timelines[0].blocks[1].start, T0 + timedelta(seconds=17))
        self.assertEqual(result.timelines[0].blocks[1].end, T0 + timedelta(seconds=29))

    def test_half_open_block_and_interlocking(self):
        a = service(1, "V1", 0, "Y", "B1", "P1A")
        b = service(2, "V2", 12, "Y", "B1", "P1A")
        self.assertEqual(run(a, b).status, "validated")
        b = service(2, "V2", 11, "Y", "B1", "P1A")
        codes = {c.code for c in run(a, b).conflicts}
        self.assertIn("BLOCK_OVERLAP", codes)
        self.assertIn("INTERLOCKING_OVERLAP", codes)
        b = service(2, "V2", 0, "Y", "B2", "P1B")
        codes = {c.code for c in run(a, b).conflicts}
        self.assertNotIn("BLOCK_OVERLAP", codes)
        self.assertIn("INTERLOCKING_OVERLAP", codes)

    def test_continuity_and_unknown_battery_after_overlap(self):
        a = service(1, "V1", 0, "Y", "B1", "P1A")
        b = service(2, "V1", 6, "P1A", "B3", "B5", "P2A")
        c = service(3, "V1", 40, "P2A", "B6", "B7", "P3A")
        result = run(a, b, c)
        self.assertIn("VEHICLE_OVERLAP", {x.code for x in result.conflicts})
        self.assertEqual(result.unknown_from["V1"], b.start_at)
        self.assertIsNone(result.timelines[1].visits[1].battery)
        self.assertIsNone(result.timelines[2].visits[1].battery)
        fixed = run(a, service(2, "V1", 12, "P1A", "B3", "B5", "P2A"))
        self.assertEqual(fixed.status, "validated")
        wrong = run(service(1, "V1", 0, "P1A", "B3", "B5", "P2A"))
        self.assertIn("LOCATION_DISCONTINUITY", {x.code for x in wrong.conflicts})
        self.assertIsNone(wrong.timelines[0].visits[0].battery)

    def test_broken_vehicle_does_not_hide_resource_conflicts_or_other_batteries(self):
        first = service(1, "V1", 0, "Y", "B1", "P1A")
        overlap = service(2, "V1", 6, "P1A", "B3", "B5", "P2A")
        other = service(3, "V2", 0, "Y", "B2", "P1B")
        result = run(overlap, other, first)  # input order does not determine timeline order
        self.assertEqual([t.service.id for t in result.timelines], [1, 3, 2])
        self.assertEqual(result.status, "draft")
        self.assertEqual(result.unknown_from, {"V1": overlap.start_at})
        self.assertEqual({c.code for c in result.conflicts},
                         {"VEHICLE_OVERLAP", "INTERLOCKING_OVERLAP"})
        self.assertEqual(result.timelines[0].visits[-1].battery, 79)
        self.assertEqual(result.timelines[1].visits[-1].battery, 79)
        self.assertTrue(all(v.battery is None for v in result.timelines[2].visits))

    def test_location_gap_marks_only_later_vehicle_runs_unknown(self):
        first = service(1, "V1", 0, "Y", "B1", "P1A")
        gap = service(2, "V1", 24, "P2A", "B6", "B7", "P3A")
        later = service(3, "V1", 48, "P3A", "B10", "B11", "P2B")
        result = run(first, gap, later)
        self.assertEqual(result.unknown_from["V1"], gap.start_at)
        self.assertEqual([c.code for c in result.conflicts], ["LOCATION_DISCONTINUITY"])
        self.assertEqual(result.timelines[0].visits[-1].battery, 79)
        self.assertTrue(all(v.battery is None for t in result.timelines[1:]
                            for v in t.visits))

    def test_battery_charge_between_services_and_threshold(self):
        loop = ("Y", "B1", "P1A", "B1", "Y")
        # Each trip costs two units. Without a Yard wait, departure after 1 trip is below 80.
        first = service(1, "V1", 0, *loop)
        second = service(2, "V1", 24, *loop)
        self.assertIn("INSUFFICIENT_CHARGE", {x.code for x in run(first, second).conflicts})
        second = service(2, "V1", 48, *loop)
        result = run(first, second)
        self.assertEqual(result.status, "validated")
        self.assertEqual(result.timelines[1].visits[0].battery, 80)
        partial = run(first, service(2, "V1", 30, *loop))
        self.assertEqual(partial.timelines[1].visits[0].battery, 78.5)
        self.assertIn("INSUFFICIENT_CHARGE", {x.code for x in partial.conflicts})
        late = run(service(1, "V1", 246, *loop))
        self.assertEqual(late.timelines[0].visits[0].battery, 100)

    def test_yard_dwell_charges_during_service_and_across_runs(self):
        first = service(1, "V1", 0, "Y", "B1", "P1A", "B1", "Y", dwell={4: 24})
        second = service(2, "V1", 48, "Y", "B1", "P1A")
        result = run(first, second)
        self.assertEqual(result.status, "validated")
        self.assertEqual(result.timelines[0].visits[-1].arrival, T0 + timedelta(seconds=24))
        self.assertEqual(result.timelines[0].end_at, second.start_at)
        self.assertEqual(result.timelines[0].visits[-1].battery, 80)
        self.assertEqual(result.timelines[1].visits[0].battery, 80)
        initial = run(service(1, "V1", 0, "Y", "B1", "P1A", dwell={0: 24}))
        self.assertEqual(initial.timelines[0].visits[0].departure, T0 + timedelta(seconds=24))
        self.assertEqual(initial.timelines[0].visits[0].battery, 82)
        self.assertEqual(initial.timelines[0].blocks[0].start, T0 + timedelta(seconds=24))
        capped = run(service(1, "V1", 0, "Y", "B1", "P1A", dwell={0: 300}))
        self.assertEqual(capped.timelines[0].visits[0].battery, 100)

    def test_cross_midnight_and_config_recompute(self):
        near_midnight = service(1, "V1", 15 * 3600 + 59 * 60 + 55, "Y", "B1", "P1A")
        before = run(near_midnight)
        after = run(near_midnight, durations={**DURATIONS, "B1": 20})
        self.assertEqual(after.timelines[0].visits[-1].arrival.date(),
                         (T0 + timedelta(days=1)).date())
        self.assertEqual((after.timelines[0].end_at - before.timelines[0].end_at).total_seconds(), 8)

    def test_battery_crosses_30_on_long_loop(self):
        circuit = ("B3", "B5", "P2A", "B6", "B7", "P3A", "B10", "B11",
                   "P2B", "B12", "B13", "P1A")
        s = service(1, "V1", 0, "Y", "B1", "P1A", *(circuit * 7), "B1", "Y")
        result = run(s)
        self.assertIn("LOW_BATTERY", {c.code for c in result.conflicts})
        levels = [v.battery for v in result.timelines[0].visits if v.node.startswith("B")]
        self.assertIn(30, levels)
        self.assertIn(29, levels)


if __name__ == "__main__":
    unittest.main()
