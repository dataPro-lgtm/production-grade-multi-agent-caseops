from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx

from .contracts import (
    BaselineCase,
    CaseResult,
    EvaluationReport,
    GoldenCase,
    LayerName,
    TrialResult,
)
from .dataset import load_baseline, load_dataset, load_quality_contract
from .evaluators import EvaluationEngine


class LiveHttpTarget:
    def __init__(
        self,
        *,
        base_url: str,
        primary_api_key: str,
        isolated_api_key: str,
        timeout_seconds: float,
    ) -> None:
        self._primary_api_key = primary_api_key
        self._isolated_api_key = isolated_api_key
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def execute(
        self,
        *,
        case: GoldenCase,
        idempotency_key: str,
    ) -> tuple[int, Mapping[str, object], Mapping[str, object] | None, int | None]:
        headers = {
            "X-API-Key": self._primary_api_key,
            "Idempotency-Key": idempotency_key,
        }
        path = "/v1/cases/C-102/system-runs"
        first = await self._client.post(path, headers=headers, json=case.request)
        response = first
        if case.mode == "idempotent_replay":
            if first.status_code >= 400:
                return first.status_code, _json(first), None, None
            response = await self._client.post(path, headers=headers, json=case.request)
        elif case.mode == "idempotency_conflict":
            if first.status_code >= 400:
                return first.status_code, _json(first), None, None
            response = await self._client.post(
                path,
                headers=headers,
                json=case.changed_request,
            )
        payload = _json(response)
        if response.status_code >= 400:
            return response.status_code, payload, None, None
        graph_uri = payload.get("context_graph_uri")
        if not isinstance(graph_uri, str):
            return response.status_code, payload, None, None
        graph_response = await self._client.get(
            graph_uri,
            headers={"X-API-Key": self._primary_api_key},
        )
        graph = _json(graph_response) if graph_response.status_code == 200 else None
        isolated = await self._client.get(
            graph_uri,
            headers={"X-API-Key": self._isolated_api_key},
        )
        return response.status_code, payload, graph, isolated.status_code


def _json(response: httpx.Response) -> Mapping[str, object]:
    try:
        body = response.json()
    except json.JSONDecodeError:
        return {
            "status": response.status_code,
            "code": "NON_JSON_RESPONSE",
            "body_excerpt": response.text[:200],
        }
    if not isinstance(body, dict):
        return {
            "status": response.status_code,
            "code": "NON_OBJECT_RESPONSE",
        }
    return cast(Mapping[str, object], body)


def _layer_scores(trials: Sequence[TrialResult]) -> dict[LayerName, float]:
    values: dict[LayerName, list[float]] = {
        "contract": [],
        "outcome": [],
        "path": [],
        "evidence": [],
        "security": [],
        "efficiency": [],
    }
    for trial in trials:
        for layer in trial.layers:
            values[layer.layer].append(layer.score)
    return {layer: sum(scores) / len(scores) for layer, scores in values.items() if scores}


def _regressions(
    *,
    case_scores: Mapping[LayerName, float],
    consistency: float,
    baseline: BaselineCase,
) -> tuple[str, ...]:
    findings: list[str] = []
    for layer, baseline_score in baseline.layer_scores.items():
        candidate_score = case_scores[layer]
        if candidate_score < baseline_score:
            findings.append(
                f"{layer}: baseline={baseline_score:.3f} candidate={candidate_score:.3f}"
            )
    if consistency < baseline.consistency:
        findings.append(
            f"consistency: baseline={baseline.consistency:.3f} candidate={consistency:.3f}"
        )
    return tuple(findings)


async def run_evaluation(
    *,
    target: LiveHttpTarget,
    candidate_release: str,
) -> EvaluationReport:
    dataset = load_dataset()
    contract = load_quality_contract()
    baseline = load_baseline()
    if baseline.dataset_version != dataset.version:
        raise ValueError("baseline and Golden Dataset versions do not match")
    baseline_by_case = {item.case_id: item for item in baseline.cases}
    engine = EvaluationEngine(contract)
    eval_run_id = f"eval-{uuid.uuid4().hex[:16]}"
    started_at = datetime.now(UTC)
    cases: list[CaseResult] = []

    for case in dataset.cases:
        baseline_case = baseline_by_case.get(case.case_id)
        if baseline_case is None:
            raise ValueError(f"missing baseline case: {case.case_id}")
        if baseline_case.repetitions != case.repetitions:
            raise ValueError(f"repetition contract drifted: {case.case_id}")
        trials: list[TrialResult] = []
        for repetition in range(1, case.repetitions + 1):
            trial_id = f"{eval_run_id}-{case.case_id.lower()}-{repetition}"
            started = time.perf_counter()
            http_status, payload, graph, isolated_status = await target.execute(
                case=case,
                idempotency_key=trial_id,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            layers, fingerprint, diagnostics = engine.evaluate(
                case=case,
                http_status=http_status,
                payload=payload,
                graph=graph,
                isolated_graph_status=isolated_status,
            )
            trials.append(
                TrialResult(
                    trial_id=trial_id,
                    case_id=case.case_id,
                    repetition=repetition,
                    http_status=http_status,
                    elapsed_ms=round(elapsed_ms, 3),
                    semantic_fingerprint=fingerprint,
                    layers=layers,
                    diagnostics=diagnostics,
                )
            )
        scores = _layer_scores(trials)
        fingerprints = {trial.semantic_fingerprint for trial in trials}
        consistency = 1.0 if len(fingerprints) == 1 else 0.0
        regressions = _regressions(
            case_scores=scores,
            consistency=consistency,
            baseline=baseline_case,
        )
        gate_layers = set(contract.release_blocking_layers)
        passed = (
            not regressions
            and consistency >= contract.thresholds.required_consistency
            and all(
                score >= contract.thresholds.required_layer_score
                for layer, score in scores.items()
                if layer in gate_layers
            )
        )
        cases.append(
            CaseResult(
                case_id=case.case_id,
                title=case.title,
                risk=case.risk,
                repetitions=case.repetitions,
                layer_scores=scores,
                consistency=consistency,
                passed=passed,
                regressions=regressions,
                trials=tuple(trials),
            )
        )

    all_trials = [trial for case in cases for trial in case.trials]
    completed_at = datetime.now(UTC)
    passed = all(case.passed for case in cases)
    return EvaluationReport(
        eval_run_id=eval_run_id,
        candidate_release=candidate_release,
        baseline_release=baseline.release,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        quality_contract_version=contract.version,
        started_at=started_at,
        completed_at=completed_at,
        execution_mode="live-http",
        passed=passed,
        summary={
            "case_count": len(cases),
            "trial_count": len(all_trials),
            "passed_cases": sum(case.passed for case in cases),
            "failed_cases": sum(not case.passed for case in cases),
            "mean_elapsed_ms": round(
                sum(trial.elapsed_ms for trial in all_trials) / len(all_trials),
                3,
            ),
            "n_run_semantic_drift_cases": sum(case.consistency < 1.0 for case in cases),
            "release_decision": "pass" if passed else "block",
        },
        cases=tuple(cases),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m caseops.evaluation.runner",
        description="Run the versioned CaseOps Golden Dataset against a live API.",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--api-key", default="caseops-local-dev-key")
    parser.add_argument("--isolated-api-key", default="caseops-other-tenant-key")
    parser.add_argument("--candidate-release", default="v0.8.0")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


async def _async_main(args: argparse.Namespace) -> int:
    target = LiveHttpTarget(
        base_url=args.base_url,
        primary_api_key=args.api_key,
        isolated_api_key=args.isolated_api_key,
        timeout_seconds=args.timeout_seconds,
    )
    try:
        report = await run_evaluation(
            target=target,
            candidate_release=args.candidate_release,
        )
    finally:
        await target.close()
    payload = report.model_dump_json(indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        decision = str(report.summary["release_decision"]).upper()
        print(
            f"{decision}: {report.summary['passed_cases']}/{report.summary['case_count']} "
            f"cases, {report.summary['trial_count']} trials, "
            f"semantic drift cases={report.summary['n_run_semantic_drift_cases']}"
        )
        for case in report.cases:
            marker = "PASS" if case.passed else "FAIL"
            print(
                f"{marker} {case.case_id}: N={case.repetitions} "
                f"consistency={case.consistency:.3f}"
            )
            for regression in case.regressions:
                print(f"  regression: {regression}")
    return 0 if report.passed else 2


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_async_main(args))
    except (httpx.HTTPError, ValueError) as error:
        print(f"evaluation infrastructure error: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
