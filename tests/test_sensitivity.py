"""Тесты анализа чувствительности."""
from __future__ import annotations

import pytest

from fm.aggregate import AXES, Event, add_levels, monthly_flow
from fm.sensitivity import (
    WEIGHT_SCHEMES,
    Config,
    analyse,
    clip_binds,
    flow_with_weights,
    grid,
    levels,
    spearman,
)


def mk(eid, date, *, ff=0, fo=0, so=0, ho=0, sig=2) -> Event:
    return Event(
        id=eid, date=date, month=date[:7], title="t", category="политика",
        scores={"freedom_from": ff, "freedom_for": fo, "solidarity": so, "hope": ho},
        significance=sig, rationale="r",
    )


# --- согласованность с fm.aggregate ---------------------------------------

def test_linear_weights_reproduce_monthly_flow_exactly():
    """Схема «линейная» обязана совпадать с боевой агрегацией, иначе анализ
    измеряет чувствительность не той функции."""
    evs = [mk("a", "2024-03-01", ff=-3, sig=3), mk("b", "2024-03-02", ho=2, sig=1),
           mk("c", "2024-05-01", so=1, sig=2)]
    cfg = Config(12, 0.4, 5, "линейная (текущая)")
    mine = flow_with_weights(evs, cfg)
    theirs = monthly_flow(evs, 12.0, 5.0)
    for m, t in zip(mine, theirs):
        assert m["month"] == t["month"]
        for a in AXES:
            assert m[a] == pytest.approx(t[a])


def test_levels_match_add_levels_on_linear_scheme():
    evs = [mk("a", "2024-03-01", ff=-3, sig=3), mk("b", "2024-04-01", ff=1)]
    cfg = Config(12, 0.4, 5, "линейная (текущая)")
    got = levels(evs, cfg)["freedom_from"]
    want = [r["level_freedom_from"] for r in add_levels(monthly_flow(evs, 12.0, 5.0), 0.4)]
    assert got == pytest.approx(want)


# --- схемы весов -----------------------------------------------------------

def test_equal_weights_ignore_significance():
    evs = [mk("a", "2024-03-01", ff=1, sig=1), mk("b", "2024-03-02", ff=1, sig=3)]
    row = flow_with_weights(evs, Config(12, 0.4, 5, "равные"))[0]
    assert row["freedom_from"] == pytest.approx(2 / 12)


def test_steep_weights_amplify_significance_3():
    evs = [mk("a", "2024-03-01", ff=1, sig=3)]
    linear = flow_with_weights(evs, Config(12, 0.4, 5, "линейная (текущая)"))[0]["freedom_from"]
    steep = flow_with_weights(evs, Config(12, 0.4, 5, "крутая"))[0]["freedom_from"]
    assert steep > linear


def test_every_weight_scheme_covers_all_significance_levels():
    for name, w in WEIGHT_SCHEMES.items():
        assert set(w) == {1, 2, 3}, f"схема {name} не покрывает значимость 1-3"


# --- spearman --------------------------------------------------------------

def test_spearman_perfect_on_monotone_relation():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_is_rank_based_not_value_based():
    """Нелинейное, но монотонное преобразование не должно менять результат."""
    assert spearman([1, 2, 3, 4], [1, 4, 9, 16]) == pytest.approx(1.0)


def test_spearman_handles_ties():
    assert spearman([1, 1, 2, 2], [5, 5, 9, 9]) == pytest.approx(1.0)


def test_spearman_none_when_no_variation():
    assert spearman([1, 1, 1, 1], [1, 2, 3, 4]) is None


def test_spearman_none_on_too_short_input():
    assert spearman([1, 2], [1, 2]) is None


# --- clip ------------------------------------------------------------------

def test_clip_binds_reports_nothing_when_clip_never_reached():
    evs = [mk("a", "2024-03-01", ff=1)]
    assert clip_binds(evs, [Config(12, 0.4, 5, "линейная (текущая)")]) == {}


def test_clip_binds_detects_saturation():
    evs = [mk(f"e{i}", "2024-03-01", ff=3, sig=3) for i in range(10)]  # 90/12 = 7.5 > 5
    assert clip_binds(evs, [Config(12, 0.4, 5, "линейная (текущая)")])


# --- analyse ---------------------------------------------------------------

def test_analyse_flags_sign_flip_between_configs():
    """Воспроизводит найденный в данных случай (2022-04): после большого шока
    идёт месяц со слабым сигналом противоположного знака. Медленное забывание
    оставляет знак шока, быстрое — отдаёт его слабому месяцу. Знак определяется
    выбором α, а не содержанием месяца."""
    evs = [mk("shock", "2024-01-01", ff=-3, sig=3),      # Flow −0.75
           mk("weak", "2024-02-01", ff=1, sig=2)]        # Flow +0.17
    slow, fast = Config(12, 0.2, 5, "линейная (текущая)"), Config(12, 0.8, 5, "линейная (текущая)")
    assert levels(evs, slow)["freedom_from"][1] < 0 < levels(evs, fast)["freedom_from"][1]

    rep = analyse(evs, [slow, fast], slow)
    assert rep["axes"]["freedom_from"]["sign_flips"] == ["2024-02"]


def test_analyse_reports_stable_axis_as_fully_agreeing():
    evs = [mk("a", "2024-01-01", ho=2), mk("b", "2024-02-01", ho=3)]
    cfgs = grid([10, 12], [0.3, 0.4], [5], ["линейная (текущая)"])
    rep = analyse(evs, cfgs, cfgs[0])
    assert rep["axes"]["hope"]["sign_agreement"] == 1.0
    assert rep["axes"]["hope"]["sign_flips"] == []


def test_analyse_final_range_brackets_the_baseline():
    evs = [mk("a", "2024-01-01", ff=-2, sig=3), mk("b", "2024-02-01", ff=1)]
    cfgs = grid([8, 12, 20], [0.3, 0.4], [5], list(WEIGHT_SCHEMES))
    base = Config(12, 0.4, 5, "линейная (текущая)")
    rep = analyse(evs, cfgs + [base], base)
    s = rep["axes"]["freedom_from"]
    assert s["final_min"] <= s["final_baseline"] <= s["final_max"]


def test_grid_is_full_cartesian_product():
    assert len(grid([8, 12], [0.3, 0.4], [5], ["равные", "крутая"])) == 8
