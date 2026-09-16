from fastapi import HTTPException, status
from sqlalchemy.orm import Session, selectinload

from app.models.entities import Career, ProjectTeacher, ResearchProject, Teacher, User
from app.models.enums import UserRole
from app.schemas.projects import ProjectCreate
from app.services.validated_read_service import ValidatedReadService


class ProjectService:
    def __init__(self, db: Session, user: User):
        self.db = db
        self.user = user

    def list(self, period_id: int | None = None, career_id: int | None = None) -> list[dict]:
        query = self.db.query(ResearchProject).options(
            selectinload(ResearchProject.period),
            selectinload(ResearchProject.teachers)
            .selectinload(ProjectTeacher.teacher)
            .selectinload(Teacher.career)
            .selectinload(Career.faculty),
        ).distinct()
        if period_id:
            query = query.filter(ResearchProject.period_id == period_id)
        if self.user.role == UserRole.CAREER_MANAGER or career_id:
            scoped_career_id = self.user.career_id if self.user.role == UserRole.CAREER_MANAGER else career_id
            query = query.join(ProjectTeacher).join(Teacher).filter(Teacher.career_id == scoped_career_id)
        projects = query.order_by(ResearchProject.name).all()
        teachers = {
            assignment.teacher.id: assignment.teacher
            for project in projects
            for assignment in project.teachers
        }
        effective_names = ValidatedReadService(self.db).effective_teacher_names(
            list(teachers.values())
        )
        return [
            {
                "id": project.id,
                "period_id": project.period_id,
                "name": project.name,
                "project_type": project.project_type,
                "description": project.description,
                "status": project.status,
                "progress_percentage": project.progress_percentage,
                "year_label": project.year_label,
                "cycle": project.cycle,
                "teachers": [
                    {
                        "id": assignment.id,
                        "role": assignment.role,
                        "teacher_name": effective_names.get(
                            assignment.teacher_id,
                            assignment.teacher_name,
                        ),
                        "teacher_email": assignment.teacher_email,
                        "career_name": assignment.career_name,
                        "faculty_name": assignment.faculty_name,
                    }
                    for assignment in project.teachers
                ],
            }
            for project in projects
        ]

    def create(self, data: ProjectCreate) -> ResearchProject:
        if self.user.role == UserRole.CAREER_MANAGER:
            teacher_ids = [item.teacher_id for item in data.teachers]
            careers = {
                row[0]
                for row in self.db.query(Teacher.career_id).filter(Teacher.id.in_(teacher_ids)).all()
            }
            if self.user.career_id not in careers:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Al menos un docente del proyecto debe pertenecer a tu carrera",
                )

        project = ResearchProject(
            period_id=data.period_id,
            name=data.name,
            project_type=data.project_type,
            description=data.description,
        )
        self.db.add(project)
        self.db.flush()
        for teacher in data.teachers:
            self.db.add(
                ProjectTeacher(
                    project_id=project.id,
                    teacher_id=teacher.teacher_id,
                    role=teacher.role,
                )
            )
        self.db.commit()
        self.db.refresh(project)
        return project
