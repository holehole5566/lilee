# Vehicle Scheduling System

Angular + FastAPI + PostgreSQL scheduling take-home. Run the complete system with Docker Compose. This is a **planning simulation**, not a railway safety system.

## Run

Requires Docker and Docker Compose. From this directory:

```sh
docker compose up --build
```

- UI: <http://localhost:8080>
- In-app Manual conflict walkthrough (繁中): <http://localhost:8080/guide>
- API docs: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/api/health>

The one-shot `migrate` service applies Alembic revisions `001` and `002` and seeds the fixed track before the API starts. PostgreSQL data persists in the Compose `pgdata` volume. **Do not run `docker compose down -v` if you want to keep that data.** Back up non-disposable databases before upgrading. Revision `002` preserves existing manual services, revision and scenario start while adding the separate auto scenario. An earlier prototype database can be adopted only when its schema matches the expected structure; an unknown or partial schema fails closed. Destructive downgrade is not supported.

`SCENARIO_START_AT` in `docker-compose.yml` is a **demo** UTC instant (`2026-01-01T00:00:00Z`), not an assignment requirement. Set it before initializing a new database; changing the environment variable later does not reset the saved scenario. There is no scenario-reset API.

## Features and current scope

- **Schedule Editor:** Manual service CRUD, Vehicle CRUD, directed path selection via SVG track map or dropdown, platform/Yard dwell editing, and read-only candidate preview. Every move belongs to a service path; there is no separate repositioning mode. Structural errors are rejected; operational conflicts may be saved as a clearly marked `draft`.
- **Schedule Viewer:** Read-only manual/auto alternative scenarios, derived times and battery, conflict list, and SVG seek/play/pause with accelerated playback. A broken vehicle trajectory displays unknown position/battery rather than inventing a path.
- **Block Configuration:** Change each block's positive traversal duration. Preview the effects on both scenarios before confirming with both expected revisions.
- **Conflict rules (Bonus 1):** Detect same-vehicle overlap/location discontinuity, shared block occupancy, interlocking-group occupancy, battery below 30 in a block, and departure from Yard below 80. Domain unit tests cover battery rules; battery scenarios have not yet been recorded as manual UI E2E acceptance tests.
- **Interactive map (Bonus 2):** Directed path clicking/highlighting in Editor and backend-timeline-based playback in Viewer. The visual layout still needs a final manual comparison to `track_map.png`; unsaved service candidate previews are not playable yet.
- **Auto generator (Bonus 3):** Preview a passenger schedule for a selected existing fleet, passenger range and maximum station wait. An atomic commit replaces **only** the auto alternative after rechecking revision, conflicts and coverage. Search is capped at 128 generated services and 2,000 evaluated candidates; it may miss a feasible schedule, and incomplete previews cannot be committed. Larger fleets and longer ranges need broader acceptance checks.
- **操作手冊:** `/guide` is a read-only Traditional Chinese walkthrough with Manual conflict demos. Use an empty disposable Manual schedule for reproducible results; the guide never creates or deletes services.

Manual and auto are **alternative schedules**, not vehicles operating together. Manual CRUD never edits auto services; the auto generator never merges with or overwrites manual services. Topology, vehicle definitions and block durations are shared.

## Architecture and data model

A single FastAPI application uses SQLAlchemy/PostgreSQL for persistence and a pure-Python domain engine for path, timeline, conflict and battery rules. The application layer controls transactions, locks and revision checks. PostgreSQL schema (Alembic `001` + `002`; **PK** = primary key, **FK** = foreign key):

| Table | Stored columns and DB constraints | Purpose |
| --- | --- | --- |
| `schedule` | `id` integer **PK** (`1` manual / `2` auto); `scenario_start_at` timestamptz; `revision` integer ≥ 0; `validation_status` `draft` or `validated`. | Separate alternative schedules, each with a fixed start and revision. |
| `vehicles` | `id` varchar(40) **PK**; `name` varchar(120). | Shared fleet; ID stays stable while the name can change. |
| `track_nodes` | `id` varchar(10) **PK**; `kind` `YARD` / `PLATFORM` / `BLOCK`; nullable `station_id` varchar(10). | Fixed topology nodes; `station_id` is a label, **not** a FK to a station table. |
| `track_edges` | `from_node_id`, `to_node_id`: composite **PK**, each **FK** → `track_nodes.id`. | Directed adjacency; a bidirectional connection has two rows. |
| `interlocking_groups` | `id` varchar(10) **PK**. | Names of exclusive-use groups. |
| `interlocking_members` | `group_id` **FK** → `interlocking_groups.id`, `block_id` **FK** → `track_nodes.id`: composite **PK**. | Blocks assigned to each group. |
| `block_configs` | `block_id` **PK**, **FK** → `track_nodes.id`; `traversal_seconds` integer > 0. | One shared duration per block (seeded at 60 seconds). |
| `services` | `id` generated integer **PK**; `schedule_id` **FK** → `schedule.id` (indexed, delete restricted); `vehicle_id` **FK** → `vehicles.id` (delete restricted); `start_at` timestamptz; `is_passenger_service` boolean. | A vehicle run in one scenario. The boolean is a legacy compatibility column; new services always allow boarding at visited platforms. |
| `service_steps` | `service_id` **FK** → `services.id` (cascade delete), `sequence_index` integer ≥ 0: composite **PK**; `node_id` **FK** → `track_nodes.id`; `dwell_seconds` integer ≥ 0. | Ordered path, including repeated nodes and manual platform/Yard dwell. |

All columns above are `NOT NULL` except `track_nodes.station_id`. The migrations seed **21 nodes, 28 directed edges, three interlocking groups and 14 block configurations**. The SVG is presentation only; the DB is the topology source.

For a service, the stored scheduling inputs are start time, ordered path and dwell (alongside IDs and the legacy flag); there are no separate visit, battery, occupancy or conflict tables. The domain engine derives platform/Yard arrival/departure, block intervals, battery and conflicts. DB constraints cover keys and basic value ranges, but **not** directed path validity, allowed endpoint/dwell node types, resource overlap, vehicle continuity or battery: the same domain validator checks those for preview and mutations.

A small complete scenario is loaded and evaluated under its schedule-row lock. Shared fleet/configuration mutations lock manual then auto, revalidate both and advance both revisions. This keeps revision/status and data consistent in one transaction, but coarse-grained whole-scenario evaluation limits write throughput; no large-fleet performance guarantee is made.

### Business logic vs data access

- **Data access:** `db.py` defines ORM tables; `repository.py` loads the graph, configuration, vehicles and services into plain objects. Alembic migrations own schema changes.
- **Business rules:** `domain.py` validates paths and derives times, conflicts and battery; `coverage.py` checks station waiting limits; `generator.py` tries candidate schedules. These modules do not import FastAPI or SQLAlchemy and do not open a DB connection.
- **Application/API:** `api.py` opens transactions, locks schedule rows, loads data, evaluates a complete candidate and decides whether to save a validated schedule or a manual draft. This use-case orchestration is currently in `api.py`, **not** a separate application module.

Flow: **API → repository load → pure domain evaluation → save policy → repository/ORM write**. `test_domain.py`, `test_coverage.py` and `test_generator.py` construct in-memory inputs to test rules without PostgreSQL; `test_api_integration.py` separately verifies persistence, rollback and locking against a disposable PostgreSQL database. This keeps fast rule tests independent of the storage layer without pretending that unit tests replace DB integration tests.

### Trade-off: SQL-side rules vs application-side evaluation

We considered computing scheduling rules in SQL. PostgreSQL is well suited to joins, filtering and basic integrity constraints, and doing more work there could avoid loading a whole scenario into application memory. It is a viable alternative, not something SQL cannot express. But vehicle continuity, charging across services and repeated generator candidate evaluations are stateful rules we also need for manual previews and playback. Putting all of them in queries or stored procedures would tie those trial calculations and their tests to a live database.

Instead, the repository loads the selected **small, complete scenario** into plain Python objects; the pure domain functions derive the timeline and conflicts. The same evaluator serves manual mutations, previews and generator trials, and rule-level unit tests run without PostgreSQL. SQL still handles retrieval and PK/FK/CHECK constraints; integration tests verify the mapping, transactions and locking. **Cost:** more data transfer and application CPU, full recomputation on changes and coarse schedule-row locks. We have not benchmarked large schedules; SQL-side filtering or aggregation could be added if profiling identifies a bottleneck, without duplicating the scheduling rules.

### Data-model trade-off: ordered step rows vs JSONB

We chose one `service_steps` row per path visit, keyed by `(service_id, sequence_index)`, rather than storing the whole path as JSONB on `services`. Rows preserve order and repeated visits, and `node_id` has a normal FK: deleting or renaming a referenced node ID cannot silently leave a dangling step. This also makes individual steps queryable with SQL. The cost is more rows and a join to load a path; updating a service currently validates the proposed schedule, then deletes and reinserts that service's steps in one transaction instead of patching individual positions.

JSONB would make storing and replacing a whole path simpler, and PostgreSQL can query and index JSONB. But individual node IDs inside a JSONB array cannot use an ordinary column FK; they would need application validation or extra database logic. **A node FK alone does not prove the path is valid**: adjacency, endpoint, timing and conflict rules stay in the domain validator. For this small fixed graph, explicit node references and a clear visit order were worth the extra table.

## API overview

| Method/path | Behavior |
| --- | --- |
| `GET /api/track`, `GET /api/blocks`, `GET /api/health` | Read fixed track, durations and health. |
| `GET /api/schedule?scenario=manual\|auto` | Read derived schedule; omitted scenario defaults to manual. |
| `GET/POST /api/vehicles`, `GET/PUT/DELETE /api/vehicles/{id}` | Shared fleet; PUT renames, deletion of a referenced vehicle returns 409. |
| `GET/POST /api/services`, `GET/PUT/DELETE /api/services/{id}` | Manual services only. GET list returns a schedule envelope, not a bare array. |
| `POST /api/schedule/validate` | Read-only manual create/update/delete candidate preview with base revision. |
| `POST /api/blocks/{id}/preview`, `PUT /api/blocks/{id}` | Preview both alternatives; commit requires `expected_revision` and `expected_auto_revision`. |
| `POST /api/schedule/generate/preview`, `POST /api/schedule/generate/commit` | Read-only auto search; commit takes the same inputs plus auto `expected_revision`, re-searches under lock, validates and atomically replaces auto only. |

Example manual service input:

```json
{"vehicle_id":"V1","start_at":"2026-01-01T08:00:00Z","steps":[{"node":"Y","dwell_seconds":24},{"node":"B1"},{"node":"P1A","dwell_seconds":30}]}
```

Generator preview input:

```json
{"vehicle_ids":["V1","V2"],"start":"2026-01-01T08:00:00Z","end":"2026-01-01T09:00:00Z","interval_seconds":600}
```

Preview returns `base_revision`, `complete`, `attempts`, `gaps` and a candidate schedule with **provisional** service IDs. Commit must also include `expected_revision`; saved IDs may differ. A stale or incomplete commit returns 409 without replacing the previous auto schedule. Structural errors return 422; operational conflicts in manual CRUD are storable drafts. Responses include derived visit arrival/departure/battery, service `end_at`, intervals, conflicts, status and revision. Structured application errors use `detail.code`/`detail.message`; framework input-validation errors use FastAPI's native 422 format.

## Scheduling assumptions

These are simplified rules for this scheduling exercise, not real-world railway safety rules:

- Platforms and the Yard have no vehicle capacity limit. A train can stay at a platform as long as its schedule says; there is no maximum dwell time.
- Every vehicle starts at Yard `Y` with battery 80. It charges there, including during a manually scheduled Yard stop, at one unit per 12 seconds (up to 100).
- For a conflict-free schedule, a vehicle cannot teleport: its next service starts where the previous one ended, and the two services do not overlap.
- Auto-generated services stop for **60 seconds at each platform**; their Yard stops have no dwell. Manual services use the platform/Yard dwell times entered in the Editor.
- Any service that stops at a platform can board passengers during its dwell; travel without a platform visit does not create a boarding opportunity. During the requested time range, the wait at each station must stay within the chosen limit.

## Tests

```sh
# Pure-Python domain/coverage/generator tests (no PostgreSQL needed):
(cd backend && PYTHONPATH=. python3 -m unittest discover -s tests -v)

# With a disposable Compose database running:
docker compose run --rm -e RUN_DB_TESTS=1 api python -m unittest discover -s tests -v

(cd frontend && npm ci && npm run build)
# With Compose running and local Google Chrome, against disposable schedule data:
(cd frontend && npm run test:e2e)
# Destructively replaces auto; ONLY on an empty disposable auto scenario:
(cd frontend && npm run test:e2e:generator)
```

The browser E2E scripts mutate data. Never run them against a schedule you want to preserve. Use `docker compose up --build` to pick up code changes in existing API/Web containers; rebuilding does not reset the PostgreSQL volume.
