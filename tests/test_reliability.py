"""Тесты метрик надёжности кодирования."""
from __future__ import annotations

import pytest

from fm.aggregate import Event
from fm.reliability import compare, format_report, krippendorff_alpha


def mk(eid, ff=0, fo=0, so=0, ho=0, sig=2, escape=None, press=False) -> Event:
    return Event(
        id=eid, date="2024-03-15", month="2024-03", title="t", category="политика",
        scores={"freedom_from": ff, "freedom_for": fo, "solidarity": so, "hope": ho},
        escape=escape or [], press_pressure=press, significance=sig, rationale="r",
    )


# --- альфа Криппендорфа ----------------------------------------------------

def test_alpha_is_one_on_perfect_agreement_with_spread():
    pairs = [(-3, -3), (0, 0), (2, 2), (3, 3), (1, 1)]
    assert krippendorff_alpha(pairs, "ordinal") == pytest.approx(1.0)


def test_alpha_is_none_when_everyone_gave_the_same_value():
    """Нет разброса — разногласие невозможно; это не «идеальное согласие»."""
    assert krippendorff_alpha([(0, 0)] * 20, "ordinal") is None


def test_alpha_near_zero_on_independent_noise():
    """Систематически противоположные оценки дают альфу заметно ниже нуля."""
    pairs = [(-2, 2), (2, -2)] * 10
    assert krippendorff_alpha(pairs, "nominal") < 0


def test_alpha_ordinal_penalises_far_disagreement_more_than_near():
    near = [(0, 1), (1, 0), (2, 2), (-2, -2), (3, 3), (-3, -3)]
    far = [(0, 3), (3, 0), (2, 2), (-2, -2), (3, 3), (-3, -3)]
    assert krippendorff_alpha(near, "ordinal") > krippendorff_alpha(far, "ordinal")


def test_alpha_nominal_ignores_distance():
    """Nominal видит только «совпало / не совпало»: переименование категорий ничего не меняет."""
    near = [(0, 1), (1, 0), (0, 0), (1, 1), (0, 0)]
    far = [(0, 3), (3, 0), (0, 0), (3, 3), (0, 0)]  # то же самое, метка 1 → 3
    assert krippendorff_alpha(near, "nominal") == pytest.approx(krippendorff_alpha(far, "nominal"))


def test_alpha_ordinal_sees_intermediate_mass():
    """Ordinal учитывает, сколько наблюдений лежит между разошедшимися значениями."""
    dense_between = [(0, 2), (1, 1), (1, 1), (1, 1), (0, 0), (2, 2)]
    empty_between = [(0, 2), (0, 0), (0, 0), (0, 0), (0, 0), (2, 2)]
    assert krippendorff_alpha(dense_between, "ordinal") != pytest.approx(
        krippendorff_alpha(empty_between, "ordinal")
    )


def test_alpha_on_empty_input_is_none():
    assert krippendorff_alpha([], "ordinal") is None


def test_alpha_is_symmetric_in_coder_order():
    pairs = [(0, 1), (2, -1), (3, 3), (-2, 0), (1, 1)]
    flipped = [(b, a) for a, b in pairs]
    assert krippendorff_alpha(pairs, "ordinal") == pytest.approx(krippendorff_alpha(flipped, "ordinal"))


# --- compare ---------------------------------------------------------------

def test_compare_reports_exact_and_within1():
    a = [mk("1", ho=2), mk("2", ho=0), mk("3", ho=-3)]
    b = [mk("1", ho=2), mk("2", ho=1), mk("3", ho=0)]
    stats = compare(a, b)["axes"]["hope"]
    assert stats["exact"] == pytest.approx(1 / 3)
    assert stats["within1"] == pytest.approx(2 / 3)


def test_compare_sign_agreement_ignores_units_both_coded_zero():
    """Согласие «оба поставили 0» не должно раздувать согласие по знаку на осях с 90% нулей."""
    a = [mk(str(i)) for i in range(9)] + [mk("x", so=2)]
    b = [mk(str(i)) for i in range(9)] + [mk("x", so=-2)]
    stats = compare(a, b)["axes"]["solidarity"]
    assert stats["n_signal"] == 1
    assert stats["sign_agreement"] == 0.0
    assert stats["exact"] == pytest.approx(0.9), "сырое совпадение при этом высокое — в этом и смысл"


def test_compare_counts_tag_overlap_directionally():
    a = [mk("1", escape=["conformity"]), mk("2", escape=["conformity"]), mk("3")]
    b = [mk("1", escape=["conformity"]), mk("2"), mk("3", escape=["conformity"])]
    tag = compare(a, b)["tags"]["conformity"]
    assert (tag["both"], tag["only_a"], tag["only_b"]) == (1, 1, 1)
    assert tag["jaccard"] == pytest.approx(1 / 3)


def test_rare_tag_is_marked_unstable():
    a = [mk(str(i)) for i in range(30)]
    b = [mk(str(i)) for i in range(30)]
    a[0] = mk("0", escape=["destructiveness"])
    assert compare(a, b)["tags"]["destructiveness"]["stable"] is False


def test_compare_uses_only_common_ids_and_reports_the_rest():
    rep = compare([mk("1"), mk("2")], [mk("2"), mk("3")])
    assert rep["n_common"] == 1
    assert rep["only_in_a"] == ["1"] and rep["only_in_b"] == ["3"]


def test_compare_without_common_ids_raises():
    with pytest.raises(ValueError, match="нет общих id"):
        compare([mk("1")], [mk("2")])


def test_compare_covers_press_pressure_and_significance():
    a = [mk("1", press=True, sig=3), mk("2", sig=1)]
    b = [mk("1", press=True, sig=2), mk("2", sig=1)]
    rep = compare(a, b)
    assert rep["tags"]["press_pressure"]["both"] == 1
    assert rep["significance"]["exact"] == 0.5 and rep["significance"]["within1"] == 1.0


def test_format_report_renders_all_axes_and_tags():
    text = format_report(compare([mk("1", ho=1)], [mk("1", ho=2)]))
    for label in ("freedom_from", "freedom_for", "solidarity", "hope",
                  "significance", "conformity", "press_pressure"):
        assert label in text
