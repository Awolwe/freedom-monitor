"""Тесты проверяемости H1 и расчёта мощности."""
from __future__ import annotations

import random

import pytest

from fm.hypothesis import (
    LAGS,
    circular_shift_test,
    escape_series,
    lagged_outcome,
    months_needed,
    power,
    quadrant_series,
    statistic,
)


def row(month, ff=0.0, fo=0.0, esc=0):
    return {
        "month": month, "level_freedom_from": ff, "level_freedom_for": fo,
        "esc_authoritarianism": esc, "esc_destructiveness": 0, "esc_conformity": 0,
    }


# --- извлечение рядов ------------------------------------------------------

def test_quadrant_is_only_x_positive_y_negative():
    rows = [row("m1", 0.2, -0.1), row("m2", 0.2, 0.1),
            row("m3", -0.2, -0.1), row("m4", -0.2, 0.1)]
    assert quadrant_series(rows) == [1, 0, 0, 0]


def test_quadrant_excludes_exact_zero_on_either_axis():
    """Ноль — не «в квадранте»: на нуле знак не определён."""
    assert quadrant_series([row("m", 0.0, -0.1), row("m", 0.2, 0.0)]) == [0, 0]


def test_quadrant_rejects_unknown_axis():
    with pytest.raises(ValueError, match="неизвестная ось"):
        quadrant_series([row("m")], x="freedom_frum")


def test_escape_series_sums_all_three_mechanisms():
    r = row("m")
    r.update(esc_authoritarianism=1, esc_destructiveness=2, esc_conformity=3)
    assert escape_series([r]) == [6]


# --- окно лагов ------------------------------------------------------------

def test_lagged_outcome_sums_the_window():
    assert lagged_outcome([0, 1, 2, 3, 4, 5])[0] == 1 + 2 + 3


def test_lagged_outcome_is_none_where_window_runs_past_the_end():
    out = lagged_outcome([0, 1, 2, 3, 4])
    assert out[-1] is None and out[-2] is None and out[-3] is None
    assert out[1] is not None


def test_lagged_outcome_uses_declared_lags():
    assert LAGS == (1, 2, 3)


# --- статистика ------------------------------------------------------------

def test_statistic_is_difference_of_means():
    pred = [1, 0, 1, 0]
    out = [4.0, 1.0, 6.0, 1.0]
    assert statistic(pred, out) == pytest.approx(4.0)


def test_statistic_none_when_predictor_never_fires():
    assert statistic([0, 0, 0], [1.0, 2.0, 3.0]) is None


def test_statistic_ignores_months_with_incomplete_window():
    assert statistic([1, 0, 1], [4.0, 1.0, None]) == pytest.approx(3.0)


# --- тест циклическим сдвигом ---------------------------------------------

def test_p_value_cannot_reach_005_on_twelve_months():
    """Структурный предел: нулевое распределение из n−1 сдвигов даёт минимум p = 1/n.
    §6 назначает порог «≥12 месяцев», на котором отвергнуть нельзя ни при каких данных."""
    n = 12
    pred = [1, 1, 1] + [0] * (n - 3)
    out = lagged_outcome([9] * 3 + [0] * (n - 3))
    res = circular_shift_test(pred, out, random.Random(0))
    assert res.p_value >= 1 / n > 0.05


def test_p_value_can_reach_005_from_twentyone_months():
    n = 21
    pred = [1, 1, 1] + [0] * (n - 3)
    out = lagged_outcome([9] * 3 + [0] * (n - 3))
    res = circular_shift_test(pred, out, random.Random(0))
    assert 1 / n < 0.05
    assert res.p_value is not None


def test_strong_planted_signal_gives_small_p_on_long_series():
    """Предиктор в начале, всплеск тегов строго в окне лага, дальше тишина."""
    n = 60
    pred = [0] * n
    counts = [0] * n
    for t in (10, 11, 30, 31):
        pred[t] = 1
        for l in LAGS:
            counts[t + l] += 4
    res = circular_shift_test(pred, lagged_outcome(counts), random.Random(0))
    assert res.observed > 0
    assert res.p_value < 0.05


def test_no_signal_gives_unremarkable_p():
    rng = random.Random(3)
    n = 60
    pred = [int(rng.random() < 0.15) for _ in range(n)]
    counts = [int(rng.random() < 0.3) for _ in range(n)]
    res = circular_shift_test(pred, lagged_outcome(counts), random.Random(0))
    assert res.p_value > 0.05


def test_result_reports_usable_points_and_predictor_count():
    pred = [1, 1] + [0] * 10
    res = circular_shift_test(pred, lagged_outcome([1] * 12), random.Random(0))
    assert res.n_predictor == 2
    assert res.n_usable == 9, "последние три месяца не имеют полного окна"


# --- мощность --------------------------------------------------------------

def test_power_is_zero_at_twelve_months_regardless_of_effect():
    """Следствие структурного предела: сколь угодно сильный эффект не даёт мощности."""
    assert power(12, base_rate=0.33, effect=5, quad_share=0.15,
                 run_len=2.7, trials=120, seed=1) == 0.0


def test_power_grows_with_series_length():
    kw = dict(base_rate=0.33, effect=3, quad_share=0.15, run_len=2.7, trials=200, seed=5)
    assert power(36, **kw) < power(120, **kw)


def test_power_grows_with_effect_size():
    kw = dict(n_months=60, base_rate=0.33, quad_share=0.15, run_len=2.7, trials=200, seed=5)
    assert power(effect=1, **kw) < power(effect=5, **kw)


def test_power_is_near_alpha_when_hypothesis_is_false():
    """Нулевой эффект должен давать долю отвержений порядка alpha, а не больше —
    иначе тест находит связь там, где её нет."""
    p = power(90, base_rate=0.33, effect=0, quad_share=0.15,
              run_len=2.7, trials=400, seed=11)
    assert p < 0.12, f"ложных отвержений {p:.2f} при alpha=0.05"


def test_months_needed_returns_none_when_target_unreachable():
    assert months_needed(base_rate=0.33, effect=0, quad_share=0.15, run_len=2.7,
                         trials=60, seed=2, candidates=(12, 24)) is None
