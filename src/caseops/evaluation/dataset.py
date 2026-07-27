from __future__ import annotations

import json
from importlib.resources import files

from .contracts import EvaluationBaseline, GoldenDataset, QualityContract


def _load_json(name: str) -> object:
    resource = files("caseops.evaluation").joinpath(name)
    return json.loads(resource.read_text(encoding="utf-8"))


def load_dataset() -> GoldenDataset:
    return GoldenDataset.model_validate(_load_json("golden_cases.json"))


def load_quality_contract() -> QualityContract:
    return QualityContract.model_validate(_load_json("quality_contract.json"))


def load_baseline() -> EvaluationBaseline:
    return EvaluationBaseline.model_validate(_load_json("baseline_v0.7.0.json"))
