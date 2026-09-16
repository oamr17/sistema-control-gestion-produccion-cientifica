from pydantic import BaseModel, Field

from app.models.enums import ProjectTeacherRole, ProjectType


class ProjectTeacherInput(BaseModel):
    teacher_id: int
    role: ProjectTeacherRole


class ProjectCreate(BaseModel):
    period_id: int
    name: str = Field(min_length=3, max_length=250)
    project_type: ProjectType
    description: str | None = None
    status: str = "vigente"
    progress_percentage: float = 0
    teachers: list[ProjectTeacherInput] = []


class ProjectParticipantRead(BaseModel):
    id: int
    role: str
    teacher_name: str
    teacher_email: str
    career_name: str
    faculty_name: str

    model_config = {"from_attributes": True}


class ProjectRead(BaseModel):
    id: int
    period_id: int
    name: str
    project_type: ProjectType
    description: str | None = None
    status: str = "vigente"
    progress_percentage: float = 0
    year_label: str
    cycle: int
    teachers: list[ProjectParticipantRead] = []

    model_config = {"from_attributes": True}
