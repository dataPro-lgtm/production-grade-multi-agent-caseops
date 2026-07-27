"""Versioned, release-gating evaluation for the CaseOps system."""

from .dataset import load_baseline, load_dataset, load_quality_contract
from .evaluators import EvaluationEngine

__all__ = [
    "EvaluationEngine",
    "load_baseline",
    "load_dataset",
    "load_quality_contract",
]
