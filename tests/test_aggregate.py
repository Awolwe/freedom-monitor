"""Тесты чистых функций агрегации (METHODOLOGY.md §4)."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from fm import aggregate
from fm.aggregate import AXES, Event, add_levels, data_quality, ema, load_events, monthly_flow


def mk(
    eid: str = "e1",
    date: str = "2024-03-15",
    *,
    month: str | None = None,
    ff: int = 0,
    fo: int = 0,
    so: int = 0,
    ho: int = 0,
    sig: int = 2,
    escape: list[str] | None = None,
    press: bool = False,
    rationale: str = "тест",
    category: str = "политика",
) -> Event:
    return Event(
        id=eid,
        date=date,
        month=month or date[:7],
        title=f"событие {eid}",
        category=category,
        scores={"freedom_from": ff, "freedom_for": fo, "solidarity": so, "hope": ho},
        escape=escape or [],
        press_pressure=press,
        significance=sig,
        rationale=rationale,
    )


# --- схема -----------------------------------------------------------------

def test_month_must_prefix_date():
    with pytest.raises(ValidationError, match="не совпадает"):
        mk(date="2024-03-15", month="2024-04")


def test_month_matching_date_is_accepted():
    assert mk(date="2024-03-15", month="2024-03").month == "2024-03"


@pytest.mark.parametrize("bad", [-4, 4])
def test_scores_outside_range_rejected(bad):
    with pytest.raises(ValidationError):
        mk(ff=bad)


@pytest.mark.parametrize("bad", [0, 4])
def test_significance_outside_range_rejected(bad):
    with pytest.raises(ValidationError):
        mk(sig=bad)


def test_unknown_category_rejected():
    with pytest.raises(ValidationError):
        mk(category="спорт")


def test_unknown_escape_mechanism_rejected():
    with pytest.raises(ValidationError):
        mk(escape=["apathy"])


def test_nonzero_axes_lists_only_touched_axes():
    assert mk(ff=-2, ho=1).nonzero_axes() == ["freedom_from", "hope"]
    assert mk().nonzero_axes() == []


# --- load_events -----------------------------------------------------------

def write_jsonl(path, events: list[dict]):
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8",
    )
    return path


def raw(**over) -> dict:
    base = {
        "id": "e1", "date": "2024-03-15", "month": "2024-03", "title": "t",
        "category": "политика",
        "scores": {"freedom_from": 0, "freedom_for": 0, "solidarity": 0, "hope": 0},
        "significance": 2, "rationale": "r",
    }
    base.update(over)
    return base


def test_load_events_skips_blank_lines(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text(json.dumps(raw(), ensure_ascii=False) + "\n\n\n", encoding="utf-8")
    events, errors = load_events(p)
    assert len(events) == 1 and errors == []


def test_load_events_reports_bad_line_with_number_and_keeps_going(tmp_path):
    p = write_jsonl(tmp_path / "e.jsonl", [raw(id="ok1"), raw(id="bad", significance=9), raw(id="ok2")])
    events, errors = load_events(p)
    assert [e.id for e in events] == ["ok1", "ok2"]
    assert len(errors) == 1 and "e.jsonl:2" in errors[0]


def test_nonzero_scores_without_rationale_is_an_error(tmp_path):
    p = write_jsonl(tmp_path / "e.jsonl", [
        raw(id="x", scores={"freedom_from": -2, "freedom_for": 0, "solidarity": 0, "hope": 0}, rationale="   "),
    ])
    events, errors = load_events(p)
    assert len(events) == 1, "событие остаётся в выборке, ошибка — предупреждение качества"
    assert len(errors) == 1 and "ненулевые оценки без rationale" in errors[0]


def test_all_zero_scores_without_rationale_is_not_an_error(tmp_path):
    p = write_jsonl(tmp_path / "e.jsonl", [raw(rationale="")])
    _, errors = load_events(p)
    assert errors == []


# --- monthly_flow ----------------------------------------------------------

def test_flow_is_weighted_sum_not_mean():
    """Один закон sig-3 не должен размываться десятком нейтральных новостей (§4)."""
    law = [mk("law", "2024-03-01", ff=-3, sig=3)]
    noise = [mk(f"n{i}", "2024-03-02", sig=1) for i in range(10)]
    alone = monthly_flow(law, normalizer=12.0)[0]["freedom_from"]
    diluted = monthly_flow(law + noise, normalizer=12.0)[0]["freedom_from"]
    assert alone == diluted == pytest.approx(-9 / 12)


def test_flow_normalizer_divides_weighted_sum():
    evs = [mk("a", "2024-03-01", ho=2, sig=3)]  # 2×3 = 6
    assert monthly_flow(evs, normalizer=12.0)[0]["hope"] == pytest.approx(0.5)
    assert monthly_flow(evs, normalizer=10.0)[0]["hope"] == pytest.approx(0.6)


@pytest.mark.parametrize("sign,expected", [(1, 5.0), (-1, -5.0)])
def test_flow_is_clipped_symmetrically(sign, expected):
    evs = [mk(f"e{i}", "2024-03-01", ff=sign * 3, sig=3) for i in range(10)]  # ±90/12 = ±7.5
    assert monthly_flow(evs, normalizer=12.0, clip=5.0)[0]["freedom_from"] == expected


def test_months_are_sorted_and_events_partitioned_by_month():
    evs = [mk("b", "2024-05-01", ho=1), mk("a", "2024-03-01", ho=1)]
    rows = monthly_flow(evs, normalizer=12.0)
    assert [r["month"] for r in rows] == ["2024-03", "2024-05"]
    assert all(r["n_events"] == 1 for r in rows)


def test_month_absent_from_data_produces_no_row():
    """Без carry-forward: пропуск в данных — дыра в ряду, а не нулевая точка (§4)."""
    rows = monthly_flow([mk("a", "2024-03-01", ho=1), mk("b", "2024-05-01", ho=1)], normalizer=12.0)
    assert "2024-04" not in [r["month"] for r in rows]


def test_month_with_events_but_no_axis_signal_is_zero_flow():
    row = monthly_flow([mk("a", "2024-03-01", ff=-2)], normalizer=12.0)[0]
    assert row["freedom_from"] != 0 and row["hope"] == 0


def test_escape_and_press_are_raw_counts_not_shares():
    evs = [
        mk("a", "2024-03-01", escape=["conformity", "authoritarianism"], press=True),
        mk("b", "2024-03-02", escape=["conformity"]),
        mk("c", "2024-03-03"),
    ]
    row = monthly_flow(evs, normalizer=12.0)[0]
    assert row["esc_conformity"] == 2
    assert row["esc_authoritarianism"] == 1
    assert row["esc_destructiveness"] == 0
    assert row["press_pressure_n"] == 1


def test_monthly_flow_on_empty_input():
    assert monthly_flow([], normalizer=12.0) == []


# --- ema / levels ----------------------------------------------------------

def test_ema_starts_from_zero_not_from_first_value():
    assert ema([1.0], 0.4) == [0.4]


def test_ema_converges_toward_constant_input():
    out = ema([1.0] * 60, 0.4)
    assert out[-1] == pytest.approx(1.0, abs=1e-3)
    assert out == sorted(out), "монотонное приближение снизу"


def test_ema_alpha_one_is_passthrough():
    assert ema([0.5, -0.25, 2.0], 1.0) == [0.5, -0.25, 2.0]


def test_ema_alpha_zero_never_moves():
    assert ema([5.0, -5.0], 0.0) == [0.0, 0.0]


def test_ema_recurrence_matches_formula():
    vals, alpha = [1.0, -2.0, 0.5], 0.4
    out, lvl = ema(vals, alpha), 0.0
    for v, got in zip(vals, out):
        lvl = (1 - alpha) * lvl + alpha * v
        assert got == pytest.approx(lvl)


def test_add_levels_adds_one_level_per_axis_and_keeps_flow():
    rows = monthly_flow([mk("a", "2024-03-01", ff=-3, sig=3)], normalizer=12.0)
    flow_before = rows[0]["freedom_from"]
    add_levels(rows, alpha=0.4)
    assert all(f"level_{a}" in rows[0] for a in AXES)
    assert rows[0]["freedom_from"] == flow_before
    assert rows[0]["level_freedom_from"] == pytest.approx(0.4 * flow_before, abs=1e-4)


# --- data_quality ----------------------------------------------------------

def test_data_quality_counts_zero_share_per_axis():
    evs = [mk("a", "2024-03-01", ff=-2), mk("b", "2024-03-02")]
    q = data_quality(evs)
    assert q["zero_share"]["freedom_from"] == 0.5
    assert q["zero_share"]["hope"] == 1.0


def test_data_quality_flags_thin_months():
    evs = [mk(f"a{i}", "2024-03-01") for i in range(3)] + [mk("b", "2024-04-01")]
    q = data_quality(evs)
    assert q["thin_months"] == ["2024-04"]
    assert q["per_month"] == {"2024-03": 3, "2024-04": 1}


def test_data_quality_on_empty_input_does_not_divide_by_zero():
    q = data_quality([])
    assert q["n_events"] == 0 and q["zero_share"]["hope"] == 0 and q["months"] == []


def test_data_quality_counts_significance_3():
    assert data_quality([mk("a", "2024-03-01", sig=3), mk("b", "2024-03-02", sig=2)])["sig3"] == 1


# --- реальный ряд ----------------------------------------------------------

def test_repository_events_file_validates_clean():
    path = aggregate.DATA_DIR / "events_backfill.jsonl"
    if not path.exists():
        pytest.skip("нет events_backfill.jsonl")
    events, errors = load_events(path)
    assert errors == []
    assert len(events) > 200


def test_repository_event_ids_are_unique():
    path = aggregate.DATA_DIR / "events_backfill.jsonl"
    if not path.exists():
        pytest.skip("нет events_backfill.jsonl")
    events, _ = load_events(path)
    ids = [e.id for e in events]
    assert len(ids) == len(set(ids))


# --- текущий месяц ---------------------------------------------------------

def test_current_month_is_not_reported_as_thin():
    """Идущий месяц не дефект покрытия: он просто не закончился."""
    evs = [mk("a", "2026-08-01"), mk("b", "2026-07-01"), mk("c", "2026-07-02"), mk("d", "2026-07-03")]
    q = data_quality(evs, now_month="2026-08")
    assert q["thin_months"] == []
    assert q["current_month"] == "2026-08"


def test_past_thin_month_is_still_reported():
    evs = [mk("a", "2026-08-01"), mk("b", "2026-05-01")]
    assert data_quality(evs, now_month="2026-08")["thin_months"] == ["2026-05"]


def test_current_month_absent_from_data_is_not_claimed():
    evs = [mk("a", "2026-07-01"), mk("b", "2026-07-02"), mk("c", "2026-07-03")]
    assert data_quality(evs, now_month="2026-08")["current_month"] == ""


def test_without_now_month_every_thin_month_is_reported():
    evs = [mk("a", "2026-08-01"), mk("b", "2026-05-01")]
    assert data_quality(evs)["thin_months"] == ["2026-05", "2026-08"]
