"""P0 statistics + verdict unit tests (solidated from /tmp scratch, ROADMAP P3-13)."""
import random

from collections import Counter

from p0_backtest import (_pearson, _ic_stats, _cross_section,
                         label_market_regimes, _align_bench_to_stock,
                         _halfyear, _bucket_of, decide)


def test_pearson_perfect_positive():
    assert abs(_pearson([1, 2, 3, 4], [6, 8, 10, 12]) - 1.0) < 1e-9


def test_pearson_constant_y():
    assert _pearson([1, 2, 3], [5, 5, 5]) is None


def test_ic_stats_significant_positive():
    rng = random.Random(0)
    xs = [rng.gauss(0, 1) for _ in range(500)]
    ys = [0.6 * x + rng.gauss(0, 0.2) for x in xs]
    st = _ic_stats(list(zip(xs, ys)), 200)
    assert st["ic"] > 0.5
    assert st["ci95"][0] > 0.4
    assert st["p_value"] < 1e-3
    assert st["ci_includes_zero"] is False


def test_ic_stats_antipositive_ci():
    rng = random.Random(1)
    xs = [rng.gauss(0, 1) for _ in range(500)]
    ys = [-0.6 * x + rng.gauss(0, 0.2) for x in xs]
    st = _ic_stats(list(zip(xs, ys)), 200)
    assert st["ic"] < -0.5
    assert st["ci95"][1] < -0.4


def test_bootstrap_deterministic():
    xs, ys = [1, 2, 3, 4, 5, 6], [2, 4, 1, 5, 3, 6]
    a = _ic_stats(list(zip(xs, ys)), 100)["ci95"]
    b = _ic_stats(list(zip(xs, ys)), 100)["ci95"]
    assert a == b


def test_tiny_sample_ci_includes_zero():
    rng = random.Random(7)
    xs = [rng.gauss(0, 1) for _ in range(20)]
    ys = [0.05 * x + rng.gauss(0, 8) for x in xs]
    st = _ic_stats(list(zip(xs, ys)), 300)
    assert st["ci_includes_zero"] is True


def test_cross_section_sign_consistency():
    cs = _cross_section([-0.30, -0.25, -0.20, 0.01, 0.02])
    assert cs["pct_negative"] == 60.0
    assert cs["t_stat"] is not None and cs["t_stat"] < 0
    assert cs["p_value"] < 0.2


def test_regime_label_counts():
    bars, p = [], 100.0
    for i in range(200):
        p *= 1.015
        bars.append({"date": f"2024-0{i // 30 + 1:02d}-{i % 30 + 1:02d}",
                     "close": p})
    for i in range(120):
        p *= 0.99
        bars.append({"date": f"2025-0{i // 30 + 1:02d}-{i % 30 + 1:02d}",
                     "close": p})
    regs = Counter(v["regime"] for v in label_market_regimes(bars).values())
    assert regs["bull"] > 80 and regs["bear"] > 60
    assert regs["warmup"] == 60


def test_align_forward_fill_missing():
    stk = [{"date": "2024-01-01"}, {"date": "2024-01-02"},
           {"date": "2024-01-03"}]
    bn = [{"date": "2024-01-01", "close": 100.0},
          {"date": "2024-01-03", "close": 102.0}]
    al, miss = _align_bench_to_stock(stk, bn)
    assert al == [100.0, 100.0, 102.0]
    assert miss == 0


def test_halfyear_and_bucket():
    assert _halfyear("2022-05-12") == "2022-H1"
    assert _halfyear("2022-11-02") == "2022-H2"
    assert _bucket_of(88) == "75+"
    assert _bucket_of(50) == "45-60"


def test_decide_branches():
    mk = lambda dn, ci, up, rg, ov: {"overall": {"all": {"ic": ov}},
                                     "by_phase": {"downtrend_decline":
                                                  {"ic": dn, "ci95": ci},
                                                  "uptrend_pullback": {"ic": up},
                                                  "range_swing": {"ic": rg}},
                                     "by_phase_x_regime": {},
                                     "by_halfyear_x_phase": {},
                                     "cross_section": {}}
    assert decide(mk(-0.19, [-0.28, -0.10], 0.05, 0.03, -0.06))["branch"] \
        == "regime_structural"
    assert decide(mk(0.05, [0.0, 0.11], 0.05, 0.06, 0.05))["branch"] \
        == "window_specific"
    assert decide(mk(-0.02, [-0.12, 0.08], 0.01, -0.01, 0.005))["branch"] \
        == "no_signal"
