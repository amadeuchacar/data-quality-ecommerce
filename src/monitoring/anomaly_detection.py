"""
Anomaly detection mechanism shared by the Volume and Distribution pillars: Z-score against history.
This module is deliberately small and free of business dependencies: pillars (the "what to check")
import this function (the "how to detect").
"""

import statistics
from dataclasses import dataclass

DEFAULT_WINDOW = 10           # recent executions used as baseline by the moving/ewma methods
DEFAULT_EWMA_ALPHA = 0.3      # weight of the most recent execution in the EWMA
DEFAULT_MIN_REL_STD = 0.01    # std floor as a fraction of |mean| (avoids alarms on 1-row noise)
_MAD_TO_STD = 1.4826          # scales the median absolute deviation to a normal std

METHODS = ("zscore", "moving", "ewma")


@dataclass
class ZScoreResult:
    """
    Represents the outcome of a standard Z-score anomaly detection check.

    Attributes:
        is_anomaly: True if the current value deviates beyond the allowed threshold
        z_score: Computed z-score value, or None if insufficient history
        historical_mean: Mean value of the historical baseline
        historical_std: Standard deviation of the historical baseline
    """
    is_anomaly: bool
    z_score: float | None
    historical_mean: float | None
    historical_std: float | None


def zscore_check(current_value: float, historical_values: list[float], threshold: float = 3.0) -> ZScoreResult:
    """
    Compares current_value against the distribution of historical_values using a z-score.

    With fewer than 2 historical points, a standard deviation cannot be calculated
    (returns is_anomaly=False)
    """
    if len(historical_values) < 2:
        return ZScoreResult(is_anomaly=False, z_score=None, historical_mean=None, historical_std=None)

    mean = statistics.mean(historical_values)
    std = statistics.pstdev(historical_values)

    if std == 0:
        is_anomaly = current_value != mean
        return ZScoreResult(is_anomaly=is_anomaly, z_score=None, historical_mean=mean, historical_std=0.0)

    z = (current_value - mean) / std
    return ZScoreResult(
        is_anomaly=abs(z) > threshold,
        z_score=z,
        historical_mean=mean,
        historical_std=std
    )


@dataclass
class MovingResult(ZScoreResult):
    """Extends ZScoreResult (pillar-compatible) with advanced diagnostic metadata."""
    method: str = "moving"
    window_used: int = 0
    pruned: int = 0


def _prune_outliers(values: list[float], threshold: float, min_rel_std: float) -> list[float]:
    """Removes historical points identified as outliers using median/MAD (contamination-robust)."""
    if len(values) < 3:
        return list(values)

    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    scale = max(_MAD_TO_STD * mad, min_rel_std * abs(med), 1e-12)
    kept = [v for v in values if abs(v - med) / scale <= threshold]

    # safety fallback: never prune to the point of losing baseline viability
    return kept if len(kept) >= 2 else list(values)


def _ewma_stats(values: list[float], alpha: float) -> tuple[float, float]:
    """Computes exponentially weighted moving average (EWMA) and variance (higher weight on recent executions)"""
    mean = values[0]
    var = 0.0
    for v in values[1:]:
        diff = v - mean
        incr = alpha * diff
        mean += incr
        var = (1 - alpha) * (var + diff * incr)

    return mean, var**0.5


def moving_check(
    current_value: float,
    historical_values: list[float],
    threshold: float = 3.0,
    window: int = DEFAULT_WINDOW,
    method: str = "moving",
    ewma_alpha: float = DEFAULT_EWMA_ALPHA,
    prune_outliers: bool = True,
    min_rel_std: float = DEFAULT_MIN_REL_STD
) -> MovingResult:
    """
    Performs a z-score check against a sliding baseline window.

    - method="moving": simple mean/std of the last 'window' executions
    - method="ewma": exponentially weighted mean/std across the last 'window' executions
    - prune_outliers: discards historical points that were already outliers (median/MAD)
                        BEFORE computing the baseline - prevents history contamination
    - min_rel_std: minimum standard deviation floor as a fraction of |mean|. A perfectly
                    constant history (std=0) tolerates fluctuations up to threshold * min_rel_std
                    (3% by default) rather than raising false alarms for single-row variances

    'historical_values' must be in chronological order (oldest first) and MUST NOT contain current_value
    """
    if method not in ("moving", "ewma"):
        raise ValueError(f"method must be 'moving' or 'ewma', received: {method!r}")
    if window < 2:
        raise ValueError("window must be >= 2")

    recent = list(historical_values[-window:])
    if len(recent) < 2:
        return MovingResult(False, None, None, None, method=method, window_used=len(recent))

    used = _prune_outliers(recent, threshold, min_rel_std) if prune_outliers else recent
    pruned = len(recent) - len(used)

    if method == "ewma":
        mean, std = _ewma_stats(used, ewma_alpha)
    else:
        mean = statistics.mean(used)
        std = statistics.pstdev(used)

    std = max(std, min_rel_std * abs(mean))
    if std == 0: # mean is also zero: any non-zero value is a deviation
        return MovingResult(
            current_value != mean,
            None,
            mean,
            0.0,
            method=method,
            window_used=len(used),
            pruned=pruned
        )

    z = (current_value - mean) / std
    return MovingResult(
        abs(z) > threshold,
        z,
        mean,
        std,
        method=method,
        window_used=len(used),
        pruned=pruned
    )


def detect(
    current_value: float,
    historical_values: list[float],
    threshold: float = 3.0,
    method: str = "zscore",
    window: int = DEFAULT_WINDOW,
    **kwargs
) -> ZScoreResult:
    """Single entry point used by the observability pillars: selects the detector strategy by 'method'"""
    if method == "zscore":
        return zscore_check(current_value, historical_values, threshold=threshold)
    if method in ("moving", "ewma"):
        return moving_check(current_value, historical_values, threshold=threshold, window=window, method=method, **kwargs)
    raise ValueError(f"method must be one of {METHODS}, received: {method!r}")