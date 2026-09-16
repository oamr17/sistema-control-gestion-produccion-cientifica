from fastapi import APIRouter

from app.api.v1.endpoints import admin, alerts, auth, dashboard, evidence, goals, human_review, imports, kpis, metadata, participants, production, projects, reports, research_entities, teachers

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(metadata.router, prefix="/metadata", tags=["metadata"])
api_router.include_router(teachers.router, prefix="/teachers", tags=["teachers"])
api_router.include_router(participants.router, prefix="/participants", tags=["participants"])
api_router.include_router(production.router, prefix="/production", tags=["production"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(research_entities.router, prefix="/research-entities", tags=["research-entities"])
api_router.include_router(goals.router, prefix="/goals", tags=["goals"])
api_router.include_router(kpis.router, prefix="/kpis", tags=["kpis"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(evidence.router, prefix="/evidence", tags=["evidence"])
api_router.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(imports.router, prefix="/imports", tags=["imports"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(human_review.router, prefix="/human-review", tags=["human-review"])
