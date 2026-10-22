"""
Shared types for the observability pillars.

Every pillar returns a PillarFinding, so the checks runner, the SLO evaluation, the
alerting and the incident log can treat freshness, volume, schema and distribution alike.
"""

from dataclasses import dataclass
from typing import Literal

Status = Literal["ok", "anomaly", "insufficient_history"]

# Minimum number of PREVIOUS executions before a statistical baseline (Z-score) is trusted
MIN_HISTORY_FOR_ZSCORE = 3


@dataclass
class PillarFinding:
    """
    Represents the evaluation finding for an observability pillar check.

    Attributes:
        table: name of the evaluated table
        pillar: name of the observability pillar
        status: outcome status
        detail: human-readable description of the finding
    """
    table: str
    pillar: str
    status: Status
    detail: str
