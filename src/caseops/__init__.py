"""CaseOps production-oriented reference system."""

from .application import InvestigateCase
from .domain import InvestigationRequest, InvestigationResult

__version__ = "0.6.1"

__all__ = [
    "InvestigateCase",
    "InvestigationRequest",
    "InvestigationResult",
    "__version__",
]
