"""CaseOps production-oriented reference system."""

from .application import InvestigateCase
from .domain import InvestigationRequest, InvestigationResult

__version__ = "0.5.0"

__all__ = [
    "InvestigateCase",
    "InvestigationRequest",
    "InvestigationResult",
    "__version__",
]
