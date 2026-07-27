from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request

from caseops.platform.runtime_envelope import inject_runtime_headers

from .schemas import DependencyHealth, HealthResponse


@dataclass(frozen=True, slots=True)
class HealthEvaluation:
    response: HealthResponse
    status_code: int


def evaluate_readiness(request: Request) -> HealthEvaluation:
    app = request.app
    settings = app.state.settings
    checks = [_database_check(app.state.engine)]
    if settings.readiness_remote_checks:
        mcp_origin = urlsplit(settings.mcp_url)
        checks.extend(
            [
                _http_check(
                    name="mcp",
                    url=f"{mcp_origin.scheme}://{mcp_origin.netloc}/health/live",
                    timeout=settings.readiness_timeout_seconds,
                ),
                _http_check(
                    name="a2a",
                    url=f"{settings.a2a_url.rstrip('/')}/health/live",
                    timeout=settings.readiness_timeout_seconds,
                ),
            ]
        )
    else:
        checks.extend(
            [
                _disabled_check("mcp"),
                _disabled_check("a2a"),
            ]
        )
    return _evaluation(request, checks)


def evaluate_startup(request: Request) -> HealthEvaluation:
    app = request.app
    settings = app.state.settings
    started = time.perf_counter()
    try:
        with app.state.engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != settings.expected_database_revision:
            check = DependencyHealth(
                name="database_schema",
                status="unavailable",
                critical=True,
                latency_ms=_elapsed_ms(started),
                detail=(
                    f"expected revision {settings.expected_database_revision}, "
                    f"observed {revision or 'none'}"
                ),
            )
        else:
            check = DependencyHealth(
                name="database_schema",
                status="ok",
                critical=True,
                latency_ms=_elapsed_ms(started),
                detail=f"alembic revision {revision}",
            )
    except SQLAlchemyError as error:
        check = DependencyHealth(
            name="database_schema",
            status="unavailable",
            critical=True,
            latency_ms=_elapsed_ms(started),
            detail=f"schema check failed: {type(error).__name__}",
        )
    return _evaluation(request, [check])


def _database_check(engine: Engine) -> DependencyHealth:
    started = time.perf_counter()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return DependencyHealth(
            name="database",
            status="ok",
            critical=True,
            latency_ms=_elapsed_ms(started),
            detail="primary transactional store accepted a query",
        )
    except SQLAlchemyError as error:
        return DependencyHealth(
            name="database",
            status="unavailable",
            critical=True,
            latency_ms=_elapsed_ms(started),
            detail=f"database query failed: {type(error).__name__}",
        )


def _http_check(*, name: str, url: str, timeout: float) -> DependencyHealth:
    started = time.perf_counter()
    headers: dict[str, str] = {}
    inject_runtime_headers(headers)
    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return DependencyHealth(
            name=name,
            status="ok",
            critical=False,
            latency_ms=_elapsed_ms(started),
            detail=f"{url} returned {response.status_code}",
        )
    except httpx.HTTPError as error:
        return DependencyHealth(
            name=name,
            status="unavailable",
            critical=False,
            latency_ms=_elapsed_ms(started),
            detail=f"{url} failed: {type(error).__name__}",
        )


def _disabled_check(name: str) -> DependencyHealth:
    return DependencyHealth(
        name=name,
        status="disabled",
        critical=False,
        latency_ms=0,
        detail="remote dependency checks are disabled for this runtime",
    )


def _evaluation(request: Request, checks: list[DependencyHealth]) -> HealthEvaluation:
    app = request.app
    critical_failed = any(
        check.critical and check.status == "unavailable" for check in checks
    )
    optional_failed = any(
        not check.critical and check.status == "unavailable" for check in checks
    )
    if critical_failed:
        overall = "unavailable"
        status_code = 503
    elif optional_failed:
        overall = "degraded"
        status_code = 200
    else:
        overall = "ok"
        status_code = 200
    for check in checks:
        app.state.metrics.dependency_ready.labels(
            dependency=check.name,
            critical=str(check.critical).lower(),
        ).set(1 if check.status in {"ok", "disabled"} else 0)
    settings = app.state.settings
    return HealthEvaluation(
        response=HealthResponse(
            status=overall,
            service=settings.service_name,
            version=settings.service_version,
            checks=tuple(checks),
        ),
        status_code=status_code,
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)
