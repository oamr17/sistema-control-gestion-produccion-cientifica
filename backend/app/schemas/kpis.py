from pydantic import BaseModel


class ProductionBreakdown(BaseModel):
    articles: int = 0
    books: int = 0
    book_chapters: int = 0
    presentations: int = 0
    unclassified: int = 0


class CareerKpi(BaseModel):
    career_id: int
    career_name: str
    total_teachers: int
    teachers_in_research: int
    teachers_in_research_percent: float
    projects: int
    scientific_output_total: int
    production: ProductionBreakdown
    planned_output: int
    output_compliance_percent: float
    output_variation_percent: float


class NamedCount(BaseModel):
    name: str
    count: int
    id: int | None = None


class DashboardKpi(BaseModel):
    year_label: str
    cycle: int
    reports_received: int
    reports_requires_review: int
    total_teachers: int
    internal_participants_count: int = 0
    internal_fca_count: int = 0
    internal_other_faculty_count: int = 0
    external_participants_count: int = 0
    canonical_identities_count: int = 0
    participant_appearances_count: int = 0
    participant_roles_count: int = 0
    authorships_count: int = 0
    external_researchers_detected: int = 0
    external_researchers_kpi_eligible: int = 0
    external_researchers_pending_review: int = 0
    pending_participants_count: int = 0
    discarded_participants_count: int = 0
    teachers_in_research_percent: float
    scientific_output_total: int
    scientific_output_published: int
    scientific_output_in_review: int
    scientific_output_detected: int = 0
    scientific_output_kpi_eligible: int = 0
    scientific_output_pending_review: int = 0
    scientific_output_discarded: int = 0
    projects_total: int
    projects_current: int
    projects_approved: int
    projects_average_progress_percent: float
    research_entities_detected: int = 0
    research_entities_kpi_eligible: int = 0
    research_entities_pending_review: int = 0
    research_entities_excluded: int = 0
    internal_teachers_by_career: list[NamedCount]
    production_by_career: list[NamedCount]
    external_researchers_by_university: list[NamedCount]
    careers: list[CareerKpi]
    alerts: list[str]
