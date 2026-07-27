from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.orm import Session

from caseops.agent.service import AgentRunService
from caseops.collaboration.service import CollaborationService
from caseops.context.contracts import ContextInvestigationRequest
from caseops.context.service import ContextInvestigationService
from caseops.service import InvestigationService, Principal

from .auth import authenticate
from .health import evaluate_readiness, evaluate_startup
from .schemas import (
    AgentRunCreate,
    AgentRunResponse,
    CollaborationRunCreate,
    CollaborationRunResponse,
    ContextRunResponse,
    HealthResponse,
    InvestigationCreate,
    InvestigationResponse,
    ProblemDetails,
)

router = APIRouter()


def get_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


@router.get(
    "/health/live",
    response_model=HealthResponse,
    tags=["health"],
)
def liveness(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        version=settings.service_version,
    )


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    tags=["health"],
    responses={503: {"model": ProblemDetails}},
)
def readiness(request: Request, response: Response) -> HealthResponse:
    evaluation = evaluate_readiness(request)
    response.status_code = evaluation.status_code
    return evaluation.response


@router.get(
    "/health/startup",
    response_model=HealthResponse,
    tags=["health"],
    responses={503: {"model": HealthResponse}},
)
def startup(request: Request, response: Response) -> HealthResponse:
    evaluation = evaluate_startup(request)
    response.status_code = evaluation.status_code
    return evaluation.response


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request) -> Response:
    if not request.app.state.settings.expose_metrics:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return Response(
        content=generate_latest(request.app.state.metrics.registry),
        media_type=CONTENT_TYPE_LATEST,
    )


@router.post(
    "/v1/cases/{case_id}/investigations",
    response_model=InvestigationResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["investigations"],
    responses={
        401: {"model": ProblemDetails},
        404: {"model": ProblemDetails},
        409: {"model": ProblemDetails},
        422: {"model": ProblemDetails},
    },
)
def create_investigation(
    case_id: str,
    body: InvestigationCreate,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[Session, Depends(get_session)],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=120,
            pattern=r"^[A-Za-z0-9._:-]+$",
        ),
    ],
) -> InvestigationResponse:
    execution = InvestigationService(session).investigate(
        principal=principal,
        case_id=case_id,
        notification_action=body.notification_action,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
    )
    if execution.replayed:
        response.status_code = status.HTTP_200_OK
    decision = cast(dict[str, object], execution.result["decision"])
    request.app.state.metrics.investigations.labels(
        decision_code=decision["code"],
        replayed=str(execution.replayed).lower(),
    ).inc()
    return InvestigationResponse.model_validate(
        {
            "investigation_id": execution.investigation_id,
            "idempotency_key": execution.idempotency_key,
            "created_at": execution.created_at,
            "replayed": execution.replayed,
            "result": execution.result,
        }
    )


@router.post(
    "/v1/cases/{case_id}/agent-runs",
    response_model=AgentRunResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["agent-runs"],
    responses={
        401: {"model": ProblemDetails},
        404: {"model": ProblemDetails},
        409: {"model": ProblemDetails},
        422: {"model": ProblemDetails},
    },
)
async def create_agent_run(
    case_id: str,
    body: AgentRunCreate,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[Session, Depends(get_session)],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=120,
            pattern=r"^[A-Za-z0-9._:-]+$",
        ),
    ],
) -> AgentRunResponse:
    execution = await AgentRunService(
        session=session,
        session_factory=request.app.state.session_factory,
        settings=request.app.state.settings,
    ).execute(
        principal=principal,
        case_id=case_id,
        goal=body.goal,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
    )
    if execution.replayed or execution.resumed:
        response.status_code = status.HTTP_200_OK
    request.app.state.metrics.agent_runs.labels(
        status=execution.status,
        planner=request.app.state.settings.agent_planner,
        replayed=str(execution.replayed).lower(),
    ).inc()
    if not execution.replayed:
        request.app.state.metrics.agent_steps.observe(execution.step_count)
    return AgentRunResponse.model_validate(asdict(execution))


@router.post(
    "/v1/cases/{case_id}/collaboration-runs",
    response_model=CollaborationRunResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["collaboration-runs"],
    responses={
        401: {"model": ProblemDetails},
        404: {"model": ProblemDetails},
        409: {"model": ProblemDetails},
        422: {"model": ProblemDetails},
    },
)
async def create_collaboration_run(
    case_id: str,
    body: CollaborationRunCreate,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[Session, Depends(get_session)],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=120,
            pattern=r"^[A-Za-z0-9._:-]+$",
        ),
    ],
) -> CollaborationRunResponse:
    execution = await CollaborationService(
        session=session,
        session_factory=request.app.state.session_factory,
        settings=request.app.state.settings,
    ).execute(
        principal=principal,
        case_id=case_id,
        goal=body.goal,
        join_policy=body.join_policy,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
    )
    if execution.replayed:
        response.status_code = status.HTTP_200_OK
    request.app.state.metrics.collaboration_runs.labels(
        status=execution.status,
        replayed=str(execution.replayed).lower(),
    ).inc()
    return CollaborationRunResponse.model_validate(asdict(execution))


@router.post(
    "/v1/cases/{case_id}/context-investigations",
    response_model=ContextRunResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["context-investigations"],
    responses={
        401: {"model": ProblemDetails},
        404: {"model": ProblemDetails},
        409: {"model": ProblemDetails},
        422: {"model": ProblemDetails},
    },
)
def create_context_investigation(
    case_id: str,
    body: ContextInvestigationRequest,
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[Session, Depends(get_session)],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=120,
            pattern=r"^[A-Za-z0-9._:-]+$",
        ),
    ],
) -> ContextRunResponse:
    execution = ContextInvestigationService(session).execute(
        principal=principal,
        case_id=case_id,
        request=body,
        idempotency_key=idempotency_key,
        request_id=request.state.request_id,
    )
    if execution.replayed:
        response.status_code = status.HTTP_200_OK
    request.app.state.metrics.context_runs.labels(
        verdict=execution.status,
        replayed=str(execution.replayed).lower(),
    ).inc()
    return ContextRunResponse.model_validate(asdict(execution))
