from contextvars import ContextVar
from time import perf_counter

from sqlalchemy import event
from sqlalchemy.engine import Engine


_sql_time_ms: ContextVar[float] = ContextVar("sql_time_ms", default=0.0)
_sql_count: ContextVar[int] = ContextVar("sql_count", default=0)
_query_started_at: ContextVar[float | None] = ContextVar("query_started_at", default=None)


def reset_sql_timing() -> None:
    _sql_time_ms.set(0.0)
    _sql_count.set(0)
    _query_started_at.set(None)


def current_sql_timing() -> tuple[float, int]:
    return _sql_time_ms.get(), _sql_count.get()


def install_sql_timing(engine: Engine) -> None:
    if getattr(engine, "_sql_timing_installed", False):
        return

    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(*_args) -> None:
        _query_started_at.set(perf_counter())

    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(*_args) -> None:
        started_at = _query_started_at.get()
        if started_at is None:
            return
        elapsed_ms = (perf_counter() - started_at) * 1000
        _sql_time_ms.set(_sql_time_ms.get() + elapsed_ms)
        _sql_count.set(_sql_count.get() + 1)
        _query_started_at.set(None)

    setattr(engine, "_sql_timing_installed", True)
