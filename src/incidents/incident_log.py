"""
Incident Logging

Every anomaly detected by an observability pillar generates a structured incident record,
including TTD (Time to Detection). Incident RESOLUTION can also be recorded, calculating
TTR (Time to Resolution).

`occurred_at` is when the bad data landed. The checks runner passes the timestamp of the
anomalous snapshot (the ingestion that brought the data), so TTD measures how long the
problem sat in staging before a check caught it. Without that information, `occurred_at`
defaults to `detected_at` (TTD=0).

Each incident also stores `snapshot_at`. The same anomalous snapshot is re-evaluated on every
run until new data arrives, so (table, pillar, snapshot_at) identifies an incident and avoids
opening a duplicate for a problem that is already registered.
"""

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class Incident:
    """Represents a recorded data quality incident.

    Attributes:
        incident_id: Unique UUID string for the incident.
        table: Name of the table where the incident occurred.
        pillar: Observability pillar that detected the anomaly.
        detail: Human-readable description of the issue.
        affected_tables: List of downstream tables impacted via lineage propagation.
        occurred_at: ISO-8601 timestamp of when the issue allegedly occurred.
        detected_at: ISO-8601 timestamp of when the issue was detected.
        ttd_seconds: Time to Detection in seconds.
        severity: alert severity assigned at detection ('info', 'warning' or 'critical').
        snapshot_at: timestamp of the history snapshot that triggered the incident, if any.
    """

    incident_id: str
    table: str
    pillar: str
    detail: str
    affected_tables: list[str]
    occurred_at: str
    detected_at: str
    ttd_seconds: float
    severity: str = "warning"
    snapshot_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serializes the incident dataclass into a standard dictionary."""
        return asdict(self)


def compute_ttd_seconds(occurred_at: datetime, detected_at: datetime) -> float:
    """Computes the Time to Detection (TTD) in seconds."""
    return (detected_at - occurred_at).total_seconds()


def register_incident(
    table: str,
    pillar: str,
    detail: str,
    affected_tables: list[str],
    incidents_dir: Path,
    occurred_at: datetime | None = None,
    severity: str = "warning",
    snapshot_at: str | None = None,
) -> Incident:
    """Creates, persists, and returns a new Incident record.

    Args:
        table: Name of the affected table.
        pillar: Observability pillar name.
        detail: Detailed description of the anomaly.
        affected_tables: Downstream tables impacted via lineage.
        incidents_dir: Directory where incident logs are stored.
        occurred_at: Optional custom timestamp for when the incident started.
        severity: Alert severity assigned at detection.
        snapshot_at: Timestamp of the history snapshot that triggered the incident.

    Returns:
        The generated Incident instance.
    """
    detected_at = datetime.now(UTC)
    occurred_at = occurred_at or detected_at

    incident = Incident(
        incident_id=str(uuid.uuid4()),
        table=table,
        pillar=pillar,
        detail=detail,
        affected_tables=affected_tables,
        occurred_at=occurred_at.isoformat(),
        detected_at=detected_at.isoformat(),
        ttd_seconds=compute_ttd_seconds(occurred_at, detected_at),
        severity=severity,
        snapshot_at=snapshot_at,
    )

    incidents_dir.mkdir(parents=True, exist_ok=True)
    with open(incidents_dir / "incidents.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(incident.as_dict(), ensure_ascii=False) + "\n")

    return incident


def load_incidents(incidents_dir: Path) -> list[dict[str, Any]]:
    """Loads all logged incidents from the JSONL history file.

    Args:
        incidents_dir: Directory where incident logs are stored.

    Returns:
        A list of incident record dictionaries.
    """
    path = incidents_dir / "incidents.jsonl"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def find_incident(
    incidents: list[dict[str, Any]], table: str, pillar: str, snapshot_at: str | None
) -> dict[str, Any] | None:
    """Returns the incident already registered for this table, pillar and snapshot, if any."""
    if snapshot_at is None:
        return None
    return next(
        (
            inc for inc in incidents
            if inc["table"] == table and inc["pillar"] == pillar and inc.get("snapshot_at") == snapshot_at
        ),
        None,
    )


@dataclass
class Resolution:
    """Represents the resolution details for a recorded incident.

    Attributes:
        incident_id: UUID of the resolved incident.
        resolution_summary: Summary of how the issue was fixed.
        resolved_at: ISO-8601 timestamp of when the resolution occurred.
        ttr_seconds: Time to Resolution in seconds.
    """

    incident_id: str
    resolution_summary: str
    resolved_at: str
    ttr_seconds: float

    def as_dict(self) -> dict[str, Any]:
        """Serializes the resolution dataclass into a standard dictionary."""
        return asdict(self)


def compute_ttr_seconds(detected_at: datetime, resolved_at: datetime) -> float:
    """Computes Time to Resolution (TTR): elapsed time between detection and resolution."""
    return (resolved_at - detected_at).total_seconds()


def register_resolution(
    incident_id: str,
    resolution_summary: str,
    incidents_dir: Path,
    detected_at: datetime | None = None,
    resolved_at: datetime | None = None,
) -> Resolution:
    """Registers the resolution of an existing incident, calculating its TTR.

    If `detected_at` is not provided, it is retrieved from the incident record in incidents.jsonl.

    Args:
        incident_id: UUID of the incident being resolved.
        resolution_summary: Description of the corrective action taken.
        incidents_dir: Directory where incident and resolution logs are stored.
        detected_at: Optional custom detection timestamp.
        resolved_at: Optional custom resolution timestamp.

    Returns:
        The generated Resolution instance.
    """
    resolved_at = resolved_at or datetime.now(UTC)

    if detected_at is None:
        incidents = load_incidents(incidents_dir)
        match = next((inc for inc in incidents if inc["incident_id"] == incident_id), None)
        if match is None:
            raise ValueError(f"Incident '{incident_id}' not found in incidents.jsonl.")
        detected_at = datetime.fromisoformat(match["detected_at"])

    resolution = Resolution(
        incident_id=incident_id,
        resolution_summary=resolution_summary,
        resolved_at=resolved_at.isoformat(),
        ttr_seconds=compute_ttr_seconds(detected_at, resolved_at),
    )

    incidents_dir.mkdir(parents=True, exist_ok=True)
    with open(incidents_dir / "resolutions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(resolution.as_dict(), ensure_ascii=False) + "\n")

    return resolution


def load_resolutions(incidents_dir: Path) -> list[dict[str, Any]]:
    """Loads all logged incident resolutions from the JSONL history file.

    Args:
        incidents_dir: Directory where resolution logs are stored.

    Returns:
        A list of resolution record dictionaries.
    """
    path = incidents_dir / "resolutions.jsonl"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]