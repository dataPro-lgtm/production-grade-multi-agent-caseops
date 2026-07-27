from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping
from contextvars import ContextVar, Token
from datetime import UTC, datetime, timedelta
from typing import Any

from opentelemetry import propagate
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import DEPLOYMENT_ENVIRONMENT, SERVICE_NAME, Resource
from opentelemetry.sdk.resources import SERVICE_VERSION as OTEL_SERVICE_VERSION
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from caseops.config import Settings

_request_deadline: ContextVar[datetime | None] = ContextVar(
    "caseops_request_deadline",
    default=None,
)


def current_deadline() -> datetime | None:
    return _request_deadline.get()


def remaining_seconds(*, ceiling: float | None = None) -> float:
    deadline = current_deadline()
    if deadline is None:
        if ceiling is None:
            raise RuntimeError("request deadline is not available")
        return ceiling
    remaining = max(0.0, (deadline - datetime.now(UTC)).total_seconds())
    return min(remaining, ceiling) if ceiling is not None else remaining


def inject_runtime_headers(headers: MutableMapping[str, str]) -> None:
    propagate.inject(headers)
    deadline = current_deadline()
    if deadline is not None:
        headers["X-Request-Deadline"] = deadline.isoformat()


class TelemetryRuntime:
    def __init__(self, *, settings: Settings, service_name: str) -> None:
        resource = Resource.create(
            {
                SERVICE_NAME: service_name,
                OTEL_SERVICE_VERSION: settings.service_version,
                DEPLOYMENT_ENVIRONMENT: settings.environment,
            }
        )
        self.provider = TracerProvider(resource=resource)
        endpoint = settings.otel_exporter_otlp_traces_endpoint
        if endpoint:
            exporter = OTLPSpanExporter(endpoint=endpoint)
            self.provider.add_span_processor(
                BatchSpanProcessor(
                    exporter,
                    export_timeout_millis=int(
                        settings.otel_batch_export_timeout_seconds * 1000
                    ),
                )
            )
        self.tracer = self.provider.get_tracer("caseops.runtime", settings.service_version)

    def shutdown(self) -> None:
        self.provider.force_flush()
        self.provider.shutdown()


class RuntimeEnvelopeMiddleware:
    """Establish W3C trace context and one absolute deadline per request."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        runtime: TelemetryRuntime,
        default_timeout_seconds: float,
        max_timeout_seconds: float,
    ) -> None:
        self._app = app
        self._runtime = runtime
        self._default_timeout = default_timeout_seconds
        self._max_timeout = max_timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = self._headers(scope)
        try:
            deadline = self._resolve_deadline(headers)
        except ValueError as error:
            await self._reject(send, 400, "INVALID_REQUEST_DEADLINE", str(error))
            return
        if deadline <= datetime.now(UTC):
            await self._reject(
                send,
                408,
                "REQUEST_DEADLINE_EXCEEDED",
                "请求在进入业务处理前已超过截止时间。",
            )
            return

        parent = propagate.extract(headers)
        method = str(scope.get("method", "UNKNOWN"))
        path = str(scope.get("path", "/"))
        status_code = 500
        token: Token[datetime | None] = _request_deadline.set(deadline)
        with self._runtime.tracer.start_as_current_span(
            f"{method} {path}",
            context=parent,
            kind=SpanKind.SERVER,
            attributes={
                "http.request.method": method,
                "url.path": path,
                "caseops.request.deadline": deadline.isoformat(),
            },
        ) as span:
            span_context = span.get_span_context()
            trace_id = f"{span_context.trace_id:032x}"
            traceparent = (
                f"00-{trace_id}-{span_context.span_id:016x}-{span_context.trace_flags:02x}"
            )

            async def send_with_envelope(message: Message) -> None:
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = int(message["status"])
                    response_headers = list(message.get("headers", []))
                    response_headers.extend(
                        [
                            (b"x-trace-id", trace_id.encode()),
                            (b"traceparent", traceparent.encode()),
                            (
                                b"x-request-deadline",
                                deadline.isoformat().encode(),
                            ),
                        ]
                    )
                    message["headers"] = response_headers
                await send(message)

            try:
                await self._app(scope, receive, send_with_envelope)
                span.set_status(
                    Status(StatusCode.ERROR if status_code >= 500 else StatusCode.UNSET)
                )
            except Exception as error:
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR, str(error)))
                raise
            finally:
                span.set_attribute("http.response.status_code", status_code)
                span.set_attribute(
                    "caseops.request.remaining_ms",
                    max(0, int((deadline - datetime.now(UTC)).total_seconds() * 1000)),
                )
                _request_deadline.reset(token)

    def _resolve_deadline(self, headers: Mapping[str, str]) -> datetime:
        now = datetime.now(UTC)
        absolute = headers.get("x-request-deadline")
        relative = headers.get("x-request-timeout-ms")
        if absolute and relative:
            raise ValueError("X-Request-Deadline 与 X-Request-Timeout-Ms 不能同时提供")
        if absolute:
            try:
                parsed = datetime.fromisoformat(absolute.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("X-Request-Deadline 必须是 RFC 3339 时间") from error
            if parsed.tzinfo is None:
                raise ValueError("X-Request-Deadline 必须包含时区")
            requested = parsed.astimezone(UTC)
        elif relative:
            try:
                timeout_ms = int(relative)
            except ValueError as error:
                raise ValueError("X-Request-Timeout-Ms 必须是正整数") from error
            if timeout_ms <= 0:
                raise ValueError("X-Request-Timeout-Ms 必须是正整数")
            requested = now + timedelta(milliseconds=timeout_ms)
        else:
            requested = now + timedelta(seconds=self._default_timeout)
        ceiling = now + timedelta(seconds=self._max_timeout)
        return min(requested, ceiling)

    @staticmethod
    def _headers(scope: Scope) -> dict[str, str]:
        return {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }

    @staticmethod
    async def _reject(
        send: Send,
        status_code: int,
        code: str,
        detail: str,
    ) -> None:
        payload: dict[str, Any] = {
            "type": f"https://caseops.dev/problems/{code.lower()}",
            "title": "Request runtime contract rejected",
            "status": status_code,
            "detail": detail,
            "code": code,
        }
        body = json.dumps(payload, ensure_ascii=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/problem+json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
