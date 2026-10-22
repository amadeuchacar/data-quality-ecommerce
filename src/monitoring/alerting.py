"""
Minimal alerting layer: prints formatted alerts to the console and persists each alert
to reports/alerts/alerts.jsonl. A webhook (e.g., Slack) can be plugged in via the
ALERT_WEBHOOK_URL environment variable - network failures will never crash the pipeline.
"""

import json
import os
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

Severity = Literal["info", "warning", "critical"]

WEBHOOK_ENV_VAR = "ALERT_WEBHOOK_URL"


def send_alert(message: str, severity: Severity = "warning", alerts_dir: Path | None = None) -> None:
    """
    Dispatches a structured alert to the console, appends it to the JSONL history log
    and optionally delivers it via an external webhook without risking pipeline failure.

    Args:
        message: the descriptive content of the alert
        severity: impact level ('info', 'warning', or 'critical')
        alerts_dir: optional directory path where alerts logs are stored
    """
    timestamp = datetime.now(UTC).isoformat()
    # plain-text tag: emojis break consoles with legacy encodings (e.g. cp1252 on Windows)
    print(f"[ALERT-{severity.upper()}] {message}")

    if alerts_dir is not None:
        alerts_dir.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": timestamp, "severity": severity, "message": message}
        with open(alerts_dir / "alerts.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    webhook_url = os.environ.get(WEBHOOK_ENV_VAR)
    if webhook_url:
        _post_webhook(webhook_url, f"[{severity.upper()}] {message}")


def _post_webhook(url: str, text: str) -> None:
    """Posts the alert to a webhook. Only https URLs are accepted; failures are reported and ignored."""
    if not url.startswith("https://"):
        print(f"  ({WEBHOOK_ENV_VAR} must be an https:// URL - webhook skipped)")
        return

    payload = json.dumps({"text": text}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=3):  # scheme validated above
            pass
    except (OSError, ValueError):   # URLError/HTTPError/timeouts are OSError subclasses
        print("  (failed to send alert webhook - ignored, pipeline execution continues unaffected)")
