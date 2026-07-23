from __future__ import annotations

import time
from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Histogram


@dataclass(slots=True)
class ApiMetrics:
    registry: CollectorRegistry
    requests: Counter
    latency: Histogram
    investigations: Counter

    @classmethod
    def create(cls) -> ApiMetrics:
        registry = CollectorRegistry()
        return cls(
            registry=registry,
            requests=Counter(
                "caseops_http_requests_total",
                "HTTP requests handled by CaseOps",
                ("method", "route", "status"),
                registry=registry,
            ),
            latency=Histogram(
                "caseops_http_request_duration_seconds",
                "CaseOps HTTP request latency",
                ("method", "route"),
                registry=registry,
            ),
            investigations=Counter(
                "caseops_investigations_total",
                "Case investigations by outcome and replay status",
                ("decision_code", "replayed"),
                registry=registry,
            ),
        )


class RequestTimer:
    def __init__(self) -> None:
        self._started = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self._started
