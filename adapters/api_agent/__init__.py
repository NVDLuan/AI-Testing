"""AnalysisClient implementations (2 flavor HTTP thật + mock offline)."""
from __future__ import annotations

from .base import BaseHttpClient, norm_name
from .handwashing_client import HandWashingHttpClient
from .linfox_client import LinfoxHttpClient
from .mock_client import MockAnalysisClient

__all__ = [
    "BaseHttpClient",
    "norm_name",
    "HandWashingHttpClient",
    "LinfoxHttpClient",
    "MockAnalysisClient",
]
