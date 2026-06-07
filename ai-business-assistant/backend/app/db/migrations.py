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
    if "users" in tables:
        _add_column(engine, "users", "avatar", "avatar VARCHAR(500) DEFAULT '' NOT NULL")
        _add_column(engine, "users", "company_name", "company_name VARCHAR(200) DEFAULT '我的 AI 公司' NOT NULL")
        _add_column(engine, "users", "is_verified_company", "is_verified_company BOOLEAN DEFAULT 0 NOT NULL")
        _add_column(engine, "users", "realname_verified", "realname_verified BOOLEAN DEFAULT 0 NOT NULL")
        _add_column(engine, "users", "deleted_retention_days", "deleted_retention_days INTEGER DEFAULT 7 NOT NULL")
        _add_column(engine, "users", "company_industry", "company_industry VARCHAR(100) DEFAULT '' NOT NULL")
        _add_column(engine, "users", "auto_company_mode_enabled", "auto_company_mode_enabled BOOLEAN DEFAULT 0 NOT NULL")
        _add_column(engine, "users", "auto_company_mode_requires_confirm", "auto_company_mode_requires_confirm BOOLEAN DEFAULT 1 NOT NULL")
        _add_column(engine, "users", "default_auto_group_all_employees", "default_auto_group_all_employees BOOLEAN DEFAULT 1 NOT NULL")
        _add_column(engine, "users", "default_industry_required", "default_industry_required BOOLEAN DEFAULT 1 NOT NULL")
    if "ai_employees" in tables:
        _add_column(engine, "ai_employees", "avatar_url", "avatar_url VARCHAR(500) DEFAULT '' NOT NULL")
        _add_column(engine, "ai_employees", "job_role_id", "job_role_id INTEGER")
        _add_column(engine, "ai_employees", "is_system", "is_system BOOLEAN DEFAULT 0 NOT NULL")
        _add_column(engine, "ai_employees", "is_material_manager", "is_material_manager BOOLEAN DEFAULT 0 NOT NULL")
        _add_column(engine, "ai_employees", "allow_upload_assets", "allow_upload_assets BOOLEAN DEFAULT 1 NOT NULL")
        _add_column(engine, "ai_employees", "allow_receive_project_context", "allow_receive_project_context BOOLEAN DEFAULT 1 NOT NULL")
        _add_column(engine, "ai_employees", "is_active", "is_active BOOLEAN DEFAULT 1 NOT NULL")
        _add_column(engine, "ai_employees", "resigned_at", "resigned_at DATETIME")
    if "employee_messages" in tables:
        _add_column(engine, "employee_messages", "deleted_at", "deleted_at DATETIME")
        _add_column(engine, "employee_messages", "deleted_batch_id", "deleted_batch_id INTEGER")
    if "projects" in tables:
        _add_column(engine, "projects", "storage_mode", "storage_mode VARCHAR(30) DEFAULT 'hybrid' NOT NULL")
        _add_column(engine, "projects", "data_retention_policy", "data_retention_policy VARCHAR(40) DEFAULT 'keep_forever' NOT NULL")
        _add_column(engine, "projects", "allow_third_party_ai", "allow_third_party_ai BOOLEAN DEFAULT 1 NOT NULL")
        _add_column(engine, "projects", "auto_desensitize", "auto_desensitize BOOLEAN DEFAULT 1 NOT NULL")
        _add_column(engine, "projects", "industry", "industry VARCHAR(100) DEFAULT '' NOT NULL")
        _add_column(engine, "projects", "auto_operation_status", "auto_operation_status VARCHAR(50) DEFAULT 'not_started' NOT NULL")
        _add_column(engine, "projects", "auto_operation_group_id", "auto_operation_group_id INTEGER")
        _add_column(engine, "projects", "stage", "stage VARCHAR(100) DEFAULT '' NOT NULL")
        _add_column(engine, "projects", "stage_summary", "stage_summary TEXT DEFAULT '' NOT NULL")
    if "project_assets" in tables:
        _add_column(engine, "project_assets", "privacy_level", "privacy_level VARCHAR(30) DEFAULT 'normal' NOT NULL")
        _add_column(engine, "project_assets", "is_sensitive", "is_sensitive BOOLEAN DEFAULT 0 NOT NULL")
        _add_column(engine, "project_assets", "desensitized_path", "desensitized_path VARCHAR(500) DEFAULT '' NOT NULL")
        _add_column(engine, "project_assets", "original_deleted_at", "original_deleted_at DATETIME")
        _add_column(engine, "project_assets", "retention_deadline", "retention_deadline DATETIME")
