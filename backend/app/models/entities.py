from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import (
    GoalMetric,
    ProductionType,
    ProjectTeacherRole,
    ProjectType,
    Quartile,
    UserRole,
)


class Faculty(Base):
    __tablename__ = "faculties"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True)
    careers: Mapped[list["Career"]] = relationship(back_populates="faculty")


class Career(Base):
    __tablename__ = "careers"
    __table_args__ = (
        UniqueConstraint("id", "faculty_id", name="uq_careers_id_faculty_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculties.id"))
    name: Mapped[str] = mapped_column(String(180), unique=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)

    faculty: Mapped[Faculty] = relationship(back_populates="careers")
    teachers: Mapped[list["Teacher"]] = relationship(back_populates="career")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(180))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), index=True)
    career_id: Mapped[int | None] = mapped_column(ForeignKey("careers.id"), nullable=True)
    faculty_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "faculties.id",
            name="fk_users_faculty_id_faculties",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    career: Mapped[Career | None] = relationship()
    faculty: Mapped[Faculty | None] = relationship()


class Teacher(Base):
    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    career_id: Mapped[int] = mapped_column(ForeignKey("careers.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(180), index=True)
    institutional_email: Mapped[str] = mapped_column(String(180), unique=True)
    research_hours: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True, index=True)
    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id"), nullable=True, index=True)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_section: Mapped[str | None] = mapped_column(String(120), nullable=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    normalization_action: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    normalization_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_name: Mapped[str | None] = mapped_column(String(220), nullable=True, index=True)
    raw_faculty: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_faculty: Mapped[str | None] = mapped_column(String(220), nullable=True)
    raw_career: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_career: Mapped[str | None] = mapped_column(String(220), nullable=True)
    validation_status: Mapped[str] = mapped_column(String(40), default="validated", index=True)

    career: Mapped[Career] = relationship(back_populates="teachers")
    import_batch: Mapped["ImportBatch | None"] = relationship(foreign_keys=[import_batch_id])
    import_job: Mapped["ImportJob | None"] = relationship(foreign_keys=[import_job_id])
    productions: Mapped[list["ScientificProduction"]] = relationship(back_populates="teacher")
    projects: Mapped[list["ProjectTeacher"]] = relationship(back_populates="teacher")

    @property
    def career_name(self) -> str:
        return self.career.name

    @property
    def faculty_name(self) -> str:
        return self.career.faculty.name


class ExternalResearcher(Base):
    __tablename__ = "external_researchers"
    __table_args__ = (UniqueConstraint("period_id", "normalized_name", "normalized_institution", name="uq_external_researcher_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("academic_periods.id"), index=True)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True, index=True)
    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id"), nullable=True, index=True)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    full_name: Mapped[str] = mapped_column(String(180), index=True)
    normalized_name: Mapped[str] = mapped_column(String(220), index=True)
    institution: Mapped[str] = mapped_column(String(220), index=True)
    normalized_institution: Mapped[str] = mapped_column(String(260), index=True)
    source_section: Mapped[str] = mapped_column(String(80), default="integrantes_externos")
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    normalization_action: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    normalization_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_institution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)

    period: Mapped["AcademicPeriod"] = relationship()
    import_batch: Mapped["ImportBatch | None"] = relationship(foreign_keys=[import_batch_id])
    import_job: Mapped["ImportJob | None"] = relationship()

    @property
    def year_label(self) -> str:
        return self.period.year_label

    @property
    def cycle(self) -> int:
        return self.period.cycle


class ResearchEntity(Base):
    __tablename__ = "research_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int | None] = mapped_column(ForeignKey("academic_periods.id"), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(50), index=True)
    code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    normalized_code: Mapped[str | None] = mapped_column(String(140), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(350), nullable=True)
    normalized_name: Mapped[str | None] = mapped_column(String(380), nullable=True, index=True)
    director_name: Mapped[str | None] = mapped_column(String(220), nullable=True)
    normalized_director_name: Mapped[str | None] = mapped_column(String(260), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cycle: Mapped[int | None] = mapped_column(Integer, nullable=True)
    academic_unit: Mapped[str | None] = mapped_column(String(220), nullable=True)
    career_name: Mapped[str | None] = mapped_column(String(220), nullable=True)
    progress_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    validation_status: Mapped[str] = mapped_column(String(40), default="pending_review", index=True)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True, index=True)
    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id"), nullable=True, index=True)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_section: Mapped[str | None] = mapped_column(String(120), nullable=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)

    period: Mapped["AcademicPeriod | None"] = relationship()
    import_batch: Mapped["ImportBatch | None"] = relationship()
    import_job: Mapped["ImportJob | None"] = relationship()

    @property
    def year_label(self) -> str | None:
        return self.period.year_label if self.period else None


class PersonRole(Base):
    __tablename__ = "person_roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int | None] = mapped_column(ForeignKey("academic_periods.id"), nullable=True, index=True)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True, index=True)
    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id"), nullable=True, index=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True, index=True)
    external_researcher_id: Mapped[int | None] = mapped_column(ForeignKey("external_researchers.id"), nullable=True, index=True)
    scientific_production_id: Mapped[int | None] = mapped_column(ForeignKey("scientific_productions.id"), nullable=True, index=True)
    research_project_id: Mapped[int | None] = mapped_column(ForeignKey("research_projects.id"), nullable=True, index=True)
    research_entity_id: Mapped[int | None] = mapped_column(ForeignKey("research_entities.id"), nullable=True, index=True)
    role_type: Mapped[str] = mapped_column(String(50), index=True)
    person_type: Mapped[str] = mapped_column(String(50), index=True)
    person_key: Mapped[str | None] = mapped_column(String(260), nullable=True, index=True)
    canonical_identity_key: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    canonical_name: Mapped[str | None] = mapped_column(String(220), nullable=True)
    identity_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    identity_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    identity_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    identity_decided_by: Mapped[str | None] = mapped_column(String(180), nullable=True)
    identity_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_name: Mapped[str | None] = mapped_column(String(220), nullable=True, index=True)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_section: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(40), default="pending_review", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)

    period: Mapped["AcademicPeriod | None"] = relationship()
    import_batch: Mapped["ImportBatch | None"] = relationship()
    import_job: Mapped["ImportJob | None"] = relationship()
    teacher: Mapped["Teacher | None"] = relationship()
    external_researcher: Mapped["ExternalResearcher | None"] = relationship()
    scientific_production: Mapped["ScientificProduction | None"] = relationship()
    research_project: Mapped["ResearchProject | None"] = relationship()
    research_entity: Mapped["ResearchEntity | None"] = relationship()


class AcademicPeriod(Base):
    __tablename__ = "academic_periods"
    __table_args__ = (UniqueConstraint("year_label", "cycle", name="uq_period_year_cycle"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    year_label: Mapped[str] = mapped_column(String(20), index=True)
    cycle: Mapped[int] = mapped_column(Integer, index=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScientificProduction(Base):
    __tablename__ = "scientific_productions"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True, index=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("academic_periods.id"), index=True)
    research_entity_id: Mapped[int | None] = mapped_column(ForeignKey("research_entities.id"), nullable=True, index=True)
    production_type: Mapped[ProductionType] = mapped_column(Enum(ProductionType), index=True)
    title: Mapped[str] = mapped_column(String(250))
    journal: Mapped[str | None] = mapped_column(String(180), nullable=True)
    quartile: Mapped[Quartile | None] = mapped_column(Enum(Quartile), nullable=True)
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    evidence_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="published", index=True)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True, index=True)
    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id"), nullable=True, index=True)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_section: Mapped[str | None] = mapped_column(String(120), nullable=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    normalization_action: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    normalization_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_title: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    raw_authors: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_authors: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    raw_impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_impact: Mapped[str | None] = mapped_column(String(180), nullable=True)
    validation_status: Mapped[str] = mapped_column(String(40), default="validated", index=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)

    teacher: Mapped[Teacher | None] = relationship(back_populates="productions")
    period: Mapped[AcademicPeriod] = relationship()
    research_entity: Mapped["ResearchEntity | None"] = relationship()
    import_batch: Mapped["ImportBatch | None"] = relationship(foreign_keys=[import_batch_id])
    import_job: Mapped["ImportJob | None"] = relationship(foreign_keys=[import_job_id])
    authors: Mapped[list["ScientificProductionAuthor"]] = relationship(back_populates="production")

    @property
    def teacher_name(self) -> str:
        return self.teacher.full_name if self.teacher else "Autor interno no resuelto"

    @property
    def career_name(self) -> str | None:
        return self.teacher.career.name if self.teacher else (self.research_entity.career_name if self.research_entity else None)

    @property
    def faculty_name(self) -> str | None:
        return self.teacher.career.faculty.name if self.teacher else (self.research_entity.academic_unit if self.research_entity else None)

    @property
    def year_label(self) -> str:
        return self.period.year_label

    @property
    def cycle(self) -> int:
        return self.period.cycle


class ScientificProductionAuthor(Base):
    __tablename__ = "scientific_production_authors"
    __table_args__ = (
        UniqueConstraint("production_id", "author_order", name="uq_scientific_production_author_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    production_id: Mapped[int] = mapped_column(ForeignKey("scientific_productions.id"), index=True)
    author_order: Mapped[int] = mapped_column(Integer)
    raw_author_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_author_name: Mapped[str | None] = mapped_column(String(220), nullable=True, index=True)
    canonical_identity_key: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    canonical_name: Mapped[str | None] = mapped_column(String(220), nullable=True)
    identity_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    identity_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    identity_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    identity_decided_by: Mapped[str | None] = mapped_column(String(180), nullable=True)
    identity_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    teacher_id: Mapped[int | None] = mapped_column(ForeignKey("teachers.id"), nullable=True, index=True)
    external_researcher_id: Mapped[int | None] = mapped_column(ForeignKey("external_researchers.id"), nullable=True, index=True)
    research_entity_id: Mapped[int | None] = mapped_column(ForeignKey("research_entities.id"), nullable=True, index=True)
    author_type: Mapped[str] = mapped_column(String(30), index=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_section: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_field: Mapped[str | None] = mapped_column(String(120), nullable=True)
    row_or_block_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    validation_status: Mapped[str] = mapped_column(String(40), default="pending_review", index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True, index=True)
    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id"), nullable=True, index=True)
    parser_version: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)

    production: Mapped[ScientificProduction] = relationship(back_populates="authors")
    teacher: Mapped["Teacher | None"] = relationship()
    external_researcher: Mapped["ExternalResearcher | None"] = relationship()
    research_entity: Mapped["ResearchEntity | None"] = relationship()
    import_batch: Mapped["ImportBatch | None"] = relationship()
    import_job: Mapped["ImportJob | None"] = relationship()

    @property
    def author_name(self) -> str:
        return self.normalized_author_name or self.raw_author_name or ""

    @property
    def person_type(self) -> str:
        return {
            "internal": "docente_interno",
            "internal_teacher": "docente_interno",
            "external": "investigador_externo",
            "external_researcher": "investigador_externo",
            "unresolved": "pendiente_clasificacion",
        }.get(self.author_type, "pendiente_clasificacion")

    @property
    def production_role(self) -> str:
        return "autor_producto"


class ResearchProject(Base):
    __tablename__ = "research_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("academic_periods.id"), index=True)
    name: Mapped[str] = mapped_column(String(250))
    project_type: Mapped[ProjectType] = mapped_column(Enum(ProjectType), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="vigente", index=True)
    progress_percentage: Mapped[float] = mapped_column(Float, default=0)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True, index=True)
    import_job_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id"), nullable=True, index=True)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_section: Mapped[str | None] = mapped_column(String(120), nullable=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    normalization_action: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    normalization_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_project_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_project_name: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    raw_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_code: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    raw_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    raw_progress: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_progress: Mapped[float | None] = mapped_column(Float, nullable=True)

    period: Mapped[AcademicPeriod] = relationship()
    import_batch: Mapped["ImportBatch | None"] = relationship(foreign_keys=[import_batch_id])
    import_job: Mapped["ImportJob | None"] = relationship(foreign_keys=[import_job_id])
    teachers: Mapped[list["ProjectTeacher"]] = relationship(back_populates="project")

    @property
    def year_label(self) -> str:
        return self.period.year_label

    @property
    def cycle(self) -> int:
        return self.period.cycle


class ProjectTeacher(Base):
    __tablename__ = "project_teachers"
    __table_args__ = (UniqueConstraint("project_id", "teacher_id", name="uq_project_teacher"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("research_projects.id"), index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"), index=True)
    role: Mapped[ProjectTeacherRole] = mapped_column(Enum(ProjectTeacherRole))

    project: Mapped[ResearchProject] = relationship(back_populates="teachers")
    teacher: Mapped[Teacher] = relationship(back_populates="projects")

    @property
    def project_name(self) -> str:
        return self.project.name

    @property
    def project_type(self) -> str:
        return self.project.project_type.value

    @property
    def teacher_name(self) -> str:
        return self.teacher.full_name

    @property
    def teacher_email(self) -> str:
        return self.teacher.institutional_email

    @property
    def career_name(self) -> str:
        return self.teacher.career.name

    @property
    def faculty_name(self) -> str:
        return self.teacher.career.faculty.name


class AnnualGoal(Base):
    __tablename__ = "annual_goals"
    __table_args__ = (
        UniqueConstraint("career_id", "year_label", "metric", name="uq_goal_career_year_metric"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    career_id: Mapped[int] = mapped_column(ForeignKey("careers.id"), index=True)
    year_label: Mapped[str] = mapped_column(String(20), index=True)
    metric: Mapped[GoalMetric] = mapped_column(Enum(GoalMetric), index=True)
    planned_value: Mapped[int] = mapped_column(Integer)

    career: Mapped[Career] = relationship()


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(50), default="PROGRESS_PDF", index=True)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED", index=True)
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(180), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    jobs: Mapped[list["ImportJob"]] = relationship(back_populates="batch")


class ImportJob(Base):
    __tablename__ = "import_jobs"
    __table_args__ = (
        UniqueConstraint("document_key", "source_rev", name="uq_import_jobs_document_revision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(50), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="PENDING", index=True)
    imported_by: Mapped[str | None] = mapped_column(String(180), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_identifier: Mapped[str | None] = mapped_column(String(700), nullable=True, index=True)
    source_rev: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    source_fingerprint: Mapped[str | None] = mapped_column(String(900), nullable=True, index=True)
    document_key: Mapped[str | None] = mapped_column(String(900), nullable=True, index=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("import_jobs.id"), nullable=True, index=True)
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_traceback: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    current_step: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queue_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_extraction_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ocr_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parser_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    persistence_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_ocr: Mapped[bool] = mapped_column(Boolean, default=False)
    extraction_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    batch: Mapped[ImportBatch | None] = relationship(back_populates="jobs")


class ImportReviewItem(Base):
    __tablename__ = "import_review_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_job_id: Mapped[int] = mapped_column(ForeignKey("import_jobs.id"), index=True)
    trace_id: Mapped[int | None] = mapped_column(ForeignKey("imported_ocr_traces.id"), nullable=True, index=True)
    field: Mapped[str] = mapped_column(String(120), index=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_section: Mapped[str | None] = mapped_column(String(120), nullable=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)

    import_job: Mapped[ImportJob] = relationship()
    trace: Mapped["ImportedOcrTrace | None"] = relationship()


class ImportNormalizationAudit(Base):
    __tablename__ = "import_normalization_audits"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True, index=True)
    import_job_id: Mapped[int] = mapped_column(ForeignKey("import_jobs.id"), index=True)
    normalized_record_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_section: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(40), index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)

    import_batch: Mapped["ImportBatch | None"] = relationship()
    import_job: Mapped[ImportJob] = relationship()


class ImportedResearchRecord(Base):
    __tablename__ = "imported_research_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_job_id: Mapped[int] = mapped_column(ForeignKey("import_jobs.id"), index=True)
    faculty_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    national_id: Mapped[str] = mapped_column(String(20), index=True)
    full_name: Mapped[str] = mapped_column(String(180), index=True)
    gender: Mapped[str | None] = mapped_column(String(30), nullable=True)
    project_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    project_code: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    year_label: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    role_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    dedication: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[ImportJob] = relationship()


class ImportedProjectParticipant(Base):
    __tablename__ = "imported_project_participants"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_job_id: Mapped[int] = mapped_column(ForeignKey("import_jobs.id"), index=True)
    ies_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    project_code: Mapped[str] = mapped_column(String(60), index=True)
    project_name: Mapped[str] = mapped_column(String(255))
    project_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    participant_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    participant_identifier: Mapped[str] = mapped_column(String(20), index=True)
    assigned_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    research_group: Mapped[str | None] = mapped_column(String(180), nullable=True)

    job: Mapped[ImportJob] = relationship()


class ImportedProgressReport(Base):
    __tablename__ = "imported_progress_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_job_id: Mapped[int] = mapped_column(ForeignKey("import_jobs.id"), index=True)
    career_name: Mapped[str | None] = mapped_column(String(180), nullable=True, index=True)
    year_label: Mapped[str] = mapped_column(String(20), index=True)
    cycle: Mapped[int] = mapped_column(Integer, index=True)
    teacher_identifier: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    teacher_name: Mapped[str] = mapped_column(String(180), index=True)
    articles: Mapped[int] = mapped_column(Integer, default=0)
    books: Mapped[int] = mapped_column(Integer, default=0)
    book_chapters: Mapped[int] = mapped_column(Integer, default=0)
    presentations: Mapped[int] = mapped_column(Integer, default=0)
    projects: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[ImportJob] = relationship()


class ImportedOcrTrace(Base):
    __tablename__ = "imported_ocr_traces"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_job_id: Mapped[int] = mapped_column(ForeignKey("import_jobs.id"), index=True)
    progress_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("imported_progress_reports.id"),
        nullable=True,
        index=True,
    )
    source_filename: Mapped[str] = mapped_column(String(255), index=True)
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ocr_provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_status: Mapped[str] = mapped_column(String(30), default="PENDIENTE_REVISION", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(180), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)

    job: Mapped[ImportJob] = relationship()
    progress_report: Mapped[ImportedProgressReport | None] = relationship()
