"""
Pillar: Volume

Compares the row count of the current execution against the historical volume of the same table using Z-score.
When historical data is insufficient (fewer than 3 previous runs), it falls back to the contract's
`min_expected_volume` as a safety net.
"""

from typing import Any

from src.monitoring.anomaly_detection import DEFAULT_WINDOW, detect
from src.quality.pillars.base import MIN_HISTORY_FOR_ZSCORE, PillarFinding


def check_volume(
    table_name: str,
    current_n_rows: int,
    history: list[dict[str, Any]],
    min_expected_volume: int | None = None,
    z_threshold: float = 3.0,
    method: str = "moving",
    window: int = DEFAULT_WINDOW,
) -> PillarFinding:
    """Evaluates whether the current dataset row count aligns with historical volume expectations.

    `history` must include the current execution snapshot as its final element. We compare
    current_n_rows against history[:-1] — everything that came BEFORE this execution.

    Args:
        table_name: Name of the table being validated.
        current_n_rows: Row count of the current execution run.
        history: Chronological list of historical snapshot records (including current).
        min_expected_volume: Minimum row count threshold defined in the contract as a fallback.
        z_threshold: Z-score cutoff threshold for flagging volume anomalies.
        method: Detection strategy ('zscore', 'moving', or 'ewma').
        window: Sliding window size for baseline calculations.

    Returns:
        A PillarFinding object indicating whether volume is within acceptable limits or anomalous.
    """
    previous_volumes = [h["n_rows"] for h in history[:-1]]

    if len(previous_volumes) < MIN_HISTORY_FOR_ZSCORE:
        if min_expected_volume is not None and current_n_rows < min_expected_volume:
            return PillarFinding(
                table_name,
                "volume",
                "anomaly",
                f"Received {current_n_rows} rows, below the contract's minimum expected volume "
                f"({min_expected_volume}). Insufficient history for Z-score analysis "
                f"({len(previous_volumes)}/{MIN_HISTORY_FOR_ZSCORE}).",
            )
        return PillarFinding(
            table_name,
            "volume",
            "insufficient_history",
            f"{current_n_rows} rows. Insufficient history for Z-score analysis "
            f"({len(previous_volumes)}/{MIN_HISTORY_FOR_ZSCORE}); meets contract minimum threshold.",
        )

    result = detect(current_n_rows, previous_volumes, threshold=z_threshold, method=method, window=window)
    if result.is_anomaly:
        z_label = f"{result.z_score:.2f}" if result.z_score is not None else "N/A (zero variance in history)"
        mean_label = f"{result.historical_mean:.0f}" if result.historical_mean is not None else "N/A"
        std_label = f"{result.historical_std:.0f}" if result.historical_std is not None else "N/A"
        return PillarFinding(
            table_name,
            "volume",
            "anomaly",
            f"{current_n_rows} rows (Z-score={z_label}, baseline[{method}]: mean={mean_label}, std={std_label}).",
        )

    return PillarFinding(table_name, "volume", "ok", f"{current_n_rows} rows, within historical norms.")