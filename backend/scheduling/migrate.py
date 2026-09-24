"""One-shot upgrade; guarded adoption of the earlier create_all prototype schema.

Never drop or recreate an existing scheduling table merely to stamp a version.
"""
from pathlib import Path
import re
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from .db import Base, engine, bootstrap

LEGACY_CHECKS = {
    "schedule": {
        "schedule_id_check": "id = 1",
        "schedule_revision_check": "revision >= 0",
        "schedule_validation_status_check": "validation_status::text = ANY (ARRAY['draft'::character varying, 'validated'::character varying]::text[])",
    },
    "track_nodes": {
        "track_nodes_kind_check": "kind::text = ANY (ARRAY['YARD'::character varying, 'PLATFORM'::character varying, 'BLOCK'::character varying]::text[])",
    },
    "block_configs": {"block_configs_traversal_seconds_check": "traversal_seconds > 0"},
    "service_steps": {"service_steps_sequence_index_check": "sequence_index >= 0",
                      "service_steps_dwell_seconds_check": "dwell_seconds >= 0"},
}


def normalized(sql):
    return re.sub(r"\s+", "", sql.lower())


def verify_schema(inspector, *, legacy=False):
    """Refuse auto-stamp of unknown prototype or startup with a drifted current schema."""
    for table in Base.metadata.sorted_tables:
        name = table.name
        actual = {col["name"]: col for col in inspector.get_columns(name)}
        expected = {col.name: col for col in table.columns
                    if not (legacy and name == "services" and col.name == "schedule_id")}
        if actual.keys() != expected.keys():
            raise RuntimeError(f"Cannot adopt schema: {name} columns differ; back up and migrate manually")
        for key, column in expected.items():
            found = actual[key]
            if found["type"].compile(dialect=engine.dialect) != column.type.compile(dialect=engine.dialect):
                raise RuntimeError(f"Cannot adopt schema: {name}.{key} type differs")
            if found["nullable"] != column.nullable:
                raise RuntimeError(f"Cannot adopt schema: {name}.{key} nullability differs")
        if set(inspector.get_pk_constraint(name)["constrained_columns"]) != {c.name for c in table.primary_key}:
            raise RuntimeError(f"Cannot adopt schema: {name} primary key differs")
        expected_fk = {(tuple(fk.parent.name for fk in constraint.elements),
                        constraint.referred_table.name,
                        tuple(fk.column.name for fk in constraint.elements),
                        constraint.ondelete)
                       for constraint in table.foreign_key_constraints
                       if not (legacy and name == "services" and "schedule_id" in
                               {fk.parent.name for fk in constraint.elements})}
        actual_fk = {(tuple(fk["constrained_columns"]), fk["referred_table"],
                      tuple(fk["referred_columns"]), fk["options"].get("ondelete"))
                     for fk in inspector.get_foreign_keys(name)}
        if expected_fk != actual_fk:
            raise RuntimeError(f"Cannot adopt schema: {name} foreign keys differ")
        checks = {check["name"]: normalized(check["sqltext"])
                  for check in inspector.get_check_constraints(name)}
        expected_checks = {key: normalized(sql) for key, sql in LEGACY_CHECKS.get(name, {}).items()}
        if not legacy and name == "schedule":
            expected_checks["schedule_id_check"] = normalized("id = ANY (ARRAY[1, 2])")
        if checks != expected_checks:
            raise RuntimeError(f"Cannot adopt schema: {name} check constraints differ")
        if not legacy and name == "services":
            indexes = {index["name"]: tuple(index["column_names"])
                       for index in inspector.get_indexes(name)}
            if indexes != {"ix_services_schedule_id": ("schedule_id",)}:
                raise RuntimeError("services scenario index differs")


def run():
    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    expected = set(Base.metadata.tables)
    with engine.connect() as connection:
        inspector = inspect(connection)
        existing = set(inspector.get_table_names())
        if not {"alembic_version"}.issubset(existing) and (existing & expected):
            if not expected.issubset(existing):
                raise RuntimeError("Partial legacy schema detected; back up and migrate manually")
            verify_schema(inspector, legacy=True)
            # Existing prototype has identical initial tables. Stamp only; preserve all rows.
            command.stamp(cfg, "001")
    command.upgrade(cfg, "head")
    # Verify after migration (also catches a stamped DB with missing/broken schema).
    with engine.connect() as connection:
        verify_schema(inspect(connection))
    bootstrap()


if __name__ == "__main__":
    run()
