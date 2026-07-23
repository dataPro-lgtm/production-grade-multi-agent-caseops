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
    agent_runs: Counter
    agent_steps: Histogram
    collaboration_runs: Counter

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
            agent_runs=Counter(
                "caseops_agent_runs_total",
                "Controlled Agent runs by terminal status and planner",
                ("status", "planner", "replayed"),
                registry=registry,
            ),
            agent_steps=Histogram(
                "caseops_agent_run_steps",
                "Tool proposal steps per controlled Agent run",
                buckets=(1, 2, 3, 4, 5, 8, 13, 21),
                registry=registry,
            ),
            collaboration_runs=Counter(
                "caseops_collaboration_runs_total",
                "Multi-Agent collaboration runs by terminal status and replay state",
                ("status", "replayed"),
                registry=registry,
            ),
        )


class RequestTimer:
    def __init__(self) -> None:
        self._started = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self._started
