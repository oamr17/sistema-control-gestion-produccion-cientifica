from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.entities import AcademicPeriod, AnnualGoal, Career, User
from app.models.enums import GoalMetric, UserRole
from app.schemas.goals import AnnualGoalCreate, AnnualGoalRead, GoalSummaryItem
from app.services.validated_read_service import ValidatedReadService

router = APIRouter()


def _percent(reported_value: int, planned_value: int) -> float:
    if planned_value <= 0:
        return 0
    return round((reported_value / planned_value) * 100, 2)


@router.get("", response_model=list[AnnualGoalRead])
def list_goals(
    year_label: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[AnnualGoal]:
    query = db.query(AnnualGoal)
    if user.role == UserRole.CAREER_MANAGER:
        query = query.filter(AnnualGoal.career_id == user.career_id)
    if year_label:
        query = query.filter(AnnualGoal.year_label == year_label)
    return query.order_by(AnnualGoal.year_label.desc()).all()


@router.get("/summary", response_model=list[GoalSummaryItem])
def goal_summary(
    year_label: str,
    cycle: int,
    career_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[GoalSummaryItem]:
    selected_career_id = user.career_id if user.role == UserRole.CAREER_MANAGER else career_id

    careers_query = db.query(Career)
    if selected_career_id:
        careers_query = careers_query.filter(Career.id == selected_career_id)
    careers = careers_query.all()

    selected_career_ids = [career.id for career in careers]

    if not selected_career_ids:
        return []

    goals = (
        db.query(AnnualGoal)
        .filter(
            AnnualGoal.year_label == year_label,
            AnnualGoal.career_id.in_(selected_career_ids),
        )
        .all()
    )

    period = (
        db.query(AcademicPeriod)
        .filter(AcademicPeriod.year_label == year_label, AcademicPeriod.cycle == cycle)
        .first()
    )
    reported_totals = (
        ValidatedReadService(db).goal_totals(
            period.id,
            selected_career_ids if selected_career_id else None,
        )
        if period
        else {metric: 0 for metric in GoalMetric}
    )

    planned_totals = {metric: 0 for metric in GoalMetric}
    for goal in goals:
        planned_totals[goal.metric] += goal.planned_value

    labels = {
        GoalMetric.SCIENTIFIC_OUTPUT: "Producción científica total",
        GoalMetric.ARTICLES: "Artículos",
        GoalMetric.BOOKS: "Libros",
        GoalMetric.BOOK_CHAPTERS: "Capítulos de libro",
        GoalMetric.PRESENTATIONS: "Ponencias",
        GoalMetric.PROJECTS: "Proyectos",
        GoalMetric.TEACHERS_IN_RESEARCH: "Docentes en investigación",
    }

    ordered_metrics = [
        GoalMetric.SCIENTIFIC_OUTPUT,
        GoalMetric.ARTICLES,
        GoalMetric.BOOKS,
        GoalMetric.BOOK_CHAPTERS,
        GoalMetric.PRESENTATIONS,
        GoalMetric.PROJECTS,
        GoalMetric.TEACHERS_IN_RESEARCH,
    ]

    return [
        GoalSummaryItem(
            metric=metric,
            label=labels[metric],
            planned_value=planned_totals[metric],
            reported_value=reported_totals[metric],
            compliance_percent=_percent(reported_totals[metric], planned_totals[metric]),
        )
        for metric in ordered_metrics
    ]


@router.post("", response_model=AnnualGoalRead)
def upsert_goal(
    payload: AnnualGoalCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> AnnualGoal:
    goal = (
        db.query(AnnualGoal)
        .filter(
            AnnualGoal.career_id == payload.career_id,
            AnnualGoal.year_label == payload.year_label,
            AnnualGoal.metric == payload.metric,
        )
        .first()
    )
    if goal:
        goal.planned_value = payload.planned_value
    else:
        goal = AnnualGoal(**payload.model_dump())
        db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal
