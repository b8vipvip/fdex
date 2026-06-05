from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def _add_column(engine: Engine, table: str, column: str, ddl: str) -> None:
    inspector = inspect(engine)
    existing = {col["name"] for col in inspector.get_columns(table)}
    if column not in existing:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def run_lightweight_migrations(engine: Engine) -> None:
    """Small SQLite-friendly migrations for the MVP without Alembic."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "projects" in tables:
        _add_column(engine, "projects", "storage_mode", "storage_mode VARCHAR(30) DEFAULT 'hybrid' NOT NULL")
        _add_column(engine, "projects", "data_retention_policy", "data_retention_policy VARCHAR(40) DEFAULT 'keep_forever' NOT NULL")
        _add_column(engine, "projects", "allow_third_party_ai", "allow_third_party_ai BOOLEAN DEFAULT 1 NOT NULL")
        _add_column(engine, "projects", "auto_desensitize", "auto_desensitize BOOLEAN DEFAULT 1 NOT NULL")
    if "project_assets" in tables:
        _add_column(engine, "project_assets", "privacy_level", "privacy_level VARCHAR(30) DEFAULT 'normal' NOT NULL")
        _add_column(engine, "project_assets", "is_sensitive", "is_sensitive BOOLEAN DEFAULT 0 NOT NULL")
        _add_column(engine, "project_assets", "desensitized_path", "desensitized_path VARCHAR(500) DEFAULT '' NOT NULL")
        _add_column(engine, "project_assets", "original_deleted_at", "original_deleted_at DATETIME")
        _add_column(engine, "project_assets", "retention_deadline", "retention_deadline DATETIME")
