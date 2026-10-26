"""
Stage 4 — Root Cause Analysis (RCA) and Postmortem

The 5 Whys technique is deliberately simple and manual: its purpose is not
to automate the investigation, but to structure the reasoning of the investigator. This module
provides only the structure (dataclass) and persistence for the RCA filled out by a human.

The postmortem follows the format of chronology, impact, root cause, corrective actions
and is blameless by design: there is no field for "person responsible for the error", only for
"person responsible for the investigation" (conducted_by) and "corrective actions".

The command line in src/incidents/cli.py is the human entry point: it records the RCA and
writes the postmortem to reports/incidents/postmortems/.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class RCA:
    """Represents a Root Cause Analysis (RCA) record based on the 5 Whys technique.

    Attributes:
        incident_id: Unique UUID of the associated incident.
        whys: List of sequential explanatory answers following the 5 Whys methodology.
        root_cause: Summary description of the identified root cause.
        conducted_by: Name or identifier of the investigator who conducted the RCA.
        conducted_at: ISO-8601 timestamp of when the RCA was completed.
    """

    incident_id: str
    whys: list[str]
    root_cause: str
    conducted_by: str
    conducted_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def as_dict(self) -> dict[str, Any]:
        """Serializes the RCA dataclass into a standard dictionary."""
        return asdict(self)


def save_rca(rca: RCA, incidents_dir: Path) -> None:
    """Persists an RCA record to the JSONL history file.

    Args:
        rca: The RCA instance to save.
        incidents_dir: Directory where incident logs and RCAs are stored.
    """
    incidents_dir.mkdir(parents=True, exist_ok=True)
    with open(incidents_dir / "rca.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rca.as_dict(), ensure_ascii=False) + "\n")


def load_rca_for_incident(incident_id: str, incidents_dir: Path) -> dict[str, Any] | None:
    """Retrieves the most recent RCA record of a specific incident (an RCA can be redone).

    Args:
        incident_id: UUID of the incident.
        incidents_dir: Directory where RCA logs are stored.

    Returns:
        The latest RCA record dictionary if found, otherwise None.
    """
    path = incidents_dir / "rca.jsonl"
    if not path.exists():
        return None
    latest = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            if record["incident_id"] == incident_id:
                latest = record
    return latest


def render_postmortem(
    incident: dict[str, Any],
    rca: dict[str, Any] | RCA,
    timeline: list[str],
    impact: str,
    corrective_actions: list[str],
) -> str:
    """Generates a Markdown postmortem document from an incident record and its corresponding RCA.

    `timeline`, `impact`, and `corrective_actions` are provided by the investigator.

    Args:
        incident: Incident dictionary loaded from incidents.jsonl.
        rca: RCA dictionary or instance.
        timeline: Chronological list of events leading up to and following the incident.
        impact: Description of business or technical impact.
        corrective_actions: List of preventative actions to avoid recurrence.

    Returns:
        A formatted Markdown string containing the postmortem report.
    """
    rca_dict = rca.as_dict() if isinstance(rca, RCA) else rca

    lines = [
        f"# Postmortem — {incident['table']} / {incident['pillar']}",
        "",
        "> Blameless document. The objective is to understand why the incident",
        "> was possible and how to prevent its recurrence — not to assign fault.",
        "",
        "## Summary",
        f"- **Affected table:** `{incident['table']}`",
        f"- **Detecting pillar:** `{incident['pillar']}`",
        f"- **Detected at:** {incident['detected_at']}",
        f"- **TTD (Time to Detection):** {incident['ttd_seconds']:.1f}s",
        f"- **Impacted downstream tables:** {', '.join(incident['affected_tables']) or 'none'}",
        "",
        "## Timeline",
    ]
    lines.extend(f"- {event}" for event in timeline)

    lines += [
        "",
        "## Impact",
        impact,
        "",
        "## Root Cause Analysis (5 Whys Technique)",
    ]
    for i, why in enumerate(rca_dict["whys"], start=1):
        lines.append(f"{i}. {why}")
    lines += [
        "",
        f"**Identified root cause:** {rca_dict['root_cause']}",
        "",
        "## Corrective Actions",
    ]
    lines.extend(f"- {action}" for action in corrective_actions)

    lines += [
        "",
        "## Investigation Metadata",
        f"- Conducted by: {rca_dict['conducted_by']}",
        f"- Investigation date: {rca_dict['conducted_at']}",
        f"- Incident ID: {incident['incident_id']}",
    ]

    return "\n".join(lines) + "\n"


def save_postmortem(incident_id: str, markdown: str, incidents_dir: Path) -> Path:
    """Writes the postmortem Markdown to <incidents_dir>/postmortems/<incident_id>.md."""
    out_dir = incidents_dir / "postmortems"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{incident_id}.md"
    path.write_text(markdown, encoding="utf-8")
    return path