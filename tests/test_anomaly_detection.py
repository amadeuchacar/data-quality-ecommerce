"""Unit tests for the anomaly detectors (src.monitoring.anomaly_detection)."""

import pytest

from src.monitoring.anomaly_detection import detect, moving_check, zscore_check

# --- Global Z-score ---------------------------------------------------------------------

def test_zscore_no_anomaly_within_range():
    assert not zscore_check(51.0, [50.0, 49.0, 51.0, 50.0, 50.0], threshold=3.0).is_anomaly


def test_zscore_anomaly_far_outside_range():
    assert zscore_check(1000.0, [50.0, 49.0, 51.0, 50.0, 50.0], threshold=3.0).is_anomaly


def test_zscore_insufficient_history_returns_no_anomaly():
    result = zscore_check(1000.0, [50.0], threshold=3.0)

    assert not result.is_anomaly
    assert result.z_score is None


# --- Moving window / EWMA ---------------------------------------------------------------

def test_moving_detects_anomaly_masked_by_contaminated_history():
    """A historical outlier inflates the global std and hides a later deviation."""
    hist = [100.0, 100.2, 99.9, 100.1, 100.0, 7.5e10, 100.0, 100.1]

    assert not zscore_check(500.0, hist).is_anomaly     # global Z-score limitation
    result = moving_check(500.0, hist)
    assert result.is_anomaly
    assert result.pruned == 1


def test_moving_no_anomaly_on_normal_value():
    assert not moving_check(100.1, [100.0, 100.2, 99.9, 100.1, 100.0]).is_anomaly


def test_moving_catches_drop_that_global_zscore_misses_on_trending_series():
    """150 looks normal against the whole 100..390 series, but is a huge drop from the recent ~390."""
    hist = [float(100 + 10 * i) for i in range(30)]

    assert not zscore_check(150.0, hist).is_anomaly
    assert moving_check(150.0, hist, window=5).is_anomaly
    assert not moving_check(400.0, hist, window=5).is_anomaly   # trend continuation


def test_moving_window_ignores_old_history():
    old_regime = [1000.0] * 20
    new_regime = [100.0, 101.0, 99.0, 100.5, 100.0]

    assert not moving_check(100.0, old_regime + new_regime, window=5).is_anomaly


def test_moving_zero_variance_tolerates_tiny_noise_but_catches_big_change():
    hist = [99441.0] * 6

    assert not moving_check(99440.0, hist).is_anomaly   # 1 row difference: noise
    assert moving_check(50000.0, hist).is_anomaly


def test_moving_short_history_returns_no_anomaly():
    result = moving_check(1000.0, [50.0])

    assert not result.is_anomaly
    assert result.z_score is None


def test_ewma_detects_spike_and_weights_recent_values():
    hist = [100.0, 100.5, 99.5, 100.2, 99.8, 100.1]

    assert moving_check(130.0, hist, method="ewma").is_anomaly
    assert not moving_check(100.3, hist, method="ewma").is_anomaly


@pytest.mark.parametrize("kwargs", [{"method": "xyz"}, {"window": 1}])
def test_moving_rejects_invalid_arguments(kwargs):
    with pytest.raises(ValueError):
        moving_check(1.0, [1.0, 2.0, 3.0], **kwargs)


@pytest.mark.parametrize("method", ["zscore", "moving", "ewma"])
def test_detect_dispatches_by_method(method):
    assert detect(1000.0, [50.0, 49.0, 51.0, 50.0, 50.0], method=method).is_anomaly


def test_detect_rejects_unknown_method():
    with pytest.raises(ValueError):
        detect(1.0, [50.0, 49.0, 51.0], method="foo")
