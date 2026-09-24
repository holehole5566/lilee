"""Run against disposable PostgreSQL: RUN_DB_TESTS=1 python -m unittest discover -s tests."""
import os
import uuid
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

if os.environ.get("RUN_DB_TESTS") == "1":
    from fastapi.testclient import TestClient
    from scheduling.api import app
    from scheduling.db import Session, VehicleRow, ScheduleRow, ServiceRow, StepRow
    from sqlalchemy import text, select
    from sqlalchemy.exc import IntegrityError


@unittest.skipUnless(os.environ.get("RUN_DB_TESTS") == "1", "requires PostgreSQL and RUN_DB_TESTS=1")
class ApiIntegrationTests(unittest.TestCase):
    def test_health_with_two_scenarios(self):
        with TestClient(app) as client:
            response = client.get('/api/health')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {'status': 'ok'})

    def test_migration_version_and_idempotent_seed(self):
        with Session() as db:
            self.assertEqual(db.scalar(text("SELECT version_num FROM alembic_version")), "002")
            self.assertIsNotNone(db.get(ScheduleRow, 1))
            self.assertIsNotNone(db.get(ScheduleRow, 2))
            self.assertEqual(db.scalar(text("SELECT count(*) FROM track_nodes")), 21)
            self.assertEqual(db.scalar(text("SELECT count(*) FROM track_edges")), 28)
            self.assertEqual(db.scalar(text("SELECT count(*) FROM block_configs")), 14)

    def test_manual_yard_dwell_preview_persistence_and_block_endpoint(self):
        from datetime import datetime, timedelta
        with TestClient(app) as client:
            vehicle = "T" + uuid.uuid4().hex[:12]
            self.assertEqual(client.post('/api/vehicles', json={'id': vehicle, 'name': 'Yard dwell'}).status_code, 201)
            before = client.get('/api/schedule').json()
            start = before['scenario_start_at']
            data = {'vehicle_id': vehicle, 'start_at': start,
                    'steps': [{'node': 'Y', 'dwell_seconds': 24}, {'node': 'B1'}, {'node': 'P1A'}]}
            created_id = None
            try:
                invalid = {**data, 'steps': data['steps'][:2]}
                rejected = client.post('/api/schedule/validate', json={'operation': 'create', 'service': invalid})
                self.assertEqual(rejected.status_code, 422, rejected.text)
                self.assertEqual(rejected.json()['detail']['code'], 'INVALID_ENDPOINT')
                self.assertEqual(client.post('/api/services', json=invalid).status_code, 422)
                preview = client.post('/api/schedule/validate', json={'operation': 'create', 'service': data})
                self.assertEqual(preview.status_code, 200, preview.text)
                proposed = next(t for t in preview.json()['candidate']['timelines']
                                if t['service']['vehicle_id'] == vehicle)
                self.assertEqual(proposed['visits'][0]['battery'], 82)
                self.assertEqual(proposed['visits'][0]['departure'],
                                 (datetime.fromisoformat(start) + timedelta(seconds=24)).isoformat())
                self.assertEqual(client.get('/api/schedule').json()['revision'], before['revision'])
                created = client.post('/api/services', json=data)
                self.assertEqual(created.status_code, 201, created.text)
                created_id = created.json()['id']
                saved = client.get('/api/schedule').json()
                run = next(t for t in saved['timelines'] if t['service']['id'] == created_id)
                self.assertEqual(run['service']['steps'][0]['dwell_seconds'], 24)
                self.assertEqual(run['blocks'][0]['start'], proposed['blocks'][0]['start'])
            finally:
                if created_id: client.delete(f'/api/services/{created_id}')
                client.delete(f'/api/vehicles/{vehicle}')

    def test_database_fk_restrict_even_without_api(self):
        with TestClient(app) as client:
            vehicle = "T" + uuid.uuid4().hex[:12]
            self.assertEqual(client.post("/api/vehicles", json={"id": vehicle, "name": "Test"}).status_code, 201)
            start = client.get("/api/schedule").json()["scenario_start_at"]
            created = client.post("/api/services", json={
                "vehicle_id": vehicle, "start_at": start,
                "steps": [{"node": "Y"}, {"node": "B1"}, {"node": "P1A"}]})
            self.assertEqual(created.status_code, 201, created.text)
            try:
                with self.assertRaises(IntegrityError):
                    with Session.begin() as db:
                        db.delete(db.get(VehicleRow, vehicle))
                        db.flush()
                self.assertEqual(client.get(f"/api/vehicles/{vehicle}").status_code, 200)
            finally:
                client.delete(f"/api/services/{created.json()['id']}")
                client.delete(f"/api/vehicles/{vehicle}")

    def test_manual_and_auto_are_alternative_schedules(self):
        with TestClient(app) as client:
            vehicle = "T" + uuid.uuid4().hex[:12]
            self.assertEqual(client.post("/api/vehicles", json={"id": vehicle, "name": "Alternative"}).status_code, 201)
            start = client.get("/api/schedule").json()["scenario_start_at"]
            service = {"vehicle_id": vehicle, "start_at": start,
                       "steps": [{"node": "Y"}, {"node": "B1"}, {"node": "P1A"}]}
            response = client.post("/api/services", json=service)
            self.assertEqual(response.status_code, 201, response.text)
            manual_id = response.json()["id"]
            auto_id = None
            try:
                # No generator yet: seed a valid auto run directly to test isolation.
                with Session.begin() as db:
                    auto_row = db.get(ScheduleRow, 2)
                    auto = ServiceRow(schedule_id=2, vehicle_id=vehicle,
                                      start_at=auto_row.scenario_start_at, is_passenger_service=True)
                    db.add(auto)
                    db.flush()
                    auto_id = auto.id
                    db.add_all([StepRow(service_id=auto_id, sequence_index=i, node_id=node,
                                        dwell_seconds=0) for i, node in enumerate(("Y", "B1", "P1A"))])
                    auto_row.revision += 1
                manual = client.get("/api/schedule").json()
                auto_schedule = client.get("/api/schedule?scenario=auto").json()
                self.assertEqual(manual["status"], "validated")
                self.assertEqual(auto_schedule["status"], "validated")
                self.assertEqual({t["service"]["id"] for t in manual["timelines"]}, {manual_id})
                self.assertEqual({t["service"]["id"] for t in auto_schedule["timelines"]}, {auto_id})
                self.assertEqual(client.get(f"/api/services/{auto_id}").status_code, 404)
                self.assertEqual(client.delete(f"/api/services/{auto_id}").status_code, 404)
                self.assertEqual(client.delete(f"/api/vehicles/{vehicle}").status_code, 409)
                preview = client.post("/api/blocks/B1/preview", json={"traversal_seconds": 90}).json()
                self.assertEqual([x["service_id"] for x in preview["affected_services"]], [manual_id])
                self.assertEqual([x["service_id"] for x in preview["auto_affected_services"]], [auto_id])
                self.assertEqual(client.delete(f"/api/services/{manual_id}").status_code, 200)
                self.assertEqual(client.get("/api/schedule?scenario=auto").json()["timelines"][0]["service"]["id"], auto_id)
                self.assertEqual(client.delete(f"/api/vehicles/{vehicle}").status_code, 409)
            finally:
                client.delete(f"/api/services/{manual_id}")
                if auto_id:
                    with Session.begin() as db:
                        db.query(StepRow).filter(StepRow.service_id == auto_id).delete(synchronize_session=False)
                        db.delete(db.get(ServiceRow, auto_id))
                        db.get(ScheduleRow, 2).revision += 1
                client.delete(f"/api/vehicles/{vehicle}")

    def test_generator_preview_commit_stale_and_atomic_replace(self):
        with TestClient(app) as client:
            if client.get("/api/schedule?scenario=auto").json()["timelines"]:
                self.skipTest("requires disposable empty auto scenario")
            vehicles = ["T" + uuid.uuid4().hex[:12] for _ in range(2)]
            for vehicle in vehicles:
                self.assertEqual(client.post("/api/vehicles", json={"id": vehicle, "name": "Generator"}).status_code, 201)
            try:
                before_auto = client.get("/api/schedule?scenario=auto").json()
                before_manual = client.get("/api/schedule").json()
                from datetime import datetime, timedelta
                start = datetime.fromisoformat(before_auto["scenario_start_at"].replace("Z", "+00:00"))
                # Default block duration is 60s; use a wide interval for a short feasible range.
                payload = {"vehicle_ids": [vehicles[0]],
                           "start": (start + timedelta(seconds=60)).isoformat(),
                           "end": (start + timedelta(seconds=60)).isoformat(),
                           "interval_seconds": 600}
                preview = client.post("/api/schedule/generate/preview", json=payload)
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertTrue(preview.json()["complete"], preview.text)
                self.assertEqual(client.get("/api/schedule?scenario=auto").json(), before_auto)
                self.assertEqual(client.post("/api/schedule/generate/commit", json={**payload,
                    "expected_revision": before_auto["revision"] + 1}).status_code, 409)
                saved = client.post("/api/schedule/generate/commit", json={**payload,
                    "expected_revision": before_auto["revision"]})
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertEqual(saved.json()["status"], "validated")
                self.assertEqual(saved.json()["revision"], before_auto["revision"] + 1)
                old_ids = {t["service"]["id"] for t in saved.json()["timelines"]}
                self.assertTrue(old_ids)
                self.assertEqual(client.get("/api/schedule").json(), before_manual)
                self.assertEqual(client.post("/api/schedule/generate/commit", json={**payload,
                    "expected_revision": before_auto["revision"]}).status_code, 409)
                incomplete = client.post("/api/schedule/generate/commit", json={**payload,
                    "interval_seconds": 1, "expected_revision": saved.json()["revision"]})
                self.assertEqual(incomplete.status_code, 409, incomplete.text)
                self.assertEqual(incomplete.json()["detail"]["code"], "GENERATION_INCOMPLETE")
                self.assertEqual(client.get("/api/schedule?scenario=auto").json(), saved.json())
                replacement = client.post("/api/schedule/generate/commit", json={**payload,
                    "vehicle_ids": [vehicles[1]], "expected_revision": saved.json()["revision"]})
                self.assertEqual(replacement.status_code, 200, replacement.text)
                new_ids = {t["service"]["id"] for t in replacement.json()["timelines"]}
                self.assertFalse(old_ids & new_ids)
                self.assertEqual({t["service"]["vehicle_id"] for t in replacement.json()["timelines"]},
                                 {vehicles[1]})
                self.assertEqual(client.get("/api/schedule").json(), before_manual)
                # Two writers racing on the same auto revision cannot both commit.
                revision = replacement.json()["revision"]
                barrier = Barrier(2)
                def attempt():
                    barrier.wait()
                    return client.post("/api/schedule/generate/commit", json={**payload,
                        "vehicle_ids": [vehicles[1]], "expected_revision": revision}).status_code
                with ThreadPoolExecutor(max_workers=2) as pool:
                    self.assertEqual(sorted(pool.map(lambda _: attempt(), range(2))), [200, 409])
                self.assertEqual(client.get("/api/schedule?scenario=auto").json()["revision"], revision + 1)
            finally:
                with Session.begin() as db:
                    ids = list(db.scalars(select(ServiceRow.id).where(ServiceRow.schedule_id == 2)))
                    if ids:
                        db.query(StepRow).filter(StepRow.service_id.in_(ids)).delete(synchronize_session=False)
                        db.query(ServiceRow).filter(ServiceRow.id.in_(ids)).delete(synchronize_session=False)
                        db.get(ScheduleRow, 2).revision += 1
                for vehicle in vehicles:
                    client.delete(f"/api/vehicles/{vehicle}")

    def test_generator_recharge_and_reuse_persists(self):
        with TestClient(app) as client:
            if client.get("/api/schedule?scenario=auto").json()["timelines"]:
                self.skipTest("requires disposable empty auto scenario")
            vehicle = "T" + uuid.uuid4().hex[:12]
            self.assertEqual(client.post("/api/vehicles", json={"id": vehicle, "name": "Recharge"}).status_code, 201)
            try:
                from datetime import datetime, timedelta
                auto = client.get("/api/schedule?scenario=auto").json()
                origin = datetime.fromisoformat(auto["scenario_start_at"].replace("Z", "+00:00"))
                payload = {"vehicle_ids": [vehicle], "start": (origin + timedelta(seconds=60)).isoformat(),
                           "end": (origin + timedelta(seconds=1800)).isoformat(), "interval_seconds": 1200}
                preview = client.post("/api/schedule/generate/preview", json=payload)
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertTrue(preview.json()["complete"], preview.text)
                saved = client.post("/api/schedule/generate/commit", json={
                    **payload, "expected_revision": preview.json()["base_revision"]})
                self.assertEqual(saved.status_code, 200, saved.text)
                persisted = client.get("/api/schedule?scenario=auto").json()
                self.assertEqual(persisted["timelines"], saved.json()["timelines"])
                runs = persisted["timelines"]
                self.assertGreaterEqual(len(runs), 2)
                self.assertEqual({t["service"]["vehicle_id"] for t in runs}, {vehicle})
                for previous, following in zip(runs, runs[1:]):
                    previous_end = datetime.fromisoformat(previous["end_at"])
                    next_start = datetime.fromisoformat(following["service"]["start_at"])
                    self.assertGreaterEqual((next_start - previous_end).total_seconds(), 120)
                    self.assertGreaterEqual(following["visits"][0]["battery"], 80)
            finally:
                with Session.begin() as db:
                    ids = list(db.scalars(select(ServiceRow.id).where(ServiceRow.schedule_id == 2)))
                    if ids:
                        db.query(StepRow).filter(StepRow.service_id.in_(ids)).delete(synchronize_session=False)
                        db.query(ServiceRow).filter(ServiceRow.id.in_(ids)).delete(synchronize_session=False)
                        db.get(ScheduleRow, 2).revision += 1
                client.delete(f"/api/vehicles/{vehicle}")

    def test_mutations_draft_recovery_preview_and_rejection(self):
        with TestClient(app) as client:
            vehicle = "T" + uuid.uuid4().hex[:12]
            self.assertEqual(client.post("/api/vehicles", json={"id": vehicle, "name": "Test"}).status_code, 201)
            self.assertEqual(client.post("/api/vehicles", json={"id": vehicle, "name": "Test"}).status_code, 409)
            start = client.get("/api/schedule").json()["scenario_start_at"]
            service = {"vehicle_id": vehicle, "start_at": start,
                       "steps": [{"node": "Y"}, {"node": "B1"}, {"node": "P1A", "dwell_seconds": 5}]}
            # Manual API no longer accepts a non-passenger/repositioning mode.
            rejected = client.post("/api/services", json={**service, "is_passenger_service": False})
            self.assertEqual(rejected.status_code, 422)
            invalid = client.post("/api/services", json={**service, "steps": [{"node": "Y"}, {"node": "P1A"}]})
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(invalid.json()["detail"]["code"], "INVALID_EDGE")
            before_preview = client.get("/api/schedule").json()
            preview = client.post("/api/schedule/validate", json={
                "operation": "create", "service": service})
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()["base_revision"], before_preview["revision"])
            self.assertEqual(len(preview.json()["candidate"]["timelines"]), len(before_preview["timelines"]) + 1)
            self.assertEqual(client.get("/api/schedule").json(), before_preview)
            created = client.post("/api/services", json=service)
            self.assertEqual(created.status_code, 201, created.text)
            sid = created.json()["id"]
            self.assertTrue(created.json()["timelines"][-1]["service"]["is_passenger_service"])
            try:
                self.assertEqual(client.delete(f"/api/vehicles/{vehicle}").status_code, 409)
                self.assertEqual(client.get(f"/api/services/{sid}").json()["visits"][-1]["node"], "P1A")
                proposed = client.post("/api/schedule/validate", json={
                    "operation": "update", "service_id": sid,
                    "service": {**service, "steps": [{"node": "P1A"}, {"node": "B3"},
                                                    {"node": "B5"}, {"node": "P2A"}]}})
                self.assertEqual(proposed.json()["candidate"]["status"], "draft")
                self.assertEqual(client.get(f"/api/services/{sid}").json()["visits"][0]["node"], "Y")
                # A valid graph path but impossible initial location is storable as draft.
                draft = client.put(f"/api/services/{sid}", json={
                    **service, "steps": [{"node": "P1A"}, {"node": "B3"},
                                         {"node": "B5"}, {"node": "P2A"}]})
                self.assertEqual(draft.status_code, 200, draft.text)
                self.assertEqual(draft.json()["status"], "draft")
                self.assertIn("LOCATION_DISCONTINUITY", {c["code"] for c in draft.json()["conflicts"]})
                repaired = client.put(f"/api/services/{sid}", json=service)
                self.assertEqual(repaired.json()["status"], "validated")
                deletion = client.post("/api/schedule/validate", json={
                    "operation": "delete", "service_id": sid})
                self.assertEqual(deletion.json()["candidate"]["status"], "validated")
                self.assertEqual(client.get(f"/api/services/{sid}").status_code, 200)
                before = client.get("/api/schedule").json()
                auto_before = client.get("/api/schedule?scenario=auto").json()
                preview = client.post("/api/blocks/B1/preview", json={"traversal_seconds": 90}).json()
                self.assertEqual(preview["base_revision"], before["revision"])
                self.assertEqual(preview["base_auto_revision"], auto_before["revision"])
                self.assertEqual(len(preview["affected_services"]), 1)
                self.assertEqual(client.get("/api/schedule").json()["revision"], before["revision"])
                self.assertEqual(client.put("/api/blocks/B1", json={"expected_revision": before["revision"],
                                                                     "expected_auto_revision": auto_before["revision"] - 1,
                                                                     "traversal_seconds": 90}).status_code, 409)
                self.assertEqual(client.get("/api/blocks").json()["B1"], 60)
                changed = client.put("/api/blocks/B1", json={"expected_revision": before["revision"],
                                                              "expected_auto_revision": auto_before["revision"],
                                                              "traversal_seconds": 90})
                self.assertEqual(changed.status_code, 200, changed.text)
                self.assertEqual(changed.json()["revision"], before["revision"] + 1)
                self.assertEqual(changed.json()["auto_candidate"]["revision"], auto_before["revision"] + 1)
            finally:
                client.delete(f"/api/services/{sid}")
                current = client.get("/api/schedule").json()["revision"]
                auto_current = client.get("/api/schedule?scenario=auto").json()["revision"]
                client.put("/api/blocks/B1", json={"expected_revision": current,
                                                   "expected_auto_revision": auto_current, "traversal_seconds": 60})
                client.delete(f"/api/vehicles/{vehicle}")

    def test_concurrent_block_commits_one_stale(self):
        with TestClient(app) as client:
            initial = client.get("/api/schedule").json()["revision"]
            auto_initial = client.get("/api/schedule?scenario=auto").json()["revision"]
            original = client.get("/api/blocks").json()["B14"]
            barrier = Barrier(2)
            def attempt(seconds):
                barrier.wait()
                return client.put("/api/blocks/B14", json={
                    "expected_revision": initial, "expected_auto_revision": auto_initial,
                    "traversal_seconds": seconds}).status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(attempt, (original + 1, original + 2)))
            self.assertEqual(sorted(results), [200, 409])
            current = client.get("/api/schedule").json()["revision"]
            self.assertEqual(current, initial + 1)
            self.assertEqual(client.get("/api/schedule?scenario=auto").json()["revision"], auto_initial + 1)
            self.assertEqual(client.put("/api/blocks/B14", json={
                "expected_revision": current, "expected_auto_revision": auto_initial + 1,
                "traversal_seconds": original}).status_code, 200)

    def test_transaction_rollback_on_structural_failure(self):
        with TestClient(app) as client:
            vehicle = "T" + uuid.uuid4().hex[:12]
            client.post("/api/vehicles", json={"id": vehicle, "name": "Test"})
            before = client.get("/api/schedule").json()
            bad = client.post("/api/services", json={
                "vehicle_id": vehicle, "start_at": before["scenario_start_at"],
                "steps": [{"node": "Y"}, {"node": "NO_SUCH_NODE"}]})
            self.assertEqual(bad.status_code, 422)
            after = client.get("/api/schedule").json()
            self.assertEqual(after["revision"], before["revision"])
            self.assertEqual(len(after["timelines"]), len(before["timelines"]))
            client.delete(f"/api/vehicles/{vehicle}")


if __name__ == "__main__":
    unittest.main()
