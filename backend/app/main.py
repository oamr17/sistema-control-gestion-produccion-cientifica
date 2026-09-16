from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from time import perf_counter

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import engine
from app.core.timing import current_sql_timing, install_sql_timing, reset_sql_timing


app = FastAPI(
    title="Scientific Production Management API",
    version="1.0.0",
    description="Faculty scientific production, KPI, evidence and reporting API.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

install_sql_timing(engine)

app.include_router(api_router, prefix="/api/v1")


@app.middleware("http")
async def add_server_timing_header(request, call_next):
    reset_sql_timing()
    started_at = perf_counter()
    response = await call_next(request)
    backend_ms = (perf_counter() - started_at) * 1000
    sql_ms, sql_count = current_sql_timing()
    response.headers["Server-Timing"] = (
        f"sql;dur={sql_ms:.2f};desc=\"{sql_count} queries\", backend;dur={backend_ms:.2f}"
    )
    if backend_ms > 500 or sql_ms > 500:
        response.headers["X-Performance-Warning"] = "over-500ms"
    return response


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}
