# Vehicle Scheduling System

Angular + FastAPI + PostgreSQL take-home. This is a scheduling simulation, **not** a railway safety system.

## Run

Requires Docker and Docker Compose. From this directory:

```sh
docker compose up --build
```

UI: <http://localhost:8080> · API docs: <http://localhost:8000/docs> · Health: <http://localhost:8000/api/health> · Manual guide: <http://localhost:8080/guide>

The `migrate` job applies Alembic `001`/`002` and seeds the track before the API starts. Data persists in the `pgdata` volume: **do not run `docker compose down -v` to keep it**. Back up important data before upgrades; `002` preserves the existing manual schedule, and destructive downgrade is unsupported. `SCENARIO_START_AT` in `docker-compose.yml` is a demo UTC start (`2026-01-01T00:00:00Z`); set it before first initialization, as changing it later does not reset saved schedules.

## Scope

- **Required:** Vehicle and manual Service CRUD; directed-path and same-vehicle checks; Schedule Editor, read-only Viewer and Block Configuration; backend unit tests. Structural errors return 422; operational conflicts can be saved as a visible `draft`.
- **Bonus 1:** Block/interlocking occupancy and battery conflicts (below 30 outside Yard; below 80 on departure). Battery rules have unit tests, not a manual low-battery UI E2E test.
- **Bonus 2:** Clickable SVG track map and Viewer play/pause/seek with vehicle, battery and conflict display. Final visual comparison with `track_map.png` and playback of *unsaved* previews remain open.
- **Bonus 3:** Preview/commit for a selected fleet and maximum station wait. Commit rechecks the candidate and atomically replaces **only** the auto schedule if coverage is complete and conflicts are zero. Search is bounded at 128 services / 2,000 candidate trials, so failure is not proof of infeasibility; longer ranges need broader testing.

Manual and auto are **alternative scenarios**, never simultaneous schedules. They share the track, vehicles and block durations, but have separate services, revisions and validation states.

## Architecture and data model

`api.py` handles HTTP, transactions, locks and save policy; `repository.py`/`db.py` load PostgreSQL rows into plain objects; `domain.py`, `coverage.py` and `generator.py` compute rules without FastAPI or a DB connection. Flow: **API → repository load → pure domain evaluation → save policy → DB write**. The application orchestration is currently in `api.py`, not a separate use-case module. Pure rule tests use in-memory inputs; integration tests exercise mapping, locks and rollback with PostgreSQL.

Current PostgreSQL schema (Alembic `001` + `002`; **PK** = primary key, **FK** = foreign key):

| Table | Stored columns and constraints |
| --- | --- |
| `schedule` | `id` integer **PK** (`1` manual, `2` auto); `scenario_start_at` timestamptz; `revision` integer ≥ 0; `validation_status` `draft`/`validated`. |
| `vehicles` | `id` varchar(40) **PK**; `name` varchar(120). |
| `track_nodes` | `id` varchar(10) **PK**; `kind` `YARD`/`PLATFORM`/`BLOCK`; nullable `station_id` varchar(10) (label, not FK). |
| `track_edges` | `(from_node_id, to_node_id)` composite **PK**; both **FK** → `track_nodes.id` (directed edges). |
| `interlocking_groups` | `id` varchar(10) **PK**. |
| `interlocking_members` | `(group_id, block_id)` composite **PK**; **FK** → `interlocking_groups.id`, `track_nodes.id`. |
| `block_configs` | `block_id` **PK**, **FK** → `track_nodes.id`; `traversal_seconds` integer > 0. |
| `services` | generated integer `id` **PK**; `schedule_id` **FK** → `schedule.id` (indexed, RESTRICT); `vehicle_id` **FK** → `vehicles.id` (RESTRICT); `start_at` timestamptz; legacy `is_passenger_service` boolean. |
| `service_steps` | `(service_id, sequence_index)` composite **PK**; `service_id` **FK** → `services.id` (CASCADE); `sequence_index` integer ≥ 0; `node_id` **FK** → `track_nodes.id`; `dwell_seconds` integer ≥ 0. |

All columns are `NOT NULL` except `track_nodes.station_id`. The seed contains 21 nodes, 28 directed edges, three interlocking groups and 14 block durations; the SVG is not the topology source. Services store start, ordered path and dwell, **not** derived arrivals, occupancy, battery or conflicts. DB constraints protect references/basic values; the domain validator checks directed adjacency, endpoints, continuity, occupancy and battery.

## Trade-offs

**Trade-off 1 — SQL rules vs application logic.** SQL could evaluate more of the schedule without loading it into memory. We instead load the small complete scenario and use one pure Python evaluator for manual edits, previews and generator trials, making rule unit tests independent of PostgreSQL. SQL still handles retrieval and PK/FK/CHECK constraints. The cost is full recomputation, application CPU/memory and coarse schedule-row locks; we have not benchmarked large schedules.

**Trade-off 2 — step rows vs JSONB path.** JSONB would make whole-path storage simpler. We use `(service_id, sequence_index)` rows so visits (including repeats) are ordered and every `node_id` has a normal FK. The cost is more rows/joins and replacing all steps on an update in one transaction. A node FK prevents dangling references, **not** invalid directed paths; those still require domain validation. JSONB is queryable/indexable, but individual IDs inside it cannot use an ordinary column FK.

## API overview

| Endpoint | Purpose |
| --- | --- |
| `GET /api/track`, `/api/blocks`, `/api/health` | Fixed track, shared durations and health. |
| `GET /api/schedule?scenario=manual\|auto` | Derived schedule, status, revision, visits and conflicts; default manual. |
| `/api/vehicles`, `/api/vehicles/{id}` | Shared Vehicle CRUD; referenced vehicles cannot be deleted (409). |
| `/api/services`, `/api/services/{id}` | Manual Service CRUD; list returns a schedule envelope. |
| `POST /api/schedule/validate` | Read-only create/update/delete candidate preview. |
| `POST /api/blocks/{id}/preview`, `PUT /api/blocks/{id}` | Preview both scenarios; commit requires both expected revisions. |
| `POST /api/schedule/generate/preview`, `POST /api/schedule/generate/commit` | Preview auto candidate; commit re-searches under lock and requires auto `expected_revision` (stale/incomplete → 409). |

Manual service input uses `vehicle_id`, UTC `start_at` and ordered `steps` such as `[{"node":"Y","dwell_seconds":24},{"node":"B1"},{"node":"P1A","dwell_seconds":30}]`. Responses derive arrival/departure; a preview does not write data.

## Scheduling assumptions

- Platforms and Yard have no vehicle capacity limit; platform dwell has no maximum.
- Vehicles start at `Y` with battery 80; they charge there at 1 unit per 12 seconds, up to 100, including during manual Yard dwell.
- In a conflict-free schedule, a vehicle's services must connect without overlap; it cannot teleport.
- Auto services stop 60 seconds at each platform and do not dwell at Yard. Manual platform/Yard dwell is user-entered.
- Passengers can board during platform stops; the requested maximum wait applies at each station.

## Tests

```sh
# Pure rule unit tests; no PostgreSQL required:
(cd backend && PYTHONPATH=. python3 -m unittest discover -s tests -v)
# Integration tests; use a disposable Compose database:
docker compose run --rm -e RUN_DB_TESTS=1 api python -m unittest discover -s tests -v
(cd frontend && npm ci && npm run build)
# With Compose running and disposable schedule data (requires local Chrome):
(cd frontend && npm run test:e2e && npm run test:e2e:generator)
```

Browser E2E mutates data and the generator test replaces auto: **do not run them against schedules you need to keep**. Rebuild existing containers with `docker compose up --build`; this does not reset the DB volume.
