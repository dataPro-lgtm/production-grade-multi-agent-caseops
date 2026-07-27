from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from caseops.config import Settings, get_settings
from caseops.database import build_engine, build_session_factory
from caseops.errors import (
    ActionNotAllowed,
    CaseNotFound,
    CaseOpsError,
    DataContractError,
    IdempotencyConflict,
    PolicyNotFound,
    SystemRunNotFound,
    SystemRunNotTerminal,
)
from caseops.logging_config import configure_logging
from caseops.platform.runtime_envelope import (
    RuntimeEnvelopeMiddleware,
    TelemetryRuntime,
)

from .auth import AuthenticationFailed
from .observability import ApiMetrics, RequestTimer
from .routes import router

logger = logging.getLogger("caseops.api")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,80}$")


def _problem(
    *,
    request: Request,
    status_code: int,
    code: str,
    title: str,
    detail: str,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": f"https://caseops.dev/problems/{code.lower()}",
            "title": title,
            "status": status_code,
            "detail": detail,
            "instance": request.url.path,
            "code": code,
            "request_id": request_id,
        },
    )


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    resolved_engine = engine or build_engine(resolved_settings)
    telemetry = TelemetryRuntime(
        settings=resolved_settings,
        service_name=resolved_settings.service_name,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "service_started",
            extra={
                "service": resolved_settings.service_name,
                "version": resolved_settings.service_version,
                "environment": resolved_settings.environment,
            },
        )
        yield
        resolved_engine.dispose()
        telemetry.shutdown()
        logger.info("service_stopped")

    app = FastAPI(
        title="CaseOps API",
        version=resolved_settings.service_version,
        description=(
            "Production-oriented case investigation reference service. "
            "Slice 0 provides a deterministic baseline; Slice 1 adds a controlled "
            "tool state machine and governed MCP execution; Slice 2 adds typed "
            "delegation, A2A specialists, deterministic evidence join, and events; "
            "Slice 3 adds governed hybrid retrieval, graph paths, Context Packs, "
            "claim-citation binding, and Context Trace; Slice 4 adds runtime "
            "deadlines, dependency-aware readiness, OpenTelemetry, SLO rules, "
            "and verified PostgreSQL recovery; Slice 5 adds deterministic Tool "
            "Guard policy, privacy release controls, security decision audit, "
            "and red-team regression; Slice 6 adds a validated system DAG, "
            "hierarchical team convergence, deterministic cross-team acceptance, "
            "and a queryable runtime Context Graph; Slice 7 adds a versioned "
            "Golden Dataset, layered deterministic graders, N-run semantic "
            "consistency, and paired release gates; Slice 8 adds durable "
            "operational assessments, causal incident bundles, honest "
            "resource-count attribution, and a verified A2A recovery GameDay; "
            "Slice 9 closes the delivery chain with clean-room reproduction, "
            "a release evidence manifest, SBOM, checksums, and verifiable "
            "build provenance."
        ),
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.engine = resolved_engine
    app.state.session_factory = build_session_factory(resolved_engine)
    app.state.metrics = ApiMetrics.create()
    app.state.metrics.build.info(
        {
            "service": resolved_settings.service_name,
            "version": resolved_settings.service_version,
            "environment": resolved_settings.environment,
        }
    )
    app.state.telemetry = telemetry
    app.add_middleware(
        RuntimeEnvelopeMiddleware,
        runtime=telemetry,
        default_timeout_seconds=resolved_settings.request_default_timeout_seconds,
        max_timeout_seconds=resolved_settings.request_max_timeout_seconds,
    )

    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied = request.headers.get("X-Request-ID", "")
        request_id = (
            supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid.uuid4())
        )
        request.state.request_id = request_id
        timer = RequestTimer()
        app.state.metrics.inflight.inc()
        response: Response
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            raise
        finally:
            app.state.metrics.inflight.dec()
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
        if response.status_code == status.HTTP_408_REQUEST_TIMEOUT:
            app.state.metrics.deadline_rejections.labels(reason="expired").inc()
        elif response.status_code == status.HTTP_400_BAD_REQUEST:
            app.state.metrics.deadline_rejections.labels(reason="invalid").inc()
        response.headers["X-Request-ID"] = request_id
        app.state.metrics.requests.labels(
            method=request.method,
            route=route_path,
            status=str(response.status_code),
        ).inc()
        app.state.metrics.latency.labels(
            method=request.method,
            route=route_path,
        ).observe(timer.elapsed())
        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "route": route_path,
                "status_code": response.status_code,
                "duration_seconds": round(timer.elapsed(), 6),
                "trace_id": response.headers.get("X-Trace-ID", "unavailable"),
                "request_deadline": response.headers.get(
                    "X-Request-Deadline",
                    "unavailable",
                ),
            },
        )
        return response

    @app.exception_handler(CaseOpsError)
    async def caseops_error_handler(
        request: Request,
        error: CaseOpsError,
    ) -> JSONResponse:
        mapping: dict[type[CaseOpsError], tuple[int, str]] = {
            AuthenticationFailed: (status.HTTP_401_UNAUTHORIZED, "Unauthorized"),
            CaseNotFound: (status.HTTP_404_NOT_FOUND, "Case not found"),
            PolicyNotFound: (status.HTTP_409_CONFLICT, "Policy unavailable"),
            SystemRunNotFound: (status.HTTP_404_NOT_FOUND, "System run not found"),
            SystemRunNotTerminal: (
                status.HTTP_409_CONFLICT,
                "System run not terminal",
            ),
            IdempotencyConflict: (status.HTTP_409_CONFLICT, "Idempotency conflict"),
            ActionNotAllowed: (
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Action not allowed",
            ),
            DataContractError: (
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Data contract violation",
            ),
        }
        status_code, title = mapping.get(
            type(error),
            (status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal error"),
        )
        return _problem(
            request=request,
            status_code=status_code,
            code=error.code,
            title=title,
            detail=error.message,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        _: RequestValidationError,
    ) -> JSONResponse:
        return _problem(
            request=request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="REQUEST_VALIDATION_FAILED",
            title="Request validation failed",
            detail="请求路径、请求头或请求体不符合 API 契约。",
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error_handler(
        request: Request,
        _: SQLAlchemyError,
    ) -> JSONResponse:
        return _problem(
            request=request,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="DATABASE_UNAVAILABLE",
            title="Database unavailable",
            detail="数据库当前不可用，请稍后重试。",
        )

    app.include_router(router)
    return app


app = create_app()
