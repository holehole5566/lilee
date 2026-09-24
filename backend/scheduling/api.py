"""HTTP/application boundary: transactions, revision checks and storage policy."""
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import select
from .db import (BlockRow, ServiceRow, StepRow, VehicleRow, ScheduleRow,
                 Session)
from .domain import Service, Step, StructuralError, evaluate
from .generator import generate
from .coverage import uncovered
from .repository import load, lock_schedule, lock_schedules


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema and fixed seed are installed by the one-shot migration job.
    yield


app = FastAPI(title="Vehicle Scheduling System", lifespan=lifespan)


class VehicleInput(BaseModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)


class VehicleName(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class StepInput(BaseModel):
    node: str
    dwell_seconds: int = 0


class ServiceInput(BaseModel):
    vehicle_id: str
    start_at: datetime
    steps: list[StepInput]
    # Legacy DB column retained for compatibility; new services are all boardable at platforms.
    is_passenger_service: Literal[True] = True


class CandidateInput(BaseModel):
    operation: Literal["create", "update", "delete"]
    service_id: int | None = None
    service: ServiceInput | None = None


class DurationInput(BaseModel):
    traversal_seconds: int = Field(gt=0)


class DurationCommit(DurationInput):
    expected_revision: int = Field(ge=0)
    expected_auto_revision: int = Field(ge=0)


class GenerateInput(BaseModel):
    vehicle_ids: list[str] = Field(min_length=1)
    start: datetime
    end: datetime
    interval_seconds: int = Field(gt=0)


class GenerateCommit(GenerateInput):
    expected_revision: int = Field(ge=0)


def fail(code: str, message: str, status: int, **extra):
    raise HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def validate(graph, durations, vehicles, services, start):
    try:
        return evaluate(graph, durations, vehicles, services, start)
    except StructuralError as error:
        fail(error.code, str(error), 422, service_id=error.service_id, step_index=error.step_index)


def serialize(result, row):
    return jsonable_encoder({"scenario": "manual" if row.id == 1 else "auto",
                             "revision": row.revision, "scenario_start_at": row.scenario_start_at,
                             "status": result.status,
                             "timelines": [{**jsonable_encoder(t), "end_at": t.end_at} for t in result.timelines],
                             "conflicts": result.conflicts, "unknown_from": result.unknown_from})


def domain_input(id: int, data: ServiceInput) -> Service:
    return Service(id, data.vehicle_id, data.start_at,
                   tuple(Step(s.node, s.dwell_seconds) for s in data.steps), data.is_passenger_service)


def persist_steps(db, service: Service):
    db.add_all(StepRow(service_id=service.id, sequence_index=index, node_id=step.node,
                       dwell_seconds=step.dwell_seconds)
               for index, step in enumerate(service.steps))


@app.get("/api/health")
def health():
    with Session() as db:
        db.execute(select(ScheduleRow.id).where(ScheduleRow.id == 1)).scalar_one()
    return {"status": "ok"}


@app.get("/api/track")
def track():
    with Session.begin() as db:
        lock_schedule(db)
        graph, _, _, _ = load(db)
        return jsonable_encoder(graph)


@app.get("/api/blocks")
def blocks():
    with Session.begin() as db:
        lock_schedule(db)
        _, durations, _, _ = load(db)
        return durations


@app.get("/api/vehicles")
def list_vehicles():
    with Session() as db:
        return [{"id": r.id, "name": r.name} for r in db.scalars(select(VehicleRow).order_by(VehicleRow.id))]


@app.post("/api/vehicles", status_code=201)
def create_vehicle(data: VehicleInput):
    with Session.begin() as db:
        rows = lock_schedules(db)
        if db.get(VehicleRow, data.id):
            fail("DUPLICATE_VEHICLE", "vehicle ID already exists", 409)
        db.add(VehicleRow(id=data.id, name=data.name))
        for row in rows:
            row.revision += 1  # Shared fleet change invalidates both previews.
        return data.model_dump()


@app.get("/api/vehicles/{vehicle_id}")
def get_vehicle(vehicle_id: str):
    with Session() as db:
        row = db.get(VehicleRow, vehicle_id)
        if not row:
            fail("NOT_FOUND", "vehicle not found", 404)
        return {"id": row.id, "name": row.name}


@app.put("/api/vehicles/{vehicle_id}")
def rename_vehicle(vehicle_id: str, data: VehicleName):
    with Session.begin() as db:
        lock_schedules(db)
        row = db.get(VehicleRow, vehicle_id)
        if not row:
            fail("NOT_FOUND", "vehicle not found", 404)
        row.name = data.name
        return {"id": row.id, "name": row.name}


@app.delete("/api/vehicles/{vehicle_id}", status_code=204)
def delete_vehicle(vehicle_id: str):
    with Session.begin() as db:
        rows = lock_schedules(db)
        row = db.get(VehicleRow, vehicle_id)
        if not row:
            fail("NOT_FOUND", "vehicle not found", 404)
        if db.scalars(select(ServiceRow.id).where(ServiceRow.vehicle_id == vehicle_id).limit(1)).first():
            fail("VEHICLE_IN_USE", "vehicle has assigned services", 409)
        db.delete(row)
        for schedule_row in rows:
            schedule_row.revision += 1


@app.get("/api/schedule")
def schedule(scenario: Literal["manual", "auto"] = "manual"):
    with Session.begin() as db:
        row = lock_schedule(db, 1 if scenario == "manual" else 2)
        graph, durations, vehicles, services = load(db, row.id)
        return serialize(validate(graph, durations, vehicles, services, row.scenario_start_at), row)


def generated_candidate(graph, durations, vehicles, row, data: GenerateInput):
    selected = set(data.vehicle_ids)
    if len(selected) != len(data.vehicle_ids) or not selected <= vehicles:
        fail("INVALID_FLEET", "select distinct existing vehicle IDs", 422)
    try:
        return generate(graph, durations, selected, row.scenario_start_at,
                        data.start, data.end, data.interval_seconds)
    except ValueError as error:
        fail("INVALID_GENERATION", str(error), 422)
    except StructuralError as error:
        fail(error.code, str(error), 422)


def generation_payload(candidate, row):
    return {"base_revision": row.revision, "complete": candidate.complete,
            "attempts": candidate.attempts, "exhausted": candidate.exhausted,
            "gaps": jsonable_encoder(candidate.gaps),
            "candidate": serialize(candidate.evaluation, row)}


@app.post("/api/schedule/generate/preview")
def preview_generation(data: GenerateInput):
    with Session.begin() as db:
        row = lock_schedule(db, 2)
        graph, durations, vehicles, _ = load(db, 2)
        return generation_payload(generated_candidate(graph, durations, vehicles, row, data), row)


@app.post("/api/schedule/generate/commit")
def commit_generation(data: GenerateCommit):
    with Session.begin() as db:
        row = lock_schedule(db, 2)
        if data.expected_revision != row.revision:
            fail("STALE_REVISION", "preview is out of date", 409, current_revision=row.revision)
        graph, durations, vehicles, _ = load(db, 2)
        candidate = generated_candidate(graph, durations, vehicles, row, data)
        if not candidate.complete:
            fail("GENERATION_INCOMPLETE", "bounded search did not cover all stations without conflicts",
                 409, attempts=candidate.attempts, gaps=jsonable_encoder(candidate.gaps))
        # Replace the auto alternative only. Generated IDs from the search are
        # provisional; the DB assigns real IDs and we re-evaluate that snapshot.
        old_ids = list(db.scalars(select(ServiceRow.id).where(ServiceRow.schedule_id == 2)))
        if old_ids:
            db.query(StepRow).filter(StepRow.service_id.in_(old_ids)).delete(synchronize_session=False)
            db.query(ServiceRow).filter(ServiceRow.id.in_(old_ids)).delete(synchronize_session=False)
        saved = []
        for service in candidate.services:
            record = ServiceRow(schedule_id=2, vehicle_id=service.vehicle_id,
                                start_at=service.start_at, is_passenger_service=True)
            db.add(record)
            db.flush()
            actual = Service(record.id, service.vehicle_id, service.start_at, service.steps, True)
            persist_steps(db, actual)
            saved.append(actual)
        result = validate(graph, durations, vehicles, tuple(saved), row.scenario_start_at)
        if result.status != "validated" or uncovered(graph, result, data.start, data.end, data.interval_seconds):
            fail("GENERATION_INCOMPLETE", "generated schedule failed final validation", 409)
        row.revision += 1
        row.validation_status = result.status
        return serialize(result, row)


@app.post("/api/schedule/validate")
def preview_service(data: CandidateInput):
    """Read-only candidate evaluation. A preview ID is not a reserved DB ID."""
    with Session.begin() as db:
        row = lock_schedule(db)
        graph, durations, vehicles, services = load(db)
        ids = {s.id for s in services}
        if data.operation == "create":
            if data.service is None or data.service_id is not None:
                fail("INVALID_OPERATION", "create requires service and no service_id", 422)
            preview_id = max(ids, default=0) + 1
            candidates = services + (domain_input(preview_id, data.service),)
        elif data.operation == "update":
            if data.service is None or data.service_id is None:
                fail("INVALID_OPERATION", "update requires service and service_id", 422)
            if data.service_id not in ids:
                fail("NOT_FOUND", "service not found", 404)
            preview_id = None
            candidates = tuple(s for s in services if s.id != data.service_id) + (
                domain_input(data.service_id, data.service),)
        else:
            if data.service is not None or data.service_id is None:
                fail("INVALID_OPERATION", "delete requires service_id and no service", 422)
            if data.service_id not in ids:
                fail("NOT_FOUND", "service not found", 404)
            preview_id = None
            candidates = tuple(s for s in services if s.id != data.service_id)
        result = validate(graph, durations, vehicles, candidates, row.scenario_start_at)
        return {"base_revision": row.revision, "provisional_service_id": preview_id,
                "candidate": serialize(result, row)}


@app.get("/api/services")
def list_services():
    return schedule()


@app.get("/api/services/{service_id}")
def get_service(service_id: int):
    payload = schedule()
    for timeline in payload["timelines"]:
        if timeline["service"]["id"] == service_id:
            return timeline
    fail("NOT_FOUND", "service not found", 404)


@app.post("/api/services", status_code=201)
def create_service(data: ServiceInput):
    with Session.begin() as db:
        row = lock_schedule(db)
        graph, durations, vehicles, services = load(db)
        record = ServiceRow(schedule_id=1, vehicle_id=data.vehicle_id, start_at=data.start_at,
                            is_passenger_service=data.is_passenger_service)
        # Validate references before flush to return consistent 422, not an FK error.
        if data.vehicle_id not in vehicles:
            fail("UNKNOWN_VEHICLE", "vehicle does not exist", 422)
        db.add(record)
        db.flush()
        candidate = domain_input(record.id, data)
        result = validate(graph, durations, vehicles, services + (candidate,), row.scenario_start_at)
        persist_steps(db, candidate)
        row.revision += 1
        row.validation_status = result.status
        return {"id": record.id, **serialize(result, row)}


@app.put("/api/services/{service_id}")
def update_service(service_id: int, data: ServiceInput):
    with Session.begin() as db:
        row = lock_schedule(db)
        graph, durations, vehicles, services = load(db)
        if not any(s.id == service_id for s in services):
            fail("NOT_FOUND", "service not found", 404)
        candidate = domain_input(service_id, data)
        result = validate(graph, durations, vehicles,
                          tuple(s for s in services if s.id != service_id) + (candidate,), row.scenario_start_at)
        record = db.get(ServiceRow, service_id)
        record.vehicle_id, record.start_at = candidate.vehicle_id, candidate.start_at
        record.is_passenger_service = candidate.is_passenger_service
        db.query(StepRow).filter(StepRow.service_id == service_id).delete(synchronize_session=False)
        persist_steps(db, candidate)
        row.revision += 1
        row.validation_status = result.status
        return serialize(result, row)


@app.delete("/api/services/{service_id}")
def delete_service(service_id: int):
    with Session.begin() as db:
        row = lock_schedule(db)
        graph, durations, vehicles, services = load(db)
        if not any(s.id == service_id for s in services):
            fail("NOT_FOUND", "service not found", 404)
        result = validate(graph, durations, vehicles,
                          tuple(s for s in services if s.id != service_id), row.scenario_start_at)
        db.query(StepRow).filter(StepRow.service_id == service_id).delete(synchronize_session=False)
        db.delete(db.get(ServiceRow, service_id))
        row.revision += 1
        row.validation_status = result.status
        return serialize(result, row)


def block_candidate(db, rows, block_id: str, seconds: int):
    graph, durations, vehicles, _ = load(db, 1)
    if block_id not in durations:
        fail("NOT_FOUND", "block not found", 404)
    proposed = {**durations, block_id: seconds}
    candidates = []
    for row in rows:
        _, _, _, services = load(db, row.id)
        before = validate(graph, durations, vehicles, services, row.scenario_start_at)
        after = validate(graph, proposed, vehicles, services, row.scenario_start_at)
        previous = {t.service.id: t for t in before.timelines}
        changes = [{"service_id": t.service.id, "old_end_at": previous[t.service.id].end_at,
                    "new_end_at": t.end_at}
                   for t in after.timelines if t.end_at != previous[t.service.id].end_at]
        candidates.append((after, jsonable_encoder(changes)))
    return candidates


@app.post("/api/blocks/{block_id}/preview")
def preview_block(block_id: str, data: DurationInput):
    with Session.begin() as db:
        rows = lock_schedules(db)
        (manual, manual_changes), (auto, auto_changes) = block_candidate(
            db, rows, block_id, data.traversal_seconds)
        return {"base_revision": rows[0].revision, "base_auto_revision": rows[1].revision,
                "affected_services": manual_changes, "auto_affected_services": auto_changes,
                "candidate": serialize(manual, rows[0]), "auto_candidate": serialize(auto, rows[1])}


@app.put("/api/blocks/{block_id}")
def update_block(block_id: str, data: DurationCommit):
    with Session.begin() as db:
        rows = lock_schedules(db)
        if data.expected_revision != rows[0].revision or data.expected_auto_revision != rows[1].revision:
            fail("STALE_REVISION", "preview is out of date", 409,
                 current_revision=rows[0].revision, current_auto_revision=rows[1].revision)
        (manual, _), (auto, _) = block_candidate(db, rows, block_id, data.traversal_seconds)
        db.get(BlockRow, block_id).traversal_seconds = data.traversal_seconds
        for row, result in zip(rows, (manual, auto)):
            row.revision += 1
            row.validation_status = result.status
        return {**serialize(manual, rows[0]), "auto_candidate": serialize(auto, rows[1])}
