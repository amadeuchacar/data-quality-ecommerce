"""
Pillar: Distribution

Verifies whether the mean of numerical columns and the missing-value rates per column
remain consistent with historical baselines using Z-score anomaly detection.
"""

from typing import Any

from src.monitoring.anomaly_detection import DEFAULT_WINDOW, detect
from src.quality.pillars.base import MIN_HISTORY_FOR_ZSCORE, PillarFinding


def check_distribution(
    table_name: str,
    history: list[dict[str, Any]],
    z_threshold: float = 3.0,
    method: str = "moving",
    window: int = DEFAULT_WINDOW
) -> PillarFinding:
    """
    Evaluates numerical column distributions and null-rate stability against historical trends

    Args:
        table_name: name of the table being validated
        history: chronological list of historical snapshot records
        z_threshold: z-score cutoff threshold for flagging anomalies
        method: detection strategy ('zscore', 'moving', 'ewma')
        window: sliding window size for baseline calculations
    
    Returns:
        A PillarFinding object indicating whether distribution anomalies were detected
    """
    if not history:
        return PillarFinding(table_name, "distribution", "insufficient_history", "No history available yet.")

    current = history[-1]
    previous = history[:-1]

    if len(previous) < MIN_HISTORY_FOR_ZSCORE:
        return PillarFinding(
            table_name,
            "distribution",
            "insufficient_history",
            f"Insufficient history for Z-score analysis ({len(previous)}/{MIN_HISTORY_FOR_ZSCORE} required)."
        )

    anomalous: list[str] = []

    # 1. Check numerical column mean shifts against historical values
    for col, stats in current.get("numeric_stats", {}).items():
        hist_means = [h["numeric_stats"][col]["mean"] for h in previous if col in h.get("numeric_stats", {})]
        if len(hist_means) < MIN_HISTORY_FOR_ZSCORE:
            continue
        result = detect(stats["mean"], hist_means, threshold=z_threshold, method=method, window=window)
        if result.is_anomaly:
            z_label = f"{result.z_score:.2f}" if result.z_score is not None else "N/A (zero variance in history)"
            anomalous.append(f"{col}: current mean={stats['mean']:.2f} (Z={z_label})")

    # 2. Check null-rate shifts against historical percentages
    for col, null_rate in current.get("null_rates", {}).items():
        hist_rates = [h["null_rates"][col] for h in previous if col in h.get("null_rates", {})]
        if len(hist_rates) < MIN_HISTORY_FOR_ZSCORE:
            continue
        result = detect(null_rate, hist_rates, threshold=z_threshold, method=method, window=window)
        if result.is_anomaly:
            z_label = f"{result.z_score:.2f}" if result.z_score is not None else "N/A (zero variance in history)"
            anomalous.append(f"{col}: nulls={null_rate:.2f}% (Z={z_label})")

    if anomalous:
        return PillarFinding(table_name, "distribution", "anomaly", "; ".join(anomalous))

    return PillarFinding(table_name, "distribution", "ok", "Distribution is consistent with historical baselines.")