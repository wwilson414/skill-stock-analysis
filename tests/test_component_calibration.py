"""Offline tests for O-8 train-only component calibration."""
import component_calibration as calibration


def _rows(values, returns):
    return [{"date": f"2025-01-{index + 1:02d}", "phase": "range_swing",
             "forward_return": forward, "comp_vol": value,
             "mr_score": value, "score_total": value, "combo_score": value}
            for index, (value, forward) in enumerate(zip(values, returns))]


def test_calibration_uses_oot_baseline_and_beats_it_on_monotonic_data():
    values = [1, 10] * 10
    rows = _rows(values, [-1 if value == 1 else 1 for value in values])

    result = calibration.calibrate_feature(rows, "comp_vol", train_frac=0.6)

    assert result["status"] == "ok"
    assert result["oot_n"] > 0
    assert result["brier_oot_isotonic"] < result["brier_oot_baseline"]


def test_calibration_flips_negative_train_relationship():
    rows = _rows(list(range(20, 0, -1)), [-1] * 5 + [1] * 15)

    result = calibration.calibrate_feature(rows, "mr_score", train_frac=0.6)

    assert result["status"] == "ok"
    assert result["sign_flipped"] is True


def test_combo_blocked_rows_are_excluded_from_feature_pairs():
    rows = _rows(list(range(1, 17)), [-1, 1] * 8)
    for row in rows[:4]:
        row["combo_score"] = None

    result = calibration.calibrate_feature(rows, "combo_score", train_frac=0.5)

    assert result["status"] == "ok"
    assert result["n"] == 12