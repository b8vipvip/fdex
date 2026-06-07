from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.database import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    professional_level: Mapped[str] = mapped_column(String(30), default="business")
    avatar: Mapped[str] = mapped_column(String(500), default="")
    company_name: Mapped[str] = mapped_column(String(200), default="我的 AI 公司")
    is_verified_company: Mapped[bool] = mapped_column(Boolean, default=False)
    realname_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_retention_days: Mapped[int] = mapped_column(Integer, default=7)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    projects: Mapped[list["Project"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    project_type: Mapped[str] = mapped_column(String(50), default="unknown")
    status: Mapped[str] = mapped_column(String(50), default="draft")
    requirement_score: Mapped[float] = mapped_column(Float, default=0)
    storage_mode: Mapped[str] = mapped_column(String(30), default="hybrid")
    data_retention_policy: Mapped[str] = mapped_column(String(40), default="keep_forever")
    allow_third_party_ai: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_desensitize: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    user: Mapped[User] = relationship(back_populates="projects")
    messages: Mapped[list["ProjectMessage"]] = relationship(cascade="all, delete-orphan")
    assets: Mapped[list["ProjectAsset"]] = relationship(cascade="all, delete-orphan")
    reports: Mapped[list["ProjectReport"]] = relationship(cascade="all, delete-orphan")


class ProjectMessage(Base):
    __tablename__ = "project_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="user")
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ProjectAsset(Base):
    __tablename__ = "project_assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500))
    file_type: Mapped[str] = mapped_column(String(50), default="unknown")
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(50), default="uploaded")
    privacy_level: Mapped[str] = mapped_column(String(30), default="normal")
    is_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    desensitized_path: Mapped[str] = mapped_column(String(500), default="")
    original_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    results: Mapped[list["AssetAnalysisResult"]] = relationship(cascade="all, delete-orphan")


class AssetAnalysisResult(Base):
    __tablename__ = "asset_analysis_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("project_assets.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    analyzer_type: Mapped[str] = mapped_column(String(50))
    summary: Mapped[str] = mapped_column(Text)
    structured_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ProjectReport(Base):
    __tablename__ = "project_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    report_type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200))
    content_markdown: Mapped[str] = mapped_column(Text)
    structured_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class AITaskLog(Base):
    __tablename__ = "ai_task_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("project_assets.id"), nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    input_summary: Mapped[str] = mapped_column(Text, default="")
    output_summary: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIEmployee(Base):
    __tablename__ = "ai_employees"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    avatar: Mapped[str] = mapped_column(String(20), default="🤖")
    department: Mapped[str] = mapped_column(String(100), default="综合管理部")
    position: Mapped[str] = mapped_column(String(100))
    role_prompt: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str] = mapped_column(String(500), default="")
    job_role_id: Mapped[int | None] = mapped_column(ForeignKey("job_roles.id"), nullable=True, index=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    is_material_manager: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_upload_assets: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_receive_project_context: Mapped[bool] = mapped_column(Boolean, default=True)
    industry: Mapped[str] = mapped_column(String(100), default="通用")
    reply_mode: Mapped[str] = mapped_column(String(20), default="text")
    can_create_project: Mapped[bool] = mapped_column(Boolean, default=False)
    can_delete_project: Mapped[bool] = mapped_column(Boolean, default=False)
    can_view_project_data: Mapped[bool] = mapped_column(Boolean, default=True)
    can_view_project_reports: Mapped[bool] = mapped_column(Boolean, default=True)
    can_view_other_employee_messages: Mapped[bool] = mapped_column(Boolean, default=False)
    can_view_project_progress: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    resigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class EmployeeMessage(Base):
    __tablename__ = "employee_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("ai_employees.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(30), default="user")
    content: Mapped[str] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column(String(30), default="text")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    deleted_batch_id: Mapped[int | None] = mapped_column(ForeignKey("deleted_message_batches.id"), nullable=True, index=True)


class JobRole(Base):
    __tablename__ = "job_roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    industry: Mapped[str] = mapped_column(String(100), index=True)
    category: Mapped[str] = mapped_column(String(100), default="")
    title: Mapped[str] = mapped_column(String(100), index=True)
    aliases: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    role_prompt_template: Mapped[str] = mapped_column(Text, default="")
    is_common: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class DeletedMessageBatch(Base):
    __tablename__ = "deleted_message_batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("ai_employees.id"), index=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    retention_days: Mapped[int] = mapped_column(Integer, default=7)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmployeeMessageAttachment(Base):
    __tablename__ = "employee_message_attachments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("employee_messages.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("ai_employees.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(500))
    file_type: Mapped[str] = mapped_column(String(50), default="file")
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
