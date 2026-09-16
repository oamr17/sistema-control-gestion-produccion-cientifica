import re

from pydantic import BaseModel, EmailStr, Field, field_serializer


def _visible_email(value: str | None) -> str | None:
    email = str(value or "").strip()
    key = email.lower()
    if not email:
        return None
    if key.startswith("imported-") or key.endswith("@local.import"):
        return None
    if any(marker in key for marker in ("\\", "/", "dropbox", "source_key")):
        return None
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return None
    return email


class TeacherBase(BaseModel):
    career_id: int
    full_name: str = Field(min_length=3, max_length=180)
    institutional_email: EmailStr
    research_hours: int = Field(default=0, ge=0, le=80)
    is_active: bool = True


class TeacherCreate(TeacherBase):
    pass


class TeacherUpdate(BaseModel):
    career_id: int | None = None
    full_name: str | None = Field(default=None, min_length=3, max_length=180)
    institutional_email: EmailStr | None = None
    research_hours: int | None = Field(default=None, ge=0, le=80)
    is_active: bool | None = None


class TeacherProjectSummary(BaseModel):
    id: int
    role: str
    project_name: str
    project_type: str

    model_config = {"from_attributes": True}


class TeacherProductionSummary(BaseModel):
    id: int
    production_type: str
    title: str
    year_label: str
    cycle: int

    model_config = {"from_attributes": True}


class TeacherRead(BaseModel):
    id: int
    career_id: int
    full_name: str
    institutional_email: str | None = None
    research_hours: int
    is_active: bool
    career_name: str
    faculty_name: str
    projects: list[TeacherProjectSummary] = []
    productions: list[TeacherProductionSummary] = []

    @field_serializer("institutional_email")
    def serialize_institutional_email(self, value: str | None) -> str | None:
        return _visible_email(value)

    model_config = {"from_attributes": True}
